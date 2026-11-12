"""WP16 — lending additions: write-offs, waivers, rescheduling (asked by one
person, approved by another), top-ups, the collateral register, and groups.

/api/v1/lending-plus/...   Module 'lending'.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import UserScope, get_scope, require_module, require_permission, write_audit
from app.core.money import money
from app.models import (Borrower, Branch, LendingGroup, LendingGroupMeeting, LendingGroupMember, Loan, LoanAction,
                        LoanCollateral, Staff, User)
from app.services import lending_plus as LP
from app.services import ledger as L
from app.services import repayments as rp

router = APIRouter(prefix="/api/v1/lending-plus", tags=["lending-plus"])
MODULE = require_module("lending")


def perm(*keys, mode="all"):
    return require_permission(*keys, mode=mode)


def _can(db, user, key) -> bool:
    if user.role == "super_admin":
        return True
    from app.services.authz import effective_permissions
    return key in effective_permissions(db, user.tenant_id, user.role)


def _d(v, field="date"):
    try:
        return datetime.strptime(str(v), "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        raise HTTPException(422, f"{field} must be a date (YYYY-MM-DD)")


def _loan(db, tenant_id, loan_id, scope: UserScope | None = None, lock=False) -> Loan:
    q = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == tenant_id)
    loan = (q.with_for_update() if lock else q).first()
    if loan is None:
        raise HTTPException(404, "Loan not found")
    if scope is not None and not scope.can_see_loan(loan):
        raise HTTPException(404, "Loan not found")
    return loan


def _names(db, ids):
    ids = {i for i in ids if i}
    return {u.id: u.full_name for u in db.query(User).filter(User.id.in_(ids or {-1}))}


def _action_dict(a: LoanAction, loan: Loan | None = None, names=None):
    return {"id": a.id, "loan_id": a.loan_id, "kind": a.kind, "status": a.status, "details": a.details,
            "reason": a.reason, "requested_by_user_id": a.requested_by_user_id,
            "requested_by": (names or {}).get(a.requested_by_user_id), "decided_by": (names or {}).get(a.decided_by_user_id),
            "decided_at": a.decided_at.isoformat() if a.decided_at else None, "decision_note": a.decision_note,
            "journal_id": a.journal_id, "created_at": a.created_at.isoformat() if a.created_at else None,
            "account_number": getattr(loan, "account_number", None),
            "borrower": f"{loan.borrower.first_name} {loan.borrower.last_name}" if loan is not None and loan.borrower else None,
            "outstanding": float(money(loan.outstanding_balance or 0)) if loan is not None else None,
            "loan_status": getattr(loan, "status", None)}


# =========================================================================== loan actions
class ActionIn(BaseModel):
    kind: str
    reason: str = Field(min_length=10, max_length=2000)
    component: str | None = None
    amount: float | None = None
    new_due_date: str | None = None
    fee: float | None = None


@router.get("/loans/{loan_id}")
def loan_extras(loan_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                user: User = Depends(perm("loans.view_all", "loans.view_portfolio", mode="any")),
                scope: UserScope = Depends(get_scope)):
    """Everything WP16 adds to a loan's page: owed parts, actions, collateral, group, top-ups."""
    loan = _loan(db, tenant_id, loan_id, scope)
    owed = rp.owed_breakdown(db, loan)
    actions = db.query(LoanAction).filter(LoanAction.loan_id == loan.id).order_by(LoanAction.id.desc()).all()
    names = _names(db, [a.requested_by_user_id for a in actions] + [a.decided_by_user_id for a in actions])
    coll = db.query(LoanCollateral).filter(LoanCollateral.loan_id == loan.id).all()
    held = sum((money(c.estimated_value or 0) for c in coll if c.status == "held"), L.ZERO)
    fsv = sum((money(c.forced_sale_value or c.estimated_value or 0) for c in coll if c.status == "held"), L.ZERO)
    group = db.get(LendingGroup, loan.group_id) if loan.group_id else None
    topups = db.query(Loan).filter(Loan.topup_of_loan_id == loan.id).all()
    return {"loan_id": loan.id, "status": loan.status, "owed": {k: float(v) for k, v in owed.items()},
            "written_off_at": loan.written_off_at.isoformat() if loan.written_off_at else None,
            "written_off_amount": float(loan.written_off_amount or 0), "interest_waived": float(loan.interest_waived or 0),
            "fees_waived": float(loan.fees_waived or 0), "restructure_count": loan.restructure_count or 0,
            "topup_of_loan_id": loan.topup_of_loan_id,
            "topups": [{"id": t.id, "account_number": t.account_number, "status": t.status, "principal": float(t.principal)} for t in topups],
            "actions": [_action_dict(a, loan, names) for a in actions],
            "collateral": [_coll_dict(c) for c in coll],
            "collateral_value": float(held), "collateral_forced_sale_value": float(fsv),
            "collateral_cover_pct": round(float(fsv / money(loan.outstanding_balance) * 100), 1) if money(loan.outstanding_balance or 0) > 0 else None,
            "group": {"id": group.id, "name": group.name, "code": group.code} if group else None,
            "can": {k: _can(db, user, p) for k, p in (("writeoff", "loans.writeoff"), ("reschedule", "loans.writeoff"),
                                                       ("waiver", "loans.waive"), ("topup", "loans.topup"),
                                                       ("collateral", "collateral.manage"), ("groups", "groups.manage"))}}


@router.post("/loans/{loan_id}/actions")
def request_action(loan_id: int, body: ActionIn, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(perm("loans.view_all", "loans.view_portfolio", mode="any")),
                   scope: UserScope = Depends(get_scope)):
    if body.kind not in LP.ACTION_KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(LP.ACTION_KINDS)}")
    if not _can(db, user, LP.PERM_FOR[body.kind]):
        raise HTTPException(403, f"Missing required permission: {LP.PERM_FOR[body.kind]}")
    loan = _loan(db, tenant_id, loan_id, scope, lock=True)
    a = LP.request_action(db, tenant_id, loan, body.kind, body.model_dump(exclude={"kind", "reason"}), body.reason, user)
    write_audit(db, tenant_id=tenant_id, user=user, action=f"loan.{body.kind}_requested", entity_type="loan",
                entity_id=loan.id, details={"action": a.id, **a.details, "reason": body.reason}, request=request)
    db.commit()
    return {"id": a.id, "status": a.status}


@router.get("/actions")
def actions(status: str | None = "pending", kind: str | None = None, tenant_id: int = Depends(MODULE),
            db: Session = Depends(get_db), user: User = Depends(perm("loans.writeoff", "loans.waive", mode="any"))):
    q = db.query(LoanAction).filter(LoanAction.tenant_id == tenant_id)
    if status:
        q = q.filter(LoanAction.status == status)
    if kind:
        q = q.filter(LoanAction.kind == kind)
    rows = q.order_by(LoanAction.id.desc()).limit(500).all()
    loans = {ln.id: ln for ln in db.query(Loan).filter(Loan.id.in_({a.loan_id for a in rows} or {-1}))}
    names = _names(db, [a.requested_by_user_id for a in rows] + [a.decided_by_user_id for a in rows])
    out = []
    for a in rows:
        d = _action_dict(a, loans.get(a.loan_id), names)
        d["can_decide"] = (a.status == "pending" and _can(db, user, LP.PERM_FOR[a.kind])
                           and (user.role == "super_admin" or a.requested_by_user_id != user.id))
        out.append(d)
    return {"actions": out}


class DecideIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


@router.post("/actions/{action_id}/decide")
def decide(action_id: int, body: DecideIn, request: Request, tenant_id: int = Depends(MODULE),
           db: Session = Depends(get_db), user: User = Depends(perm("loans.writeoff", "loans.waive", mode="any"))):
    a = db.query(LoanAction).filter(LoanAction.id == action_id, LoanAction.tenant_id == tenant_id).with_for_update().first()
    if a is None:
        raise HTTPException(404, "Request not found")
    if not _can(db, user, LP.PERM_FOR[a.kind]):
        raise HTTPException(403, f"Missing required permission: {LP.PERM_FOR[a.kind]}")
    if not body.approve and not (body.note and len(body.note.strip()) >= 3):
        raise HTTPException(422, "Say why it is rejected")
    LP.decide(db, a, user, body.approve, body.note)
    write_audit(db, tenant_id=tenant_id, user=user, action=f"loan.{a.kind}_{'approved' if body.approve else 'rejected'}",
                entity_type="loan", entity_id=a.loan_id, details={"action": a.id, "note": body.note}, request=request)
    db.commit()
    return {"status": a.status, "journal_id": a.journal_id}


@router.post("/actions/{action_id}/cancel")
def cancel(action_id: int, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
           user: User = Depends(perm("loans.writeoff", "loans.waive", mode="any"))):
    a = db.query(LoanAction).filter(LoanAction.id == action_id, LoanAction.tenant_id == tenant_id).with_for_update().first()
    if a is None:
        raise HTTPException(404, "Request not found")
    if a.status != "pending":
        raise HTTPException(409, f"This request is {a.status}")
    if a.requested_by_user_id != user.id and user.role != "super_admin":
        raise HTTPException(403, "Only the person who asked can withdraw it")
    a.status = "cancelled"
    write_audit(db, tenant_id=tenant_id, user=user, action=f"loan.{a.kind}_withdrawn", entity_type="loan",
                entity_id=a.loan_id, details={"action": a.id}, request=request)
    db.commit()
    return {"status": "cancelled"}


# =========================================================================== top-up
class TopupIn(BaseModel):
    principal: float = Field(gt=0)
    product_id: int | None = None


@router.post("/loans/{loan_id}/topup")
def topup(loan_id: int, body: TopupIn, request: Request, tenant_id: int = Depends(MODULE),
          db: Session = Depends(get_db), user: User = Depends(perm("loans.topup")),
          scope: UserScope = Depends(get_scope)):
    old = _loan(db, tenant_id, loan_id, scope, lock=True)
    new = LP.create_topup(db, tenant_id, old, principal=body.principal, product_id=body.product_id, user=user)
    write_audit(db, tenant_id=tenant_id, user=user, action="loan.topup_requested", entity_type="loan", entity_id=old.id,
                details={"new_loan": new.id, "principal": body.principal}, request=request)
    db.commit()
    return {"id": new.id, "account_number": new.account_number, "status": new.status,
            "payoff_now": float(money(rp.payoff_quote(db, old, datetime.utcnow())["payoff_amount"])),
            "payout": (new.pricing_snapshot or {}).get("payout_amount")}


# =========================================================================== collateral
KINDS = ("logbook", "title_deed", "household", "business_equipment", "stock", "savings", "chattel", "guarantee", "other")


class CollateralIn(BaseModel):
    borrower_id: int
    loan_id: int | None = None
    kind: str
    description: str = Field(min_length=3, max_length=300)
    identifier: str | None = Field(default=None, max_length=80)
    estimated_value: float = Field(ge=0)
    forced_sale_value: float | None = Field(default=None, ge=0)
    valued_by: str | None = None
    valued_on: str | None = None
    location: str | None = None
    insured: bool = False
    insurer: str | None = None
    policy_no: str | None = None
    insurance_expiry: str | None = None
    received_on: str


def _coll_dict(c: LoanCollateral):
    return {"id": c.id, "borrower_id": c.borrower_id, "loan_id": c.loan_id, "kind": c.kind, "description": c.description,
            "identifier": c.identifier, "estimated_value": float(c.estimated_value or 0),
            "forced_sale_value": float(c.forced_sale_value) if c.forced_sale_value is not None else None,
            "valued_by": c.valued_by, "valued_on": c.valued_on.isoformat() if c.valued_on else None, "location": c.location,
            "insured": c.insured, "insurer": c.insurer, "policy_no": c.policy_no,
            "insurance_expiry": c.insurance_expiry.isoformat() if c.insurance_expiry else None, "status": c.status,
            "received_on": c.received_on.isoformat(), "released_on": c.released_on.isoformat() if c.released_on else None,
            "status_note": c.status_note}


def _check_coll(db, tenant_id, body: CollateralIn):
    if body.kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}")
    b = db.get(Borrower, body.borrower_id)
    if b is None or b.tenant_id != tenant_id:
        raise HTTPException(404, "Client not found")
    if body.loan_id:
        ln = db.get(Loan, body.loan_id)
        if ln is None or ln.tenant_id != tenant_id or ln.borrower_id != b.id:
            raise HTTPException(422, "That loan is not this client's")
    if body.forced_sale_value is not None and body.forced_sale_value > body.estimated_value:
        raise HTTPException(422, "The forced-sale value cannot be more than the estimated value")
    if body.insured and not body.insurer:
        raise HTTPException(422, "Give the insurer for insured collateral")


@router.get("/collateral")
def collateral(status: str | None = "held", borrower_id: int | None = None, q: str | None = None,
               tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
               _: User = Depends(perm("loans.view_all", "loans.view_portfolio", mode="any"))):
    qry = db.query(LoanCollateral, Borrower).join(Borrower, Borrower.id == LoanCollateral.borrower_id) \
        .filter(LoanCollateral.tenant_id == tenant_id)
    if status:
        qry = qry.filter(LoanCollateral.status == status)
    if borrower_id:
        qry = qry.filter(LoanCollateral.borrower_id == borrower_id)
    if q:
        like = f"%{q.strip()}%"
        qry = qry.filter(or_(LoanCollateral.description.ilike(like), LoanCollateral.identifier.ilike(like)))
    rows = qry.order_by(LoanCollateral.id.desc()).limit(1000).all()
    loans = {ln.id: ln for ln in db.query(Loan).filter(Loan.id.in_({c.loan_id for c, _ in rows if c.loan_id} or {-1}))}
    today = L.today()
    out = []
    for c, b in rows:
        d = _coll_dict(c)
        d["borrower"] = f"{b.first_name} {b.last_name}"
        ln = loans.get(c.loan_id)
        d["account_number"] = ln.account_number if ln else None
        d["loan_status"] = ln.status if ln else None
        d["insurance_lapsed"] = bool(c.insured and c.insurance_expiry and c.insurance_expiry < today)
        d["release_due"] = bool(c.status == "held" and ln and ln.status == "paid")
        out.append(d)
    return {"collateral": out, "total_value": round(sum(d["estimated_value"] for d in out), 2)}


@router.post("/collateral")
def add_collateral(body: CollateralIn, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(perm("collateral.manage"))):
    _check_coll(db, tenant_id, body)
    data = body.model_dump()
    for k in ("valued_on", "insurance_expiry", "received_on"):
        data[k] = _d(data[k], k) if data.get(k) else None
    c = LoanCollateral(tenant_id=tenant_id, status="held", created_by_user_id=user.id, **data)
    db.add(c)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="collateral.added", entity_type="loan",
                entity_id=c.loan_id, details={"id": c.id, "kind": c.kind, "value": body.estimated_value}, request=request)
    db.commit()
    return {"id": c.id}


@router.put("/collateral/{cid}")
def edit_collateral(cid: int, body: CollateralIn, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(perm("collateral.manage"))):
    c = db.get(LoanCollateral, cid)
    if c is None or c.tenant_id != tenant_id:
        raise HTTPException(404, "Collateral not found")
    if c.status != "held":
        raise HTTPException(409, f"This collateral is {c.status}")
    _check_coll(db, tenant_id, body)
    data = body.model_dump()
    for k in ("valued_on", "insurance_expiry", "received_on"):
        data[k] = _d(data[k], k) if data.get(k) else None
    for k, v in data.items():
        setattr(c, k, v)
    write_audit(db, tenant_id=tenant_id, user=user, action="collateral.updated", entity_type="loan",
                entity_id=c.loan_id, details={"id": c.id}, request=request)
    db.commit()
    return {"status": "saved"}


class CollStatusIn(BaseModel):
    status: str
    date: str
    note: str = Field(min_length=5, max_length=1000)


@router.post("/collateral/{cid}/status")
def collateral_status(cid: int, body: CollStatusIn, request: Request, tenant_id: int = Depends(MODULE),
                      db: Session = Depends(get_db), user: User = Depends(perm("collateral.manage"))):
    c = db.get(LoanCollateral, cid)
    if c is None or c.tenant_id != tenant_id:
        raise HTTPException(404, "Collateral not found")
    allowed = {"held": ("released", "seized"), "seized": ("sold", "released")}.get(c.status, ())
    if body.status not in allowed:
        raise HTTPException(409, f"Collateral that is {c.status} cannot become {body.status}")
    if body.status == "released" and c.loan_id:
        ln = db.get(Loan, c.loan_id)
        if ln and ln.status in rp.COLLECTIBLE_STATUSES and money(ln.outstanding_balance or 0) > 0 and \
                not _can(db, user, "loans.writeoff"):
            raise HTTPException(409, "The loan is not repaid yet: only a manager who can write off loans may release it now")
    c.status, c.status_note = body.status, body.note
    if body.status in ("released", "sold"):
        c.released_on = _d(body.date)
    write_audit(db, tenant_id=tenant_id, user=user, action=f"collateral.{body.status}", entity_type="loan",
                entity_id=c.loan_id, details={"id": c.id, "note": body.note}, request=request)
    db.commit()
    return {"status": c.status}


# =========================================================================== groups
class GroupIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    branch_id: int | None = None
    officer_staff_id: int | None = None
    registration_no: str | None = None
    meeting_day: str | None = None
    meeting_frequency: str = "weekly"
    meeting_place: str | None = None
    formed_on: str | None = None
    status: str = "active"


def _group_dict(db, g: LendingGroup, branches=None, officers=None, with_summary=True):
    d = {"id": g.id, "code": g.code, "name": g.name, "branch_id": g.branch_id, "branch": (branches or {}).get(g.branch_id),
         "officer_staff_id": g.officer_staff_id, "officer": (officers or {}).get(g.officer_staff_id),
         "registration_no": g.registration_no, "meeting_day": g.meeting_day, "meeting_frequency": g.meeting_frequency,
         "meeting_place": g.meeting_place, "status": g.status, "formed_on": g.formed_on.isoformat() if g.formed_on else None}
    if with_summary:
        d.update(LP.group_summary(db, g))
    return d


def _check_group(db, tenant_id, body: GroupIn):
    if body.meeting_frequency not in ("weekly", "fortnightly", "monthly"):
        raise HTTPException(422, "meeting_frequency must be weekly, fortnightly or monthly")
    if body.status not in ("active", "dormant", "closed"):
        raise HTTPException(422, "status must be active, dormant or closed")
    if body.branch_id:
        br = db.get(Branch, body.branch_id)
        if br is None or br.tenant_id != tenant_id:
            raise HTTPException(422, "Branch not found")
    if body.officer_staff_id:
        st = db.get(Staff, body.officer_staff_id)
        if st is None or st.tenant_id != tenant_id:
            raise HTTPException(422, "Officer not found")


@router.get("/groups")
def groups(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
           _: User = Depends(perm("loans.view_all", "loans.view_portfolio", mode="any"))):
    branches = {b.id: b.name for b in db.query(Branch).filter(Branch.tenant_id == tenant_id)}
    officers = {s.id: s.name for s in db.query(Staff).filter(Staff.tenant_id == tenant_id)}
    return {"groups": [_group_dict(db, g, branches, officers) for g in
                       db.query(LendingGroup).filter(LendingGroup.tenant_id == tenant_id).order_by(LendingGroup.name)],
            "branches": [{"id": i, "name": n} for i, n in branches.items()],
            "officers": [{"id": i, "name": n} for i, n in officers.items()]}


@router.post("/groups")
def create_group(body: GroupIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                 user: User = Depends(perm("groups.manage"))):
    _check_group(db, tenant_id, body)
    n = (db.query(func.count(LendingGroup.id)).filter(LendingGroup.tenant_id == tenant_id).scalar() or 0) + 1
    code = f"GRP-{n:04d}"
    while db.query(LendingGroup.id).filter(LendingGroup.tenant_id == tenant_id, LendingGroup.code == code).first():
        n += 1
        code = f"GRP-{n:04d}"
    data = body.model_dump()
    data["formed_on"] = _d(data["formed_on"]) if data.get("formed_on") else None
    g = LendingGroup(tenant_id=tenant_id, code=code, **data)
    db.add(g)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="group.created", entity_type="group", entity_id=g.id,
                details={"name": g.name}, request=request)
    db.commit()
    return {"id": g.id, "code": code}


@router.put("/groups/{gid}")
def edit_group(gid: int, body: GroupIn, request: Request, tenant_id: int = Depends(MODULE),
               db: Session = Depends(get_db), user: User = Depends(perm("groups.manage"))):
    g = db.get(LendingGroup, gid)
    if g is None or g.tenant_id != tenant_id:
        raise HTTPException(404, "Group not found")
    _check_group(db, tenant_id, body)
    data = body.model_dump()
    data["formed_on"] = _d(data["formed_on"]) if data.get("formed_on") else None
    for k, v in data.items():
        setattr(g, k, v)
    write_audit(db, tenant_id=tenant_id, user=user, action="group.updated", entity_type="group", entity_id=g.id,
                request=request)
    db.commit()
    return {"status": "saved"}


@router.get("/groups/{gid}")
def group(gid: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
          _: User = Depends(perm("loans.view_all", "loans.view_portfolio", mode="any"))):
    g = db.get(LendingGroup, gid)
    if g is None or g.tenant_id != tenant_id:
        raise HTTPException(404, "Group not found")
    d = _group_dict(db, g)
    members = (db.query(LendingGroupMember, Borrower).join(Borrower, Borrower.id == LendingGroupMember.borrower_id)
               .filter(LendingGroupMember.group_id == g.id).order_by(LendingGroupMember.active.desc(), Borrower.first_name).all())
    ids = [b.id for _, b in members]
    loans = (db.query(Loan).filter(Loan.tenant_id == g.tenant_id, Loan.borrower_id.in_(ids or [-1]),
                                   Loan.status.in_(("active", "overdue", "defaulted", "written_off"))).all())
    today = L.today()
    by_b = {}
    for ln in loans:
        x = by_b.setdefault(ln.borrower_id, {"outstanding": 0.0, "late": False, "loans": []})
        x["outstanding"] += float(ln.outstanding_balance or 0)
        x["late"] = x["late"] or bool(ln.due_date and ln.due_date < today)
        x["loans"].append({"id": ln.id, "account_number": ln.account_number, "status": ln.status,
                           "outstanding": float(ln.outstanding_balance or 0), "in_group": ln.group_id == g.id})
    d["members_list"] = [{"id": m.id, "borrower_id": b.id, "name": f"{b.first_name} {b.last_name}", "phone": b.phone,
                          "role": m.role, "joined_on": m.joined_on.isoformat(), "active": m.active,
                          "left_on": m.left_on.isoformat() if m.left_on else None, **by_b.get(b.id, {"outstanding": 0, "late": False, "loans": []})}
                         for m, b in members]
    meetings = (db.query(LendingGroupMeeting).filter(LendingGroupMeeting.group_id == g.id)
                .order_by(LendingGroupMeeting.held_on.desc()).limit(52).all())
    active_n = max(1, sum(1 for m, _ in members if m.active))
    d["meetings"] = [{"id": mt.id, "held_on": mt.held_on.isoformat(), "present": mt.present, "attendance_pct":
                      round(len(mt.present or []) / active_n * 100), "collected": float(mt.collected or 0), "notes": mt.notes}
                     for mt in meetings]
    return d


class MemberIn(BaseModel):
    borrower_id: int
    role: str = "member"
    joined_on: str | None = None


@router.post("/groups/{gid}/members")
def add_member(gid: int, body: MemberIn, request: Request, tenant_id: int = Depends(MODULE),
               db: Session = Depends(get_db), user: User = Depends(perm("groups.manage"))):
    g = db.get(LendingGroup, gid)
    if g is None or g.tenant_id != tenant_id:
        raise HTTPException(404, "Group not found")
    if body.role not in ("chair", "secretary", "treasurer", "member"):
        raise HTTPException(422, "role must be chair, secretary, treasurer or member")
    LP.check_member(db, tenant_id, body.borrower_id, g.id)
    m = db.query(LendingGroupMember).filter(LendingGroupMember.group_id == g.id, LendingGroupMember.borrower_id == body.borrower_id,
                                            LendingGroupMember.active.is_(True)).first()
    if m:
        m.role = body.role
    else:
        m = LendingGroupMember(tenant_id=tenant_id, group_id=g.id, borrower_id=body.borrower_id, role=body.role,
                               joined_on=_d(body.joined_on) if body.joined_on else L.today(), active=True)
        db.add(m)
    if body.role != "member":
        for other in db.query(LendingGroupMember).filter(LendingGroupMember.group_id == g.id, LendingGroupMember.role == body.role,
                                                         LendingGroupMember.borrower_id != body.borrower_id):
            other.role = "member"
    write_audit(db, tenant_id=tenant_id, user=user, action="group.member_added", entity_type="group", entity_id=g.id,
                details={"borrower_id": body.borrower_id, "role": body.role}, request=request)
    db.commit()
    return {"status": "saved"}


@router.post("/groups/{gid}/members/{mid}/leave")
def member_leaves(gid: int, mid: int, request: Request, tenant_id: int = Depends(MODULE),
                  db: Session = Depends(get_db), user: User = Depends(perm("groups.manage"))):
    m = db.get(LendingGroupMember, mid)
    if m is None or m.tenant_id != tenant_id or m.group_id != gid or not m.active:
        raise HTTPException(404, "Member not found")
    open_group_loan = db.query(Loan.id).filter(Loan.borrower_id == m.borrower_id, Loan.group_id == gid,
                                               Loan.status.in_(rp.COLLECTIBLE_STATUSES)).first()
    if open_group_loan:
        raise HTTPException(409, "This member has a group loan that is not repaid: the group still guarantees it")
    m.active, m.left_on = False, L.today()
    write_audit(db, tenant_id=tenant_id, user=user, action="group.member_left", entity_type="group", entity_id=gid,
                details={"borrower_id": m.borrower_id}, request=request)
    db.commit()
    return {"status": "left"}


class MeetingIn(BaseModel):
    held_on: str
    present: list[int] = []
    collected: float = Field(default=0, ge=0)
    notes: str | None = None


@router.post("/groups/{gid}/meetings")
def record_meeting(gid: int, body: MeetingIn, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(perm("groups.manage"))):
    g = db.get(LendingGroup, gid)
    if g is None or g.tenant_id != tenant_id:
        raise HTTPException(404, "Group not found")
    held = _d(body.held_on, "held_on")
    if held > L.today():
        raise HTTPException(422, "A meeting cannot be recorded before it happens")
    members = {m.borrower_id for m in db.query(LendingGroupMember).filter(LendingGroupMember.group_id == g.id,
                                                                          LendingGroupMember.active.is_(True))}
    if set(body.present) - members:
        raise HTTPException(422, "Everyone marked present must be a member of the group")
    mt = db.query(LendingGroupMeeting).filter(LendingGroupMeeting.group_id == g.id, LendingGroupMeeting.held_on == held).first()
    if mt is None:
        mt = LendingGroupMeeting(tenant_id=tenant_id, group_id=g.id, held_on=held)
        db.add(mt)
    mt.present, mt.collected, mt.notes, mt.recorded_by_user_id = sorted(set(body.present)), money(body.collected), body.notes, user.id
    write_audit(db, tenant_id=tenant_id, user=user, action="group.meeting_recorded", entity_type="group", entity_id=g.id,
                details={"held_on": held.isoformat(), "present": len(body.present)}, request=request)
    db.commit()
    return {"status": "saved"}


class TagIn(BaseModel):
    group_id: int | None = None


@router.post("/loans/{loan_id}/group")
def tag_loan(loan_id: int, body: TagIn, request: Request, tenant_id: int = Depends(MODULE),
             db: Session = Depends(get_db), user: User = Depends(perm("groups.manage")),
             scope: UserScope = Depends(get_scope)):
    loan = _loan(db, tenant_id, loan_id, scope, lock=True)
    if body.group_id:
        g = db.get(LendingGroup, body.group_id)
        if g is None or g.tenant_id != tenant_id or g.status != "active":
            raise HTTPException(404, "Active group not found")
        if not db.query(LendingGroupMember.id).filter(LendingGroupMember.group_id == g.id,
                                                      LendingGroupMember.borrower_id == loan.borrower_id,
                                                      LendingGroupMember.active.is_(True)).first():
            raise HTTPException(422, "The client is not a member of that group")
    loan.group_id = body.group_id
    write_audit(db, tenant_id=tenant_id, user=user, action="loan.group_set", entity_type="loan", entity_id=loan.id,
                details={"group_id": body.group_id}, request=request)
    db.commit()
    return {"group_id": loan.group_id}


@router.get("/clients/search")
def client_search(q: str, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                  _: User = Depends(perm("loans.view_all", "loans.view_portfolio", mode="any")),
                  scope: UserScope = Depends(get_scope)):
    """Find clients by name or phone (for adding group members and collateral)."""
    like = f"%{q.strip()}%"
    qry = db.query(Borrower).filter(Borrower.tenant_id == tenant_id,
                                    or_(Borrower.first_name.ilike(like), Borrower.last_name.ilike(like),
                                        Borrower.phone.ilike(like)))
    rows = scope.apply_client(qry, Borrower).limit(20).all()
    return {"clients": [{"id": b.id, "name": f"{b.first_name} {b.last_name}", "phone": b.phone} for b in rows]}
