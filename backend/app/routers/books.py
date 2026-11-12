"""WP14 — the books: ERP accounting for each DCP (and for Finyl's own workspace).

/api/v1/books/...   Gated by the 'accounting' module and these permissions:
  accounting.view      see everything here
  accounting.post      journals, bills, payments, statements, assets, "post now"
  accounting.approve   approve what someone else entered (journals, bills, payments, claims)
  accounting.close     depreciation, provision, accrued interest, lock / reopen periods
  accounting.settings  chart of accounts, posting accounts, approval limits, budgets
  expenses.submit      any member of staff: their own expense claims
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_module, require_permission, write_audit
from app.core.money import D, money
from app.models import (AccAsset, AccAssetCategory, AccAttachment, AccBankLine, AccBankStatement, AccBill,
                        AccBillLine, AccBudget, AccDepreciationLine, AccDepreciationRun, AccExpenseClaim,
                        AccExpenseClaimLine, AccJournal, AccJournalLine, AccPaymentOut, AccSettings, AccSupplier,
                        Branch, ChartOfAccount, Loan, User)
from app.services import acc_ops as ops
from app.services import books_sources as src
from app.services import ledger as L
from app.services import payables as pay
from app.services import storage

router = APIRouter(prefix="/api/v1/books", tags=["books"])
MODULE = require_module("accounting")


def VIEW(): return require_permission("accounting.view")          # noqa: E704
def POST(): return require_permission("accounting.post")          # noqa: E704
def APPROVE(): return require_permission("accounting.approve")    # noqa: E704
def CLOSE(): return require_permission("accounting.close")        # noqa: E704
def SETTINGS(): return require_permission("accounting.settings")  # noqa: E704


def _can(db, user, key) -> bool:
    if user.role == "super_admin":
        return True
    from app.services.authz import effective_permissions
    return key in effective_permissions(db, user.tenant_id, user.role)


def _d(value, field="date") -> date:
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        raise HTTPException(422, f"{field} must be a date (YYYY-MM-DD)")


def _range(from_: str | None, to: str | None, settings: AccSettings) -> tuple[date, date]:
    dto = _d(to, "to") if to else L.today()
    dfrom = _d(from_, "from") if from_ else L.fy_start(dto, settings.fiscal_year_start_month)
    if dfrom > dto:
        raise HTTPException(422, "'from' must not be after 'to'")
    return dfrom, dto


def _fresh(db, tenant_id):
    """Bring the books up to date before showing them (cheap when nothing is waiting)."""
    s = L.ensure_books(db, tenant_id)
    if s.auto_post and (s.last_auto_post_at is None
                        or s.last_auto_post_at < datetime.utcnow() - timedelta(seconds=60)):
        try:
            src.sync(db, tenant_id)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    return L.ensure_books(db, tenant_id)


def _n(v):
    return float(money(v or 0))


def _names(db, ids, model=User, attr="full_name"):
    ids = {i for i in ids if i}
    return {r.id: getattr(r, attr) for r in db.query(model).filter(model.id.in_(ids or {-1}))}


# =========================================================================== overview
@router.get("/overview")
def overview(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    s = _fresh(db, tenant_id)
    b = L.Books(db, tenant_id, s)
    today = L.today()
    month_start = today.replace(day=1)
    bal = L.movements(db, tenant_id, None, today)

    def balance(acct):
        dr, cr = bal.get(acct.id, (0, 0))
        return L.natural(acct, dr, cr)
    cash = [{"account_id": a.id, "code": a.code, "name": a.name, "balance": _n(balance(a))}
            for a in sorted(b.accounts.values(), key=lambda a: a.code) if a.is_cash and a.active]
    pl_month = L.profit_and_loss(db, tenant_id, month_start, today)
    pl_year = L.profit_and_loss(db, tenant_id, L.fy_start(today, s.fiscal_year_start_month), today)
    pend = {
        "journals": db.query(func.count(AccJournal.id)).filter(AccJournal.tenant_id == tenant_id, AccJournal.status == "pending").scalar(),
        "bills": db.query(func.count(AccBill.id)).filter(AccBill.tenant_id == tenant_id, AccBill.status == "pending").scalar(),
        "payments": db.query(func.count(AccPaymentOut.id)).filter(AccPaymentOut.tenant_id == tenant_id, AccPaymentOut.status == "pending").scalar(),
        "claims": db.query(func.count(AccExpenseClaim.id)).filter(AccExpenseClaim.tenant_id == tenant_id, AccExpenseClaim.status == "submitted").scalar(),
    }
    unpaid = db.query(func.coalesce(func.sum(AccBill.total - AccBill.paid), 0)).filter(
        AccBill.tenant_id == tenant_id, AccBill.status.in_(("approved", "part_paid"))).scalar()
    overdue = db.query(func.coalesce(func.sum(AccBill.total - AccBill.paid), 0)).filter(
        AccBill.tenant_id == tenant_id, AccBill.status.in_(("approved", "part_paid")), AccBill.due_date < today).scalar()
    trend = []
    m = date(today.year - (today.month <= 5), (today.month - 6) % 12 + 1, 1)
    for _ in range(6):
        pl = L.profit_and_loss(db, tenant_id, m, L.month_end(m))
        trend.append({"month": m.strftime("%b %Y"), "income": pl["total_income"], "expenses": pl["total_expenses"],
                      "profit": pl["net_profit"]})
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return {
        "today": today.isoformat(), "lock_date": s.lock_date.isoformat() if s.lock_date else None,
        "books_start": s.books_start.isoformat() if s.books_start else None, "auto_post": s.auto_post,
        "last_auto_post_at": s.last_auto_post_at.isoformat() if s.last_auto_post_at else None,
        "waiting_to_post": src.pending_counts(db, tenant_id), "internal": L.is_internal(db, tenant_id),
        "cash": cash, "cash_total": round(sum(c["balance"] for c in cash), 2),
        "loan_book": _n(balance(b.account("loan_book"))), "provision": _n(-balance(b.account("provision"))),
        "payables": _n(unpaid), "payables_overdue": _n(overdue),
        "month": {"income": pl_month["total_income"], "expenses": pl_month["total_expenses"], "profit": pl_month["net_profit"]},
        "year": {"income": pl_year["total_income"], "expenses": pl_year["total_expenses"], "profit": pl_year["net_profit"]},
        "pending": {k: int(v or 0) for k, v in pend.items()}, "trend": trend,
    }


@router.get("/branches")
def branches(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
             _: User = Depends(require_permission("accounting.view", "expenses.submit", mode="any"))):
    """Branch names for the forms (bills, claims, budgets, assets)."""
    return {"branches": [{"id": b.id, "name": b.name}
                         for b in db.query(Branch).filter(Branch.tenant_id == tenant_id).order_by(Branch.name)]}


@router.post("/sync")
def sync_now(request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
             user: User = Depends(POST())):
    out = src.sync(db, tenant_id, limit=2000, force=True)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.sync", entity_type="books",
                details={k: v for k, v in out.items() if k != "errors"}, request=request)
    db.commit()
    return out


# =========================================================================== accounts & settings
class AccountIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=2, max_length=120)
    type: str
    subtype: str | None = None
    is_cash: bool = False
    description: str | None = None


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    subtype: str | None = None
    is_cash: bool | None = None
    active: bool | None = None
    description: str | None = None


@router.get("/accounts")
def accounts(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    s = _fresh(db, tenant_id)
    b = L.Books(db, tenant_id, s)
    mv = L.movements(db, tenant_id, None, L.today())
    used = {aid for (aid,) in db.query(AccJournalLine.account_id).filter(AccJournalLine.tenant_id == tenant_id).distinct()}
    role_of = {}
    for role in b.roles:
        role_of.setdefault(b.code(role), []).append(role)
    rows = []
    for a in sorted(b.accounts.values(), key=lambda a: a.code):
        dr, cr = mv.get(a.id, (0, 0))
        rows.append({"id": a.id, "code": a.code, "name": a.name, "type": a.type, "subtype": a.subtype,
                     "subtype_label": L.SUBTYPES.get(a.subtype or "", ("", a.subtype or ""))[1],
                     "is_cash": bool(a.is_cash), "is_system": bool(a.is_system), "active": bool(a.active),
                     "description": a.description, "balance": _n(L.natural(a, dr, cr)), "used": a.id in used,
                     "roles": role_of.get(a.code, [])})
    return {"accounts": rows, "types": list(L.ACCOUNT_TYPES),
            "subtypes": [{"key": k, "type": v[0], "label": v[1]} for k, v in L.SUBTYPES.items()],
            "roles": [{"key": k, "type": v[1], "label": v[2], "default": v[0], "code": b.code(k)}
                      for k, v in b.roles.items()]}


@router.post("/accounts")
def create_account(body: AccountIn, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(SETTINGS())):
    L.ensure_books(db, tenant_id)
    if body.type not in L.ACCOUNT_TYPES:
        raise HTTPException(422, f"type must be one of {', '.join(L.ACCOUNT_TYPES)}")
    sub = body.subtype or L.DEFAULT_SUBTYPE[body.type]
    if L.SUBTYPES.get(sub, (None,))[0] != body.type:
        raise HTTPException(422, "That group does not belong to this type of account")
    code = body.code.strip()
    if db.query(ChartOfAccount.id).filter(ChartOfAccount.tenant_id == tenant_id, ChartOfAccount.code == code).first():
        raise HTTPException(409, f"Account code {code} is already used")
    if body.is_cash and body.type != "asset":
        raise HTTPException(422, "Only an asset account can hold money")
    a = ChartOfAccount(tenant_id=tenant_id, code=code, name=body.name.strip(), type=body.type, subtype=sub,
                       is_cash=body.is_cash or sub == "cash", is_system=False, active=True, description=body.description)
    db.add(a)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="books.account_created", entity_type="account",
                entity_id=a.id, details={"code": code, "name": a.name, "type": a.type}, request=request)
    db.commit()
    return {"id": a.id}


@router.put("/accounts/{account_id}")
def update_account(account_id: int, body: AccountUpdate, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(SETTINGS())):
    a = db.get(ChartOfAccount, account_id)
    if a is None or a.tenant_id != tenant_id:
        raise HTTPException(404, "Account not found")
    b = L.Books(db, tenant_id)
    changes = body.model_dump(exclude_unset=True)
    if "subtype" in changes and changes["subtype"]:
        if L.SUBTYPES.get(changes["subtype"], (None,))[0] != a.type:
            raise HTTPException(422, "That group does not belong to this type of account")
    if changes.get("active") is False:
        if any(b.code(r) == a.code for r in b.roles):
            raise HTTPException(409, "The system posts to this account: point that posting role at another "
                                     "account first (Settings -> Posting accounts)")
        if L.account_balance(db, tenant_id, a.id) != 0:
            raise HTTPException(409, "This account still has a balance: move it to another account first")
    if changes.get("is_cash") and a.type != "asset":
        raise HTTPException(422, "Only an asset account can hold money")
    for k, v in changes.items():
        setattr(a, k, v)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.account_updated", entity_type="account",
                entity_id=a.id, details=changes, request=request)
    db.commit()
    return {"status": "saved"}


def _settings_dict(db, s: AccSettings, b: L.Books) -> dict:
    return {"fiscal_year_start_month": s.fiscal_year_start_month,
            "books_start": s.books_start.isoformat() if s.books_start else None,
            "lock_date": s.lock_date.isoformat() if s.lock_date else None, "auto_post": s.auto_post,
            "journal_approval_limit": _n(s.journal_approval_limit), "payment_approval_limit": _n(s.payment_approval_limit),
            "posting_map": {r: b.code(r) for r in b.roles}}


class SettingsIn(BaseModel):
    fiscal_year_start_month: int | None = Field(default=None, ge=1, le=12)
    books_start: str | None = None
    auto_post: bool | None = None
    journal_approval_limit: float | None = Field(default=None, ge=0)
    payment_approval_limit: float | None = Field(default=None, ge=0)
    posting_map: dict[str, str] | None = None


@router.get("/settings")
def get_settings(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    s = L.ensure_books(db, tenant_id)
    db.commit()
    return _settings_dict(db, s, L.Books(db, tenant_id, s))


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, tenant_id: int = Depends(MODULE),
                 db: Session = Depends(get_db), user: User = Depends(SETTINGS())):
    s = L.ensure_books(db, tenant_id, lock=True)
    b = L.Books(db, tenant_id, s)
    changes = body.model_dump(exclude_unset=True)
    if "posting_map" in changes and changes["posting_map"] is not None:
        new = dict(s.posting_map or {})
        for role, code in changes["posting_map"].items():
            if role not in b.roles:
                raise HTTPException(422, f"Unknown posting role '{role}'")
            a = b.accounts.get(str(code))
            if a is None or not a.active:
                raise HTTPException(422, f"Account {code} does not exist or is switched off")
            if a.type != b.roles[role][1]:
                raise HTTPException(422, f"'{b.roles[role][2]}' needs a {b.roles[role][1]} account ({code} is {a.type})")
            if role in ("bank", "mpesa_collections", "mpesa_disbursement", "cash", "platform_payment_account") and not a.is_cash:
                raise HTTPException(422, f"'{b.roles[role][2]}' needs a bank, M-Pesa or cash account")
            new[role] = a.code
        s.posting_map = new
    if "books_start" in changes:
        s.books_start = _d(changes["books_start"], "books_start") if changes["books_start"] else None
    for k in ("fiscal_year_start_month", "auto_post"):
        if changes.get(k) is not None:
            setattr(s, k, changes[k])
    for k in ("journal_approval_limit", "payment_approval_limit"):
        if changes.get(k) is not None:
            setattr(s, k, money(changes[k]))
    s.updated_by_user_id, s.updated_at = user.id, datetime.utcnow()
    write_audit(db, tenant_id=tenant_id, user=user, action="books.settings_updated", entity_type="books",
                details=changes, request=request)
    db.commit()
    return _settings_dict(db, s, L.Books(db, tenant_id, s))


# =========================================================================== journals
class JLine(BaseModel):
    account_id: int
    debit: float = 0
    credit: float = 0
    description: str | None = None
    branch_id: int | None = None


class JournalIn(BaseModel):
    date: str
    description: str = Field(min_length=3, max_length=300)
    reference: str | None = None
    branch_id: int | None = None
    lines: list[JLine] = Field(min_length=2)


class Reason(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


class ReverseIn(BaseModel):
    date: str | None = None
    reason: str = Field(min_length=5, max_length=500)


def _branch_ok(db, tenant_id, branch_id):
    if branch_id is None:
        return None
    br = db.get(Branch, branch_id)
    if br is None or br.tenant_id != tenant_id:
        raise HTTPException(422, "Branch not found")
    return branch_id


@router.get("/journals")
def list_journals(status: str | None = None, kind: str | None = None, source_type: str | None = None,
                  from_: str | None = Query(None, alias="from"), to: str | None = None, q: str | None = None,
                  account_id: int | None = None, limit: int = Query(100, le=500), offset: int = 0,
                  tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    _fresh(db, tenant_id)
    qry = db.query(AccJournal).filter(AccJournal.tenant_id == tenant_id)
    if status:
        qry = qry.filter(AccJournal.status == status)
    if kind:
        qry = qry.filter(AccJournal.kind == kind)
    if source_type:
        qry = qry.filter(AccJournal.source_type == source_type)
    if from_:
        qry = qry.filter(AccJournal.entry_date >= _d(from_, "from"))
    if to:
        qry = qry.filter(AccJournal.entry_date <= _d(to, "to"))
    if q:
        like = f"%{q.strip()}%"
        qry = qry.filter(or_(AccJournal.number.ilike(like), AccJournal.description.ilike(like),
                             AccJournal.reference.ilike(like)))
    if account_id:
        qry = qry.filter(AccJournal.id.in_(db.query(AccJournalLine.journal_id).filter(AccJournalLine.account_id == account_id)))
    total = qry.count()
    rows = qry.order_by(AccJournal.entry_date.desc(), AccJournal.id.desc()).offset(offset).limit(limit).all()
    people = _names(db, [j.created_by_user_id for j in rows] + [j.approved_by_user_id for j in rows])
    out = []
    for j in rows:
        d = L.journal_dict(db, j, with_lines=False)
        d["created_by"] = people.get(j.created_by_user_id)
        d["approved_by"] = people.get(j.approved_by_user_id)
        out.append(d)
    return {"total": total, "journals": out}


@router.get("/journals/{journal_id}")
def get_journal(journal_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                _: User = Depends(VIEW())):
    j = db.get(AccJournal, journal_id)
    if j is None or j.tenant_id != tenant_id:
        raise HTTPException(404, "Journal not found")
    d = L.journal_dict(db, j)
    people = _names(db, [j.created_by_user_id, j.approved_by_user_id])
    d["created_by"], d["approved_by"] = people.get(j.created_by_user_id), people.get(j.approved_by_user_id)
    d["attachments"] = _attachments(db, tenant_id, "journal", j.id)
    return d


@router.post("/journals")
def create_journal(body: JournalIn, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(POST())):
    s = L.ensure_books(db, tenant_id)
    on = _d(body.date)
    branch_id = _branch_ok(db, tenant_id, body.branch_id)
    for ln in body.lines:
        _branch_ok(db, tenant_id, ln.branch_id)
        if ln.debit < 0 or ln.credit < 0 or (ln.debit and ln.credit):
            raise HTTPException(422, "Each line is either a debit or a credit (one positive amount)")
    lines = [L.Line(account_id=ln.account_id, debit=money(ln.debit), credit=money(ln.credit),
                    description=ln.description, branch_id=ln.branch_id) for ln in body.lines]
    total = money(sum(ln.debit for ln in body.lines))
    status = "posted" if total <= D(s.journal_approval_limit or 0) else "pending"
    j = L.post_journal(db, tenant_id, entry_date=on, description=body.description, lines=lines, kind="manual",
                       user=user, status=status, branch_id=branch_id, reference=body.reference, settings=s)
    if status == "posted":
        j.approved_by_user_id = user.id
    write_audit(db, tenant_id=tenant_id, user=user, action="books.journal_created", entity_type="journal",
                entity_id=j.id, details={"number": j.number, "total": float(j.total), "status": status}, request=request)
    db.commit()
    return {"id": j.id, "number": j.number, "status": j.status}


def _journal_for(db, tenant_id, journal_id) -> AccJournal:
    j = db.query(AccJournal).filter(AccJournal.id == journal_id, AccJournal.tenant_id == tenant_id).with_for_update().first()
    if j is None:
        raise HTTPException(404, "Journal not found")
    return j


@router.post("/journals/{journal_id}/approve")
def approve_journal(journal_id: int, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(APPROVE())):
    j = _journal_for(db, tenant_id, journal_id)
    if j.status != "pending":
        raise HTTPException(409, f"This journal is {j.status}, not waiting for approval")
    if pay._same_person(user, j.created_by_user_id):
        raise HTTPException(403, "Someone other than the person who entered the journal must approve it")
    L.check_open(L.ensure_books(db, tenant_id), j.entry_date)
    j.status, j.approved_by_user_id, j.posted_at = "posted", user.id, datetime.utcnow()
    write_audit(db, tenant_id=tenant_id, user=user, action="books.journal_approved", entity_type="journal",
                entity_id=j.id, details={"number": j.number}, request=request)
    db.commit()
    return {"status": "posted"}


@router.post("/journals/{journal_id}/reject")
def reject_journal(journal_id: int, body: Reason, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(APPROVE())):
    j = _journal_for(db, tenant_id, journal_id)
    if j.status != "pending":
        raise HTTPException(409, f"This journal is {j.status}, not waiting for approval")
    j.status, j.reject_reason, j.approved_by_user_id = "rejected", body.reason, user.id
    write_audit(db, tenant_id=tenant_id, user=user, action="books.journal_rejected", entity_type="journal",
                entity_id=j.id, details={"number": j.number, "reason": body.reason}, request=request)
    db.commit()
    return {"status": "rejected"}


@router.post("/journals/{journal_id}/reverse")
def reverse(journal_id: int, body: ReverseIn, request: Request, tenant_id: int = Depends(MODULE),
            db: Session = Depends(get_db), user: User = Depends(APPROVE())):
    j = _journal_for(db, tenant_id, journal_id)
    if not (j.kind == "manual" or j.source_type in ("provision", "bank_line")):
        raise HTTPException(409, "This journal came from another screen (a loan, payment, bill, payroll or asset): "
                                 "correct it there, so the two always agree")
    r = L.reverse_journal(db, j, on=_d(body.date) if body.date else L.today(), user=user, reason=body.reason)
    if j.source_type == "bank_line":
        bl = db.get(AccBankLine, j.source_id)
        if bl is not None:
            ops.unmatch_line(db, tenant_id, bl.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.journal_reversed", entity_type="journal",
                entity_id=j.id, details={"number": j.number, "reversal": r.number, "reason": body.reason}, request=request)
    db.commit()
    return {"status": "reversed", "reversal_id": r.id, "reversal_number": r.number}


@router.get("/export/ledger.csv")
def export_ledger(from_: str | None = Query(None, alias="from"), to: str | None = None,
                  tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                  _: User = Depends(require_permission("accounting.view", "accounting.export", mode="any"))):
    s = _fresh(db, tenant_id)
    dfrom, dto = _range(from_, to, s)
    accts = {a.id: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == tenant_id)}
    branches = {b.id: b.name for b in db.query(Branch).filter(Branch.tenant_id == tenant_id)}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Journal", "Account Code", "Account Name", "Description", "Debit", "Credit",
                "Reference", "Branch", "Loan ID", "Source"])
    rows = (db.query(AccJournalLine, AccJournal).join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
            .filter(AccJournalLine.tenant_id == tenant_id, AccJournal.status.in_(L.COUNTED),
                    AccJournal.entry_date >= dfrom, AccJournal.entry_date <= dto)
            .order_by(AccJournal.entry_date, AccJournal.id, AccJournalLine.id))
    for ln, j in rows.yield_per(1000):
        a = accts[ln.account_id]
        w.writerow([j.entry_date.isoformat(), j.number, a.code, a.name, ln.description or j.description,
                    f"{D(ln.debit):.2f}", f"{D(ln.credit):.2f}", j.reference or "",
                    branches.get(ln.branch_id, ""), ln.loan_id or "", j.source_type or j.kind])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="ledger_{dfrom}_{dto}.csv"'})


# =========================================================================== reports
@router.get("/reports/trial-balance")
def r_tb(from_: str | None = Query(None, alias="from"), to: str | None = None, branch_id: int | None = None,
         tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    s = _fresh(db, tenant_id)
    dfrom, dto = _range(from_, to, s)
    return L.trial_balance(db, tenant_id, dfrom, dto, branch_id)


@router.get("/reports/profit-loss")
def r_pl(from_: str | None = Query(None, alias="from"), to: str | None = None, branch_id: int | None = None,
         compare: bool = False, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
         _: User = Depends(VIEW())):
    s = _fresh(db, tenant_id)
    dfrom, dto = _range(from_, to, s)
    out = L.profit_and_loss(db, tenant_id, dfrom, dto, branch_id)
    if compare:
        days = (dto - dfrom).days + 1
        pto = dfrom - timedelta(days=1)
        out["previous"] = L.profit_and_loss(db, tenant_id, pto - timedelta(days=days - 1), pto, branch_id)
    return out


@router.get("/reports/balance-sheet")
def r_bs(as_at: str | None = None, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
         _: User = Depends(VIEW())):
    s = _fresh(db, tenant_id)
    return L.balance_sheet(db, tenant_id, _d(as_at, "as_at") if as_at else L.today(), s.fiscal_year_start_month)


@router.get("/reports/cash-flow")
def r_cf(from_: str | None = Query(None, alias="from"), to: str | None = None,
         tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    s = _fresh(db, tenant_id)
    dfrom, dto = _range(from_, to, s)
    return L.cash_flow(db, tenant_id, dfrom, dto)


@router.get("/reports/ledger/{account_id}")
def r_ledger(account_id: int, from_: str | None = Query(None, alias="from"), to: str | None = None,
             tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    s = _fresh(db, tenant_id)
    dfrom, dto = _range(from_, to, s)
    return L.account_ledger(db, tenant_id, account_id, dfrom, dto)


@router.get("/reports/aged-payables")
def r_aged(as_at: str | None = None, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
           _: User = Depends(VIEW())):
    return pay.aged_payables(db, tenant_id, _d(as_at, "as_at") if as_at else L.today())


@router.get("/reports/loan/{loan_id}")
def r_loan(loan_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
           _: User = Depends(VIEW())):
    loan = db.get(Loan, loan_id)
    if loan is None or loan.tenant_id != tenant_id:
        raise HTTPException(404, "Loan not found")
    _fresh(db, tenant_id)
    accts = {a.id: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == tenant_id)}
    rows = (db.query(AccJournalLine, AccJournal).join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
            .filter(AccJournalLine.loan_id == loan.id, AccJournal.status.in_(L.COUNTED))
            .order_by(AccJournal.entry_date, AccJournal.id, AccJournalLine.id).all())
    return {"loan": {"id": loan.id, "account_number": loan.account_number},
            "lines": [{"date": j.entry_date.isoformat(), "number": j.number, "journal_id": j.id,
                       "account": f"{accts[ln.account_id].code} {accts[ln.account_id].name}",
                       "description": ln.description or j.description, "debit": _n(ln.debit), "credit": _n(ln.credit)}
                      for ln, j in rows]}


# =========================================================================== suppliers & bills
class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    kra_pin: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=160)
    payment_method: str | None = "bank"
    mpesa_number: str | None = Field(default=None, max_length=30)
    bank_name: str | None = Field(default=None, max_length=80)
    bank_account: str | None = Field(default=None, max_length=40)
    default_expense_account_id: int | None = None
    wht_rate: float = Field(default=0, ge=0, le=30)
    notes: str | None = None
    active: bool | None = None


def _supplier_dict(s: AccSupplier, owed=0.0):
    return {"id": s.id, "name": s.name, "kra_pin": s.kra_pin, "phone": s.phone, "email": s.email,
            "payment_method": s.payment_method, "mpesa_number": s.mpesa_number, "bank_name": s.bank_name,
            "bank_account": s.bank_account, "default_expense_account_id": s.default_expense_account_id,
            "wht_rate": float(s.wht_rate or 0), "notes": s.notes, "active": bool(s.active), "owed": owed}


@router.get("/suppliers")
def suppliers(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    _fresh(db, tenant_id)
    owed = dict(db.query(AccBill.supplier_id, func.sum(AccBill.total - AccBill.paid))
                .filter(AccBill.tenant_id == tenant_id, AccBill.status.in_(("approved", "part_paid")))
                .group_by(AccBill.supplier_id).all())
    return {"suppliers": [_supplier_dict(s, _n(owed.get(s.id))) for s in
                          db.query(AccSupplier).filter(AccSupplier.tenant_id == tenant_id).order_by(AccSupplier.name)]}


@router.post("/suppliers")
def create_supplier(body: SupplierIn, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(POST())):
    if db.query(AccSupplier.id).filter(AccSupplier.tenant_id == tenant_id,
                                       func.lower(AccSupplier.name) == body.name.strip().lower()).first():
        raise HTTPException(409, "A supplier with this name already exists")
    if body.default_expense_account_id:
        pay.account_of(db, tenant_id, body.default_expense_account_id, types=("expense", "asset"))
    data = body.model_dump(exclude={"active"})
    data["name"] = data["name"].strip()
    s = AccSupplier(tenant_id=tenant_id, active=True, **data)
    db.add(s)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="books.supplier_created", entity_type="supplier",
                entity_id=s.id, details={"name": s.name}, request=request)
    db.commit()
    return {"id": s.id}


@router.put("/suppliers/{supplier_id}")
def update_supplier(supplier_id: int, body: SupplierIn, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(POST())):
    s = db.get(AccSupplier, supplier_id)
    if s is None or s.tenant_id != tenant_id:
        raise HTTPException(404, "Supplier not found")
    if body.default_expense_account_id:
        pay.account_of(db, tenant_id, body.default_expense_account_id, types=("expense", "asset"))
    data = body.model_dump(exclude_unset=True)
    if data.get("active") is None:
        data.pop("active", None)
    for k, v in data.items():
        setattr(s, k, v.strip() if k == "name" else v)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.supplier_updated", entity_type="supplier",
                entity_id=s.id, details=data, request=request)
    db.commit()
    return {"status": "saved"}


class BillLineIn(BaseModel):
    account_id: int
    description: str | None = None
    amount: float = Field(gt=0)
    vat_rate: float = Field(default=0, ge=0, le=100)
    branch_id: int | None = None


class BillIn(BaseModel):
    supplier_id: int
    bill_date: str
    due_date: str | None = None
    supplier_ref: str | None = None
    description: str | None = None
    branch_id: int | None = None
    wht_rate: float | None = Field(default=None, ge=0, le=30)
    lines: list[BillLineIn] = Field(min_length=1)


def _bill_dict(db, bill: AccBill, sup_names=None, detail=False):
    d = {"id": bill.id, "number": bill.number, "supplier_id": bill.supplier_id,
         "supplier": (sup_names or {}).get(bill.supplier_id) or getattr(db.get(AccSupplier, bill.supplier_id), "name", ""),
         "supplier_ref": bill.supplier_ref, "bill_date": bill.bill_date.isoformat(),
         "due_date": bill.due_date.isoformat() if bill.due_date else None, "description": bill.description,
         "subtotal": _n(bill.subtotal), "vat": _n(bill.vat), "wht": _n(bill.wht), "total": _n(bill.total),
         "paid": _n(bill.paid), "balance": _n(D(bill.total) - D(bill.paid)), "status": bill.status,
         "journal_id": bill.journal_id, "source_type": bill.source_type, "branch_id": bill.branch_id,
         "overdue": bool(bill.due_date and bill.due_date < L.today() and bill.status in ("approved", "part_paid")),
         "void_reason": bill.void_reason}
    if detail:
        accts = {a.id: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == bill.tenant_id)}
        d["lines"] = [{"account_id": ln.account_id, "account": f"{accts[ln.account_id].code} {accts[ln.account_id].name}",
                       "description": ln.description, "amount": _n(ln.amount), "vat_rate": float(ln.vat_rate),
                       "vat": _n(ln.vat), "branch_id": ln.branch_id}
                      for ln in db.query(AccBillLine).filter(AccBillLine.bill_id == bill.id).order_by(AccBillLine.id)]
        d["payments"] = [_payment_dict(p) for p in db.query(AccPaymentOut).filter(AccPaymentOut.bill_id == bill.id)
                         .order_by(AccPaymentOut.id)]
        d["outstanding"] = _n(pay.outstanding(db, bill))
        people = _names(db, [bill.created_by_user_id, bill.approved_by_user_id])
        d["created_by"], d["approved_by"] = people.get(bill.created_by_user_id), people.get(bill.approved_by_user_id)
        d["created_by_user_id"] = bill.created_by_user_id
        d["attachments"] = _attachments(db, bill.tenant_id, "bill", bill.id)
    return d


@router.get("/bills")
def bills(status: str | None = None, supplier_id: int | None = None, tenant_id: int = Depends(MODULE),
          db: Session = Depends(get_db), _: User = Depends(VIEW())):
    _fresh(db, tenant_id)
    q = db.query(AccBill).filter(AccBill.tenant_id == tenant_id)
    if status == "unpaid":
        q = q.filter(AccBill.status.in_(("approved", "part_paid")))
    elif status:
        q = q.filter(AccBill.status == status)
    if supplier_id:
        q = q.filter(AccBill.supplier_id == supplier_id)
    names = {s.id: s.name for s in db.query(AccSupplier).filter(AccSupplier.tenant_id == tenant_id)}
    return {"bills": [_bill_dict(db, b, names) for b in q.order_by(AccBill.bill_date.desc(), AccBill.id.desc()).limit(500)]}


@router.get("/bills/{bill_id}")
def bill(bill_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    b = db.get(AccBill, bill_id)
    if b is None or b.tenant_id != tenant_id:
        raise HTTPException(404, "Bill not found")
    return _bill_dict(db, b, detail=True)


@router.post("/bills")
def create_bill(body: BillIn, request: Request, tenant_id: int = Depends(MODULE),
                db: Session = Depends(get_db), user: User = Depends(POST())):
    sup = db.get(AccSupplier, body.supplier_id)
    if sup is None or sup.tenant_id != tenant_id:
        raise HTTPException(404, "Supplier not found")
    _branch_ok(db, tenant_id, body.branch_id)
    for ln in body.lines:
        _branch_ok(db, tenant_id, ln.branch_id)
    b = pay.create_bill(db, tenant_id, user=user, supplier=sup, bill_date=_d(body.bill_date, "bill_date"),
                        due_date=_d(body.due_date, "due_date") if body.due_date else None,
                        supplier_ref=body.supplier_ref, description=body.description, branch_id=body.branch_id,
                        lines=[ln.model_dump() for ln in body.lines], wht_rate=body.wht_rate)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.bill_created", entity_type="bill", entity_id=b.id,
                details={"number": b.number, "total": float(b.total), "status": b.status}, request=request)
    db.commit()
    return {"id": b.id, "number": b.number, "status": b.status}


def _bill_for(db, tenant_id, bill_id):
    b = db.query(AccBill).filter(AccBill.id == bill_id, AccBill.tenant_id == tenant_id).with_for_update().first()
    if b is None:
        raise HTTPException(404, "Bill not found")
    return b


@router.post("/bills/{bill_id}/approve")
def approve_bill(bill_id: int, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                 user: User = Depends(APPROVE())):
    b = _bill_for(db, tenant_id, bill_id)
    pay.approve_bill(db, b, user)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.bill_approved", entity_type="bill", entity_id=b.id,
                details={"number": b.number}, request=request)
    db.commit()
    return {"status": b.status}


@router.post("/bills/{bill_id}/reject")
def reject_bill(bill_id: int, body: Reason, request: Request, tenant_id: int = Depends(MODULE),
                db: Session = Depends(get_db), user: User = Depends(APPROVE())):
    b = _bill_for(db, tenant_id, bill_id)
    pay.reject_bill(db, b, user, body.reason)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.bill_rejected", entity_type="bill", entity_id=b.id,
                details={"number": b.number, "reason": body.reason}, request=request)
    db.commit()
    return {"status": b.status}


@router.post("/bills/{bill_id}/void")
def void_bill(bill_id: int, body: Reason, request: Request, tenant_id: int = Depends(MODULE),
              db: Session = Depends(get_db), user: User = Depends(APPROVE())):
    b = _bill_for(db, tenant_id, bill_id)
    if b.source_type == "finyl_invoice":
        raise HTTPException(409, "This bill is Finyl's invoice: ask Finyl to void the invoice and it will follow")
    pay.void_bill(db, b, user, body.reason)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.bill_voided", entity_type="bill", entity_id=b.id,
                details={"number": b.number, "reason": body.reason}, request=request)
    db.commit()
    return {"status": b.status}


# =========================================================================== expense claims
class ClaimLineIn(BaseModel):
    account_id: int
    description: str | None = None
    amount: float = Field(gt=0)
    receipt_ref: str | None = None


class ClaimIn(BaseModel):
    claim_date: str
    description: str = Field(min_length=3, max_length=300)
    branch_id: int | None = None
    lines: list[ClaimLineIn] = Field(min_length=1)


def _claim_dict(db, c: AccExpenseClaim, people=None, detail=False):
    people = people or _names(db, [c.claimant_user_id, c.approved_by_user_id])
    d = {"id": c.id, "number": c.number, "claimant_user_id": c.claimant_user_id,
         "claimant": people.get(c.claimant_user_id), "claim_date": c.claim_date.isoformat(),
         "description": c.description, "total": _n(c.total), "paid": _n(c.paid), "status": c.status,
         "approved_by": people.get(c.approved_by_user_id), "reject_reason": c.reject_reason,
         "journal_id": c.journal_id, "branch_id": c.branch_id}
    if detail:
        accts = {a.id: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == c.tenant_id)}
        d["lines"] = [{"account_id": ln.account_id, "account": f"{accts[ln.account_id].code} {accts[ln.account_id].name}",
                       "description": ln.description, "amount": _n(ln.amount), "receipt_ref": ln.receipt_ref}
                      for ln in db.query(AccExpenseClaimLine).filter(AccExpenseClaimLine.claim_id == c.id)]
        d["payments"] = [_payment_dict(p) for p in db.query(AccPaymentOut).filter(AccPaymentOut.claim_id == c.id)]
        d["outstanding"] = _n(pay.claim_outstanding(db, c)) if c.status == "approved" else 0
        d["attachments"] = _attachments(db, c.tenant_id, "claim", c.id)
    return d


@router.get("/expense-accounts")
def expense_accounts(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                     _: User = Depends(require_permission("expenses.submit"))):
    """The expense accounts staff may claim against (for the claim form)."""
    L.ensure_books(db, tenant_id)
    db.commit()
    return {"accounts": [{"id": a.id, "code": a.code, "name": a.name}
                         for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == tenant_id,
                                                                  ChartOfAccount.type == "expense",
                                                                  ChartOfAccount.active.is_(True),
                                                                  ChartOfAccount.subtype.in_(("operating", "staff_cost")))
                         .order_by(ChartOfAccount.code)]}


@router.get("/claims")
def claims(status: str | None = None, mine: bool = False, tenant_id: int = Depends(MODULE),
           db: Session = Depends(get_db), user: User = Depends(require_permission("expenses.submit"))):
    q = db.query(AccExpenseClaim).filter(AccExpenseClaim.tenant_id == tenant_id)
    if mine or not _can(db, user, "accounting.view"):
        q = q.filter(AccExpenseClaim.claimant_user_id == user.id)
    if status:
        q = q.filter(AccExpenseClaim.status == status)
    rows = q.order_by(AccExpenseClaim.id.desc()).limit(500).all()
    people = _names(db, [c.claimant_user_id for c in rows] + [c.approved_by_user_id for c in rows])
    return {"claims": [_claim_dict(db, c, people) for c in rows],
            "can_approve": _can(db, user, "accounting.approve"), "can_view_all": _can(db, user, "accounting.view")}


def _claim_for(db, tenant_id, claim_id, user, lock=False):
    q = db.query(AccExpenseClaim).filter(AccExpenseClaim.id == claim_id, AccExpenseClaim.tenant_id == tenant_id)
    c = (q.with_for_update() if lock else q).first()
    if c is None or (c.claimant_user_id != user.id and not _can(db, user, "accounting.view")):
        raise HTTPException(404, "Claim not found")
    return c


@router.get("/claims/{claim_id}")
def claim(claim_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
          user: User = Depends(require_permission("expenses.submit"))):
    return _claim_dict(db, _claim_for(db, tenant_id, claim_id, user), detail=True)


@router.post("/claims")
def create_claim(body: ClaimIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                 user: User = Depends(require_permission("expenses.submit"))):
    _branch_ok(db, tenant_id, body.branch_id)
    c = pay.create_claim(db, tenant_id, user=user, claim_date=_d(body.claim_date, "claim_date"),
                         description=body.description, branch_id=body.branch_id or user.branch_id,
                         lines=[ln.model_dump() for ln in body.lines])
    write_audit(db, tenant_id=tenant_id, user=user, action="books.claim_submitted", entity_type="claim",
                entity_id=c.id, details={"number": c.number, "total": float(c.total)}, request=request)
    db.commit()
    return {"id": c.id, "number": c.number, "status": c.status}


@router.post("/claims/{claim_id}/approve")
def approve_claim(claim_id: int, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                  user: User = Depends(APPROVE())):
    c = _claim_for(db, tenant_id, claim_id, user, lock=True)
    pay.approve_claim(db, c, user)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.claim_approved", entity_type="claim",
                entity_id=c.id, details={"number": c.number}, request=request)
    db.commit()
    return {"status": c.status}


@router.post("/claims/{claim_id}/reject")
def reject_claim(claim_id: int, body: Reason, request: Request, tenant_id: int = Depends(MODULE),
                 db: Session = Depends(get_db), user: User = Depends(APPROVE())):
    c = _claim_for(db, tenant_id, claim_id, user, lock=True)
    pay.reject_claim(db, c, user, body.reason)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.claim_rejected", entity_type="claim",
                entity_id=c.id, details={"number": c.number, "reason": body.reason}, request=request)
    db.commit()
    return {"status": c.status}


# =========================================================================== payments out
class PaymentIn(BaseModel):
    kind: str
    target_id: int
    amount: float = Field(gt=0)
    paid_on: str
    account_id: int
    method: str | None = None
    reference: str | None = Field(default=None, max_length=60)


def _payment_dict(p: AccPaymentOut):
    return {"id": p.id, "number": p.number, "kind": p.kind, "bill_id": p.bill_id, "claim_id": p.claim_id,
            "payee": p.payee, "amount": _n(p.amount), "paid_on": p.paid_on.isoformat(), "account_id": p.account_id,
            "method": p.method, "reference": p.reference, "status": p.status, "reject_reason": p.reject_reason,
            "journal_id": p.journal_id, "created_by_user_id": p.created_by_user_id}


@router.get("/payments")
def payments(status: str | None = None, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
             _: User = Depends(VIEW())):
    _fresh(db, tenant_id)
    q = db.query(AccPaymentOut).filter(AccPaymentOut.tenant_id == tenant_id)
    if status:
        q = q.filter(AccPaymentOut.status == status)
    rows = q.order_by(AccPaymentOut.id.desc()).limit(500).all()
    people = _names(db, [p.created_by_user_id for p in rows])
    out = []
    for p in rows:
        d = _payment_dict(p)
        d["created_by"] = people.get(p.created_by_user_id)
        out.append(d)
    return {"payments": out}


@router.post("/payments")
def create_payment(body: PaymentIn, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(POST())):
    p = pay.create_payment(db, tenant_id, user=user, kind=body.kind, target_id=body.target_id, amount=body.amount,
                           paid_on=_d(body.paid_on, "paid_on"), account_id=body.account_id, method=body.method,
                           reference=body.reference)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.payment_created", entity_type="payment_out",
                entity_id=p.id, details={"number": p.number, "amount": float(p.amount), "status": p.status},
                request=request)
    db.commit()
    return {"id": p.id, "number": p.number, "status": p.status}


def _payment_for(db, tenant_id, pid):
    p = db.query(AccPaymentOut).filter(AccPaymentOut.id == pid, AccPaymentOut.tenant_id == tenant_id).with_for_update().first()
    if p is None:
        raise HTTPException(404, "Payment not found")
    return p


@router.post("/payments/{payment_id}/approve")
def approve_payment(payment_id: int, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(APPROVE())):
    p = _payment_for(db, tenant_id, payment_id)
    L.check_open(L.ensure_books(db, tenant_id), p.paid_on)
    pay.approve_payment(db, p, user)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.payment_approved", entity_type="payment_out",
                entity_id=p.id, details={"number": p.number}, request=request)
    db.commit()
    return {"status": p.status}


@router.post("/payments/{payment_id}/reject")
def reject_payment(payment_id: int, body: Reason, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(APPROVE())):
    p = _payment_for(db, tenant_id, payment_id)
    pay.reject_payment(db, p, user, body.reason)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.payment_rejected", entity_type="payment_out",
                entity_id=p.id, details={"number": p.number, "reason": body.reason}, request=request)
    db.commit()
    return {"status": p.status}


# =========================================================================== attachments
ATTACH_TYPES = {"bill": AccBill, "claim": AccExpenseClaim, "journal": AccJournal, "asset": AccAsset}


def _attachments(db, tenant_id, entity_type, entity_id):
    return [{"id": a.id, "filename": a.filename, "created_at": a.created_at.isoformat() if a.created_at else None}
            for a in db.query(AccAttachment).filter(AccAttachment.tenant_id == tenant_id,
                                                    AccAttachment.entity_type == entity_type,
                                                    AccAttachment.entity_id == entity_id).order_by(AccAttachment.id)]


def _may_attach(db, user, tenant_id, entity_type, entity_id):
    model = ATTACH_TYPES.get(entity_type)
    if model is None:
        raise HTTPException(422, "Attachments are for bills, claims, journals and assets")
    obj = db.get(model, entity_id)
    if obj is None or obj.tenant_id != tenant_id:
        raise HTTPException(404, "Not found")
    if entity_type == "claim" and getattr(obj, "claimant_user_id", None) == user.id:
        return obj
    if not (_can(db, user, "accounting.post") or (entity_type == "claim" and _can(db, user, "accounting.view"))):
        raise HTTPException(403, "Missing required permission: accounting.post")
    return obj


@router.post("/attachments/{entity_type}/{entity_id}")
async def upload_attachment(entity_type: str, entity_id: int, request: Request, file: UploadFile = File(...),
                            tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    _may_attach(db, user, tenant_id, entity_type, entity_id)
    data = await file.read()
    if not data:
        raise HTTPException(422, "The file is empty")
    if len(data) > storage.MAX_BYTES:
        raise HTTPException(413, "The file is too large")
    name = storage.sanitise(file.filename or "receipt")
    if not name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp", ".xlsx", ".csv", ".docx")):
        raise HTTPException(422, "Attach a PDF, a photo (JPG/PNG/WebP) or an Excel/CSV/Word file")
    _, path = storage.save_bytes(tenant_id, "books", name, data)
    a = AccAttachment(tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id, filename=name,
                      storage_path=path, uploaded_by_user_id=user.id)
    db.add(a)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="books.attachment_added", entity_type=entity_type,
                entity_id=entity_id, details={"filename": name}, request=request)
    db.commit()
    return {"id": a.id, "filename": name}


@router.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    a = db.get(AccAttachment, attachment_id)
    if a is None or a.tenant_id != tenant_id:
        raise HTTPException(404, "Attachment not found")
    _may_attach(db, user, tenant_id, a.entity_type, a.entity_id) if a.entity_type == "claim" else None
    if a.entity_type != "claim" and not _can(db, user, "accounting.view"):
        raise HTTPException(403, "Missing required permission: accounting.view")
    try:
        data = storage.read_bytes(a.storage_path)
    except OSError:
        raise HTTPException(404, "The file is no longer stored")
    kind = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "csv": "text/csv"}.get(a.filename.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    return Response(data, media_type=kind, headers={"Content-Disposition": f'inline; filename="{a.filename}"',
                                                    "X-Content-Type-Options": "nosniff"})


# =========================================================================== reconciliation
@router.get("/reconciliation")
def recon_accounts(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    _fresh(db, tenant_id)
    out = []
    for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == tenant_id, ChartOfAccount.is_cash.is_(True),
                                             ChartOfAccount.active.is_(True)).order_by(ChartOfAccount.code):
        last = (db.query(AccBankStatement).filter(AccBankStatement.tenant_id == tenant_id,
                                                  AccBankStatement.account_id == a.id)
                .order_by(AccBankStatement.period_to.desc(), AccBankStatement.id.desc()).first())
        unmatched = db.query(func.count(AccBankLine.id)).filter(AccBankLine.tenant_id == tenant_id,
                                                                 AccBankLine.account_id == a.id,
                                                                 AccBankLine.status == "unmatched").scalar()
        out.append({"account_id": a.id, "code": a.code, "name": a.name,
                    "ledger_balance": _n(L.account_balance(db, tenant_id, a.id)),
                    "last_statement_to": last.period_to.isoformat() if last else None,
                    "last_statement_balance": _n(last.closing_balance) if last and last.closing_balance is not None else None,
                    "unmatched": int(unmatched or 0)})
    return {"accounts": out}


@router.post("/reconciliation/{account_id}/import")
async def recon_import(account_id: int, request: Request, file: UploadFile = File(...),
                       closing_balance: str | None = Form(None), layout: str | None = Form(None),
                       tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), user: User = Depends(POST())):
    data = await file.read()
    if not data:
        raise HTTPException(422, "The file is empty")
    if len(data) > storage.MAX_BYTES:
        raise HTTPException(413, "The file is too large")
    try:
        lay = json.loads(layout) if layout else None
    except ValueError:
        raise HTTPException(422, "layout must be JSON")
    _fresh(db, tenant_id)
    out = ops.import_statement(db, tenant_id, account_id=account_id, filename=file.filename or "statement",
                               data=data, closing_balance=closing_balance or None, layout=lay, user=user)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.statement_imported", entity_type="account",
                entity_id=account_id, details=out, request=request)
    db.commit()
    return out


@router.get("/reconciliation/{account_id}")
def recon_report(account_id: int, as_at: str | None = None, tenant_id: int = Depends(MODULE),
                 db: Session = Depends(get_db), _: User = Depends(VIEW())):
    _fresh(db, tenant_id)
    return ops.reconciliation(db, tenant_id, account_id, _d(as_at, "as_at") if as_at else L.today())


@router.get("/reconciliation/{account_id}/lines")
def recon_lines(account_id: int, status: str | None = None, tenant_id: int = Depends(MODULE),
                db: Session = Depends(get_db), _: User = Depends(VIEW())):
    ops.money_account(db, tenant_id, account_id)
    q = db.query(AccBankLine).filter(AccBankLine.tenant_id == tenant_id, AccBankLine.account_id == account_id)
    if status:
        q = q.filter(AccBankLine.status == status)
    return {"lines": [{"id": b.id, "date": b.txn_date.isoformat(), "description": b.description,
                       "reference": b.reference, "amount": _n(b.amount), "status": b.status,
                       "journal_line_id": b.journal_line_id, "note": b.note}
                      for b in q.order_by(AccBankLine.txn_date.desc(), AccBankLine.id.desc()).limit(1000)]}


@router.get("/reconciliation/lines/{line_id}/candidates")
def recon_candidates(line_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                     _: User = Depends(VIEW())):
    bl = db.get(AccBankLine, line_id)
    if bl is None or bl.tenant_id != tenant_id:
        raise HTTPException(404, "Line not found")
    rows = (ops._open_ledger_lines(db, tenant_id, bl.account_id, bl.txn_date - timedelta(days=30),
                                   bl.txn_date + timedelta(days=30)).all())
    rows = sorted(rows, key=lambda r: (money(D(r[0].debit) - D(r[0].credit)) != money(bl.amount),
                                       abs((r[1].entry_date - bl.txn_date).days)))[:50]
    return {"candidates": [{"line_id": ln.id, "journal_id": j.id, "number": j.number, "date": j.entry_date.isoformat(),
                            "description": ln.description or j.description, "reference": j.reference,
                            "amount": _n(D(ln.debit) - D(ln.credit)),
                            "same_amount": money(D(ln.debit) - D(ln.credit)) == money(bl.amount)} for ln, j in rows]}


class MatchIn(BaseModel):
    journal_line_id: int


class NoteIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


class LineJournalIn(BaseModel):
    account_id: int
    description: str | None = None


@router.post("/reconciliation/lines/{line_id}/match")
def recon_match(line_id: int, body: MatchIn, request: Request, tenant_id: int = Depends(MODULE),
                db: Session = Depends(get_db), user: User = Depends(POST())):
    ops.match_line(db, tenant_id, line_id, body.journal_line_id, user)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.statement_line_matched", entity_type="bank_line",
                entity_id=line_id, details={"journal_line_id": body.journal_line_id}, request=request)
    db.commit()
    return {"status": "matched"}


@router.post("/reconciliation/lines/{line_id}/unmatch")
def recon_unmatch(line_id: int, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                  user: User = Depends(POST())):
    ops.unmatch_line(db, tenant_id, line_id)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.statement_line_unmatched", entity_type="bank_line",
                entity_id=line_id, request=request)
    db.commit()
    return {"status": "unmatched"}


@router.post("/reconciliation/lines/{line_id}/ignore")
def recon_ignore(line_id: int, body: NoteIn, request: Request, tenant_id: int = Depends(MODULE),
                 db: Session = Depends(get_db), user: User = Depends(POST())):
    ops.ignore_line(db, tenant_id, line_id, body.note, user)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.statement_line_ignored", entity_type="bank_line",
                entity_id=line_id, details={"note": body.note}, request=request)
    db.commit()
    return {"status": "ignored"}


@router.post("/reconciliation/lines/{line_id}/journal")
def recon_journal(line_id: int, body: LineJournalIn, request: Request, tenant_id: int = Depends(MODULE),
                  db: Session = Depends(get_db), user: User = Depends(POST())):
    j = ops.journal_from_line(db, tenant_id, line_id, body.account_id, body.description, user)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.statement_line_booked", entity_type="bank_line",
                entity_id=line_id, details={"journal": j.number}, request=request)
    db.commit()
    return {"status": "matched", "journal_id": j.id, "number": j.number}


@router.post("/reconciliation/{account_id}/auto-match")
def recon_auto(account_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
               user: User = Depends(POST())):
    ops.money_account(db, tenant_id, account_id)
    _fresh(db, tenant_id)
    n = ops.auto_match(db, tenant_id, account_id)
    db.commit()
    return {"matched": n}


# =========================================================================== budgets
class BudgetIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    fiscal_year: int = Field(ge=2000, le=2100)
    branch_id: int | None = None
    copy_actuals: bool = False
    growth_pct: float = Field(default=0, ge=-100, le=1000)


class BudgetLinesIn(BaseModel):
    lines: list[dict]


@router.get("/budgets")
def budgets(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    rows = db.query(AccBudget).filter(AccBudget.tenant_id == tenant_id).order_by(AccBudget.fiscal_year.desc(), AccBudget.id.desc())
    branches = {b.id: b.name for b in db.query(Branch).filter(Branch.tenant_id == tenant_id)}
    return {"budgets": [{"id": b.id, "name": b.name, "fiscal_year": b.fiscal_year, "branch_id": b.branch_id,
                         "branch": branches.get(b.branch_id), "status": b.status} for b in rows]}


@router.post("/budgets")
def create_budget(body: BudgetIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                  user: User = Depends(SETTINGS())):
    s = _fresh(db, tenant_id)
    _branch_ok(db, tenant_id, body.branch_id)
    bud = AccBudget(tenant_id=tenant_id, name=body.name, fiscal_year=body.fiscal_year, branch_id=body.branch_id,
                    status="draft", created_by_user_id=user.id)
    db.add(bud)
    db.flush()
    if body.copy_actuals:
        start = date(body.fiscal_year, s.fiscal_year_start_month or 1, 1)
        ops.save_budget_lines(db, bud, ops.lines_from_actuals(db, tenant_id, start, body.growth_pct, body.branch_id))
    write_audit(db, tenant_id=tenant_id, user=user, action="books.budget_created", entity_type="budget",
                entity_id=bud.id, details=body.model_dump(), request=request)
    db.commit()
    return {"id": bud.id}


def _budget_for(db, tenant_id, bid) -> AccBudget:
    b = db.get(AccBudget, bid)
    if b is None or b.tenant_id != tenant_id:
        raise HTTPException(404, "Budget not found")
    return b


@router.get("/budgets/{budget_id}")
def budget(budget_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    return ops.budget_dict(db, _budget_for(db, tenant_id, budget_id), L.ensure_books(db, tenant_id))


@router.put("/budgets/{budget_id}/lines")
def budget_lines(budget_id: int, body: BudgetLinesIn, request: Request, tenant_id: int = Depends(MODULE),
                 db: Session = Depends(get_db), user: User = Depends(SETTINGS())):
    b = _budget_for(db, tenant_id, budget_id)
    ops.save_budget_lines(db, b, body.lines)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.budget_saved", entity_type="budget",
                entity_id=b.id, details={"lines": len(body.lines)}, request=request)
    db.commit()
    return ops.budget_dict(db, b, L.ensure_books(db, tenant_id))


@router.post("/budgets/{budget_id}/approve")
def approve_budget(budget_id: int, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(APPROVE())):
    b = _budget_for(db, tenant_id, budget_id)
    if b.status != "draft":
        raise HTTPException(409, "This budget is already approved")
    b.status, b.approved_by_user_id = "approved", user.id
    write_audit(db, tenant_id=tenant_id, user=user, action="books.budget_approved", entity_type="budget",
                entity_id=b.id, request=request)
    db.commit()
    return {"status": "approved"}


@router.get("/budgets/{budget_id}/vs-actual")
def budget_vs_actual(budget_id: int, through: str | None = None, tenant_id: int = Depends(MODULE),
                     db: Session = Depends(get_db), _: User = Depends(VIEW())):
    s = _fresh(db, tenant_id)
    return ops.budget_vs_actual(db, _budget_for(db, tenant_id, budget_id), s, _d(through, "through") if through else None)


# =========================================================================== fixed assets
class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    asset_account_id: int
    accum_account_id: int
    expense_account_id: int
    method: str = "straight_line"
    useful_life_months: int = Field(default=36, ge=0, le=600)
    rate_pct: float = Field(default=0, ge=0, le=100)


class AssetIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    category_id: int
    purchase_date: str
    in_service_date: str | None = None
    cost: float = Field(gt=0)
    residual_value: float = Field(default=0, ge=0)
    funding: str = "paid"
    paid_from_account_id: int | None = None
    accumulated_depreciation: float = Field(default=0, ge=0)
    branch_id: int | None = None
    custodian: str | None = None
    serial_no: str | None = None
    supplier_id: int | None = None
    method: str | None = None
    useful_life_months: int | None = Field(default=None, ge=1, le=600)
    rate_pct: float | None = Field(default=None, ge=0, le=100)
    notes: str | None = None


class DisposeIn(BaseModel):
    date: str
    proceeds: float = Field(default=0, ge=0)
    received_into_account_id: int | None = None
    note: str | None = None


class PeriodIn(BaseModel):
    period: str          # any day in the month (YYYY-MM-DD) or YYYY-MM


def _period(value: str) -> date:
    v = str(value)
    if len(v) == 7:
        v += "-01"
    return L.month_end(_d(v, "period"))


def _category_dict(c: AccAssetCategory, accts):
    return {"id": c.id, "name": c.name, "asset_account_id": c.asset_account_id, "accum_account_id": c.accum_account_id,
            "expense_account_id": c.expense_account_id, "method": c.method, "useful_life_months": c.useful_life_months,
            "rate_pct": float(c.rate_pct or 0),
            "asset_account": f"{accts[c.asset_account_id].code} {accts[c.asset_account_id].name}"}


@router.get("/asset-categories")
def categories(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    ops.ensure_categories(db, tenant_id)
    db.commit()
    accts = {a.id: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == tenant_id)}
    return {"categories": [_category_dict(c, accts) for c in
                           db.query(AccAssetCategory).filter(AccAssetCategory.tenant_id == tenant_id).order_by(AccAssetCategory.name)]}


def _check_category(db, tenant_id, body: CategoryIn):
    pay.account_of(db, tenant_id, body.asset_account_id, types=("asset",))
    pay.account_of(db, tenant_id, body.accum_account_id, types=("asset",))
    pay.account_of(db, tenant_id, body.expense_account_id, types=("expense",))
    if body.method not in ("straight_line", "reducing_balance"):
        raise HTTPException(422, "method must be straight_line or reducing_balance")


@router.post("/asset-categories")
def create_category(body: CategoryIn, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(SETTINGS())):
    _check_category(db, tenant_id, body)
    if db.query(AccAssetCategory.id).filter(AccAssetCategory.tenant_id == tenant_id,
                                            AccAssetCategory.name == body.name).first():
        raise HTTPException(409, "A category with this name already exists")
    c = AccAssetCategory(tenant_id=tenant_id, **body.model_dump())
    db.add(c)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="books.asset_category_created", entity_type="asset_category",
                entity_id=c.id, details=body.model_dump(), request=request)
    db.commit()
    return {"id": c.id}


@router.put("/asset-categories/{category_id}")
def update_category(category_id: int, body: CategoryIn, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(SETTINGS())):
    c = db.get(AccAssetCategory, category_id)
    if c is None or c.tenant_id != tenant_id:
        raise HTTPException(404, "Category not found")
    _check_category(db, tenant_id, body)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.asset_category_updated", entity_type="asset_category",
                entity_id=c.id, details=body.model_dump(), request=request)
    db.commit()
    return {"status": "saved"}


def _asset_dict(a: AccAsset, cats, branches=None):
    return {"id": a.id, "number": a.number, "name": a.name, "category_id": a.category_id,
            "category": cats.get(a.category_id).name if cats.get(a.category_id) else "", "branch_id": a.branch_id,
            "branch": (branches or {}).get(a.branch_id), "custodian": a.custodian, "serial_no": a.serial_no,
            "purchase_date": a.purchase_date.isoformat(), "in_service_date": a.in_service_date.isoformat(),
            "cost": _n(a.cost), "residual_value": _n(a.residual_value), "method": a.method,
            "useful_life_months": a.useful_life_months, "rate_pct": float(a.rate_pct or 0),
            "accumulated_depreciation": _n(a.accumulated_depreciation), "net_book_value": _n(ops.nbv(a)),
            "status": a.status, "funding": a.funding, "disposed_on": a.disposed_on.isoformat() if a.disposed_on else None,
            "disposal_proceeds": _n(a.disposal_proceeds) if a.disposal_proceeds is not None else None, "notes": a.notes,
            "monthly_charge": _n(ops._monthly_charge(a)) if a.status == "active" else 0}


@router.get("/assets")
def assets(status: str | None = None, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
           _: User = Depends(VIEW())):
    ops.ensure_categories(db, tenant_id)
    db.commit()
    cats = {c.id: c for c in db.query(AccAssetCategory).filter(AccAssetCategory.tenant_id == tenant_id)}
    branches = {b.id: b.name for b in db.query(Branch).filter(Branch.tenant_id == tenant_id)}
    q = db.query(AccAsset).filter(AccAsset.tenant_id == tenant_id)
    if status:
        q = q.filter(AccAsset.status == status)
    rows = [_asset_dict(a, cats, branches) for a in q.order_by(AccAsset.number)]
    last = db.query(func.max(AccDepreciationRun.period)).filter(AccDepreciationRun.tenant_id == tenant_id).scalar()
    return {"assets": rows, "last_depreciation": last.isoformat() if last else None,
            "totals": {"cost": round(sum(r["cost"] for r in rows if r["status"] != "disposed"), 2),
                       "accumulated": round(sum(r["accumulated_depreciation"] for r in rows if r["status"] != "disposed"), 2),
                       "nbv": round(sum(r["net_book_value"] for r in rows if r["status"] != "disposed"), 2)}}


@router.get("/assets/{asset_id}")
def asset(asset_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    a = db.get(AccAsset, asset_id)
    if a is None or a.tenant_id != tenant_id:
        raise HTTPException(404, "Asset not found")
    cats = {c.id: c for c in db.query(AccAssetCategory).filter(AccAssetCategory.tenant_id == tenant_id)}
    d = _asset_dict(a, cats)
    d["depreciation"] = [{"period": r.period.isoformat(), "amount": _n(ln.amount)}
                         for ln, r in db.query(AccDepreciationLine, AccDepreciationRun)
                         .join(AccDepreciationRun, AccDepreciationRun.id == AccDepreciationLine.run_id)
                         .filter(AccDepreciationLine.asset_id == a.id).order_by(AccDepreciationRun.period)]
    d["attachments"] = _attachments(db, tenant_id, "asset", a.id)
    return d


@router.post("/assets")
def create_asset(body: AssetIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                 user: User = Depends(POST())):
    cat = db.get(AccAssetCategory, body.category_id)
    if cat is None or cat.tenant_id != tenant_id:
        raise HTTPException(404, "Category not found")
    _branch_ok(db, tenant_id, body.branch_id)
    if body.supplier_id:
        s = db.get(AccSupplier, body.supplier_id)
        if s is None or s.tenant_id != tenant_id:
            raise HTTPException(404, "Supplier not found")
    a = ops.register_asset(db, tenant_id, user=user, category=cat, name=body.name,
                           purchase_date=_d(body.purchase_date, "purchase_date"),
                           in_service_date=_d(body.in_service_date, "in_service_date") if body.in_service_date else None,
                           cost=body.cost, residual_value=body.residual_value, funding=body.funding,
                           paid_from_id=body.paid_from_account_id, accumulated=body.accumulated_depreciation,
                           branch_id=body.branch_id, custodian=body.custodian, serial_no=body.serial_no,
                           supplier_id=body.supplier_id, method=body.method, useful_life_months=body.useful_life_months,
                           rate_pct=body.rate_pct, notes=body.notes)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.asset_registered", entity_type="asset",
                entity_id=a.id, details={"number": a.number, "cost": float(a.cost), "funding": a.funding}, request=request)
    db.commit()
    return {"id": a.id, "number": a.number}


@router.post("/assets/{asset_id}/dispose")
def dispose(asset_id: int, body: DisposeIn, request: Request, tenant_id: int = Depends(MODULE),
            db: Session = Depends(get_db), user: User = Depends(APPROVE())):
    a = db.query(AccAsset).filter(AccAsset.id == asset_id, AccAsset.tenant_id == tenant_id).with_for_update().first()
    if a is None:
        raise HTTPException(404, "Asset not found")
    j = ops.dispose_asset(db, tenant_id, a, on=_d(body.date), proceeds=body.proceeds,
                          received_into_id=body.received_into_account_id, user=user, note=body.note)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.asset_disposed", entity_type="asset", entity_id=a.id,
                details={"number": a.number, "proceeds": body.proceeds, "journal": j.number}, request=request)
    db.commit()
    return {"status": "disposed", "journal_id": j.id}


@router.get("/depreciation/preview")
def dep_preview(period: str, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    end = _period(period)
    rows = ops.depreciation_preview(db, tenant_id, end)
    return {"period": end.isoformat(), "rows": rows, "total": round(sum(r["amount"] for r in rows), 2)}


@router.post("/depreciation/run")
def dep_run(body: PeriodIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
            user: User = Depends(CLOSE())):
    run = ops.run_depreciation(db, tenant_id, _period(body.period), user)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.depreciation_run", entity_type="books",
                details={"period": run.period.isoformat(), "total": float(run.total)}, request=request)
    db.commit()
    return {"period": run.period.isoformat(), "total": _n(run.total), "journal_id": run.journal_id}


@router.get("/depreciation/runs")
def dep_runs(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(VIEW())):
    return {"runs": [{"id": r.id, "period": r.period.isoformat(), "total": _n(r.total), "journal_id": r.journal_id}
                     for r in db.query(AccDepreciationRun).filter(AccDepreciationRun.tenant_id == tenant_id)
                     .order_by(AccDepreciationRun.period.desc())]}


# =========================================================================== month-end close
class LockIn(BaseModel):
    period: str
    override_reason: str | None = None


class ReopenIn(BaseModel):
    lock_date: str | None = None          # the new (earlier) lock date; empty = fully open
    reason: str = Field(min_length=5, max_length=500)


@router.get("/close")
def close_status(period: str | None = None, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                 _: User = Depends(VIEW())):
    _fresh(db, tenant_id)
    end = _period(period) if period else L.month_end(date(L.today().year - (L.today().month == 1),
                                                         (L.today().month - 2) % 12 + 1, 1))
    out = ops.close_checklist(db, tenant_id, end)
    out["provision"] = ops.provision_preview(db, tenant_id, end)
    acc = ops.accrual_preview(db, tenant_id, end)
    acc.pop("rows", None)
    out["accrual"] = acc
    out["depreciation"] = {"rows": len(ops.depreciation_preview(db, tenant_id, end))}
    return out


@router.post("/close/provision")
def close_provision(body: PeriodIn, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(CLOSE())):
    end = _period(body.period)
    _fresh(db, tenant_id)
    j = ops.post_provision(db, tenant_id, end, user)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.provision_posted", entity_type="books",
                details={"period": end.isoformat(), "journal": getattr(j, "number", None)}, request=request)
    db.commit()
    return {"journal_id": getattr(j, "id", None), "posted": j is not None}


@router.post("/close/accrual")
def close_accrual(body: PeriodIn, request: Request, tenant_id: int = Depends(MODULE),
                  db: Session = Depends(get_db), user: User = Depends(CLOSE())):
    end = _period(body.period)
    _fresh(db, tenant_id)
    j = ops.post_accrual(db, tenant_id, end, user)
    write_audit(db, tenant_id=tenant_id, user=user, action="books.interest_accrued", entity_type="books",
                details={"period": end.isoformat(), "journal": getattr(j, "number", None)}, request=request)
    db.commit()
    return {"journal_id": getattr(j, "id", None), "posted": j is not None}


@router.post("/close/lock")
def close_lock(body: LockIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
               user: User = Depends(CLOSE())):
    end = _period(body.period)
    if end > L.today():
        raise HTTPException(422, "A month can only be closed after it has ended")
    _fresh(db, tenant_id)
    check = ops.close_checklist(db, tenant_id, end)
    if not check["ready"] and not (body.override_reason and len(body.override_reason.strip()) >= 10):
        missing = [i["label"] for i in check["items"] if not i["ok"] and not i.get("optional")]
        raise HTTPException(409, "Not ready to close: " + "; ".join(missing) +
                                 ". Finish these, or give a reason (10+ characters) to close anyway.")
    s = L.ensure_books(db, tenant_id, lock=True)
    if s.lock_date and s.lock_date >= end:
        raise HTTPException(409, f"The books are already closed up to {s.lock_date.isoformat()}")
    s.lock_date = end
    write_audit(db, tenant_id=tenant_id, user=user, action="books.period_closed", entity_type="books",
                details={"lock_date": end.isoformat(), "override_reason": body.override_reason,
                         "not_ready": [i["key"] for i in check["items"] if not i["ok"]]}, request=request)
    db.commit()
    return {"lock_date": end.isoformat()}


@router.post("/close/reopen")
def close_reopen(body: ReopenIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                 user: User = Depends(CLOSE())):
    s = L.ensure_books(db, tenant_id, lock=True)
    if not s.lock_date:
        raise HTTPException(409, "No period is closed")
    new = _d(body.lock_date, "lock_date") if body.lock_date else None
    if new and new >= s.lock_date:
        raise HTTPException(422, "Reopening moves the lock date earlier")
    old = s.lock_date
    s.lock_date = new
    write_audit(db, tenant_id=tenant_id, user=user, action="books.period_reopened", entity_type="books",
                details={"from": old.isoformat(), "to": new.isoformat() if new else None, "reason": body.reason},
                request=request)
    db.commit()
    return {"lock_date": new.isoformat() if new else None}
