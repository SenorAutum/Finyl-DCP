"""WP14 — reconciliation, fixed assets, budgets and the month-end close.

Reconciliation
  A bank or M-Pesa statement (CSV or .xlsx, the same files the statement import
  reads) is uploaded against one money account. Each statement line is matched to
  a ledger line on that account with the same amount within a few days (a matching
  reference wins). Lines with no partner are matched by hand, ignored with a note,
  or turned into a journal (bank charges, interest received ...). The report
  compares the statement balance with the books, item by item.

Fixed assets
  An asset is registered with its category (accounts, method, useful life). It is
  paid from a money account, already on a supplier bill, or an opening balance.
  Depreciation runs monthly (straight line, or reducing balance), never below the
  residual value; disposal removes cost and depreciation and books the gain or loss.

Budgets
  Per financial year, optionally per branch, an amount per account per month.
  Budget vs actual compares them with the books, month by month and to date.

Month-end close
  A checklist (everything posted, approvals cleared, depreciation run, provision
  and accrued interest posted, money accounts reconciled, trial balance balanced),
  then the period is locked: nothing can be posted on or before the lock date.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.money import D, money
from app.models import (AccAsset, AccAssetCategory, AccBankLine, AccBankStatement, AccBill, AccBudget,
                        AccBudgetLine, AccDepreciationLine, AccDepreciationRun, AccExpenseClaim,
                        AccJournal, AccJournalLine, AccPaymentOut, ChartOfAccount, EclProvisionConfig,
                        Loan, Repayment)
from app.services import bank_import as bi
from app.services import ledger as L
from app.services.pricing import loan_snapshot

ZERO = L.ZERO


# =========================================================================== reconciliation
def money_account(db, tenant_id, account_id) -> ChartOfAccount:
    a = db.get(ChartOfAccount, account_id)
    if a is None or a.tenant_id != tenant_id:
        raise HTTPException(404, "Account not found")
    if not a.is_cash:
        raise HTTPException(422, f"{a.code} {a.name} is not a bank, M-Pesa or cash account")
    return a


def read_statement(data: bytes, filename: str, layout: dict | None = None) -> tuple[list[dict], dict]:
    """Statement file -> [{date, description, reference, amount(+in/-out), balance}]."""
    rows = bi.read_rows(data, filename)
    layout = bi.clean_layout(layout) if layout else bi.clean_layout(bi.detect_layout(rows))
    h = bi._header_index(rows, layout)
    header = [bi._norm(c) for c in rows[h]]

    def col(fld):
        name = layout.get(fld)
        return header.index(bi._norm(name)) if name and bi._norm(name) in header else None

    cols = {f: col(f) for f in bi.FIELDS}
    out = []
    for row in rows[h + 1:]:
        get = lambda f: (row[cols[f]] if cols[f] is not None and cols[f] < len(row) else "")  # noqa: E731
        if not any(str(c).strip() for c in row) or not str(get("date")).strip():
            continue
        state = str(get("status")).strip().lower()
        if state and state not in ("completed", "complete", "success", "successful"):
            continue
        try:
            when = (bi.parse_date(get("date"), layout["date_order"]) + L.NAIROBI).date()
            cr = bi.parse_amount(get("credit")) if cols["credit"] is not None else None
            dr = bi.parse_amount(get("debit")) if cols["debit"] is not None else None
            amt = bi.parse_amount(get("amount")) if cols["amount"] is not None else None
            bal = bi.parse_amount(get("balance")) if cols["balance"] is not None else None
        except ValueError:
            continue
        if cr is not None or dr is not None:
            amount = money((cr or 0)) - money(abs(dr or 0))
        elif amt is not None:
            amount = money(amt)
        else:
            continue
        if amount == 0:
            continue
        out.append({"date": when, "description": " ".join(str(get("description")).split())[:400],
                    "reference": str(get("reference")).strip()[:80], "amount": amount,
                    "balance": money(bal) if bal is not None else None})
    if not out:
        raise HTTPException(422, "No transactions were found in this file")
    return out, layout


def import_statement(db: Session, tenant_id: int, *, account_id: int, filename: str, data: bytes,
                     closing_balance=None, layout=None, user=None) -> dict:
    acct = money_account(db, tenant_id, account_id)
    try:
        lines, layout = read_statement(data, filename, layout)
    except bi.StatementError as exc:
        raise HTTPException(422, str(exc))
    st = AccBankStatement(tenant_id=tenant_id, account_id=acct.id, filename=(filename or "")[:200],
                          period_from=min(ln["date"] for ln in lines), period_to=max(ln["date"] for ln in lines),
                          uploaded_by_user_id=getattr(user, "id", None), line_count=0)
    with_bal = [ln for ln in lines if ln["balance"] is not None]
    if closing_balance not in (None, ""):
        st.closing_balance = money(closing_balance)
    elif with_bal:
        # the balance after the last transaction: statements list oldest-first or newest-first
        ascending = lines[0]["date"] <= lines[-1]["date"]
        last_day = max(ln["date"] for ln in with_bal)
        last_rows = [ln for ln in with_bal if ln["date"] == last_day]
        st.closing_balance = (last_rows[-1] if ascending else last_rows[0])["balance"]
    db.add(st)
    db.flush()
    added = dupes = 0
    seen = set()
    for ln in lines:
        fp = hashlib.sha256("|".join([ln["date"].isoformat(), str(ln["amount"]), ln["reference"],
                                      ln["description"], str(ln["balance"])]).encode()).hexdigest()
        if fp in seen or db.query(AccBankLine.id).filter(AccBankLine.tenant_id == tenant_id,
                                                          AccBankLine.account_id == acct.id,
                                                          AccBankLine.fingerprint == fp).first():
            dupes += 1
            continue
        seen.add(fp)
        db.add(AccBankLine(statement_id=st.id, tenant_id=tenant_id, account_id=acct.id, txn_date=ln["date"],
                           description=ln["description"] or None, reference=ln["reference"] or None,
                           amount=ln["amount"], fingerprint=fp, status="unmatched"))
        added += 1
    st.line_count = added
    db.flush()
    matched = auto_match(db, tenant_id, acct.id)
    return {"statement_id": st.id, "lines_added": added, "duplicates_skipped": dupes,
            "auto_matched": matched, "closing_balance": float(st.closing_balance) if st.closing_balance is not None else None,
            "period_from": st.period_from.isoformat(), "period_to": st.period_to.isoformat()}


def _open_ledger_lines(db, tenant_id, account_id, date_from=None, date_to=None):
    q = (db.query(AccJournalLine, AccJournal)
         .join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
         .filter(AccJournalLine.tenant_id == tenant_id, AccJournalLine.account_id == account_id,
                 AccJournalLine.reconciled.is_(False), AccJournal.status.in_(L.COUNTED)))
    if date_from:
        q = q.filter(AccJournal.entry_date >= date_from)
    if date_to:
        q = q.filter(AccJournal.entry_date <= date_to)
    return q


def auto_match(db, tenant_id, account_id, window_days: int = 3) -> int:
    bank = (db.query(AccBankLine).filter(AccBankLine.tenant_id == tenant_id, AccBankLine.account_id == account_id,
                                         AccBankLine.status == "unmatched").order_by(AccBankLine.txn_date).all())
    if not bank:
        return 0
    lo = min(b.txn_date for b in bank) - timedelta(days=window_days)
    hi = max(b.txn_date for b in bank) + timedelta(days=window_days)
    pool = defaultdict(list)          # signed amount -> [(line, journal)]
    for ln, j in _open_ledger_lines(db, tenant_id, account_id, lo, hi):
        pool[money(D(ln.debit) - D(ln.credit))].append((ln, j))
    n = 0
    for bl in bank:
        cands = [(ln, j) for ln, j in pool.get(money(bl.amount), [])
                 if abs((j.entry_date - bl.txn_date).days) <= window_days and not ln.reconciled]
        if not cands:
            continue
        ref = (bl.reference or "").upper()

        def score(c):
            ln, j = c
            text = f"{j.reference or ''} {j.description or ''} {ln.description or ''}".upper()
            return (0 if ref and ref in text else 1, abs((j.entry_date - bl.txn_date).days), j.id)
        ln, j = min(cands, key=score)
        _match(bl, ln, None)
        n += 1
    db.flush()
    return n


def _match(bl: AccBankLine, ln: AccJournalLine, user):
    bl.status, bl.journal_line_id = "matched", ln.id
    bl.matched_by_user_id, bl.matched_at = getattr(user, "id", None), datetime.utcnow()
    ln.reconciled = True


def match_line(db, tenant_id, bank_line_id, journal_line_id, user):
    bl = db.get(AccBankLine, bank_line_id)
    ln = db.get(AccJournalLine, journal_line_id)
    if bl is None or bl.tenant_id != tenant_id or ln is None or ln.tenant_id != tenant_id:
        raise HTTPException(404, "Line not found")
    if bl.status != "unmatched":
        raise HTTPException(409, f"This statement line is already {bl.status}")
    if ln.account_id != bl.account_id:
        raise HTTPException(422, "That ledger line is on a different account")
    if ln.reconciled:
        raise HTTPException(409, "That ledger line is already matched to another statement line")
    if money(D(ln.debit) - D(ln.credit)) != money(bl.amount):
        raise HTTPException(422, "The amounts are different: match lines with the same amount, "
                                 "or create a journal for the difference")
    _match(bl, ln, user)


def unmatch_line(db, tenant_id, bank_line_id):
    bl = db.get(AccBankLine, bank_line_id)
    if bl is None or bl.tenant_id != tenant_id:
        raise HTTPException(404, "Line not found")
    if bl.journal_line_id:
        ln = db.get(AccJournalLine, bl.journal_line_id)
        if ln:
            ln.reconciled = False
    bl.status, bl.journal_line_id, bl.matched_at, bl.matched_by_user_id = "unmatched", None, None, None


def ignore_line(db, tenant_id, bank_line_id, note, user):
    bl = db.get(AccBankLine, bank_line_id)
    if bl is None or bl.tenant_id != tenant_id:
        raise HTTPException(404, "Line not found")
    if bl.status != "unmatched":
        raise HTTPException(409, f"This statement line is already {bl.status}")
    bl.status, bl.note, bl.matched_by_user_id, bl.matched_at = "ignored", note, user.id, datetime.utcnow()


def journal_from_line(db, tenant_id, bank_line_id, counter_account_id, description, user):
    """Book a statement line the ledger does not have yet (bank charges, interest)."""
    bl = db.get(AccBankLine, bank_line_id)
    if bl is None or bl.tenant_id != tenant_id:
        raise HTTPException(404, "Line not found")
    if bl.status != "unmatched":
        raise HTTPException(409, f"This statement line is already {bl.status}")
    other = db.get(ChartOfAccount, counter_account_id)
    if other is None or other.tenant_id != tenant_id or other.id == bl.account_id:
        raise HTTPException(422, "Choose the other account for this line (for example M-Pesa and Bank Charges)")
    amt = money(bl.amount)
    desc = (description or bl.description or "Statement line")[:300]
    j = L.post_journal(db, tenant_id, entry_date=bl.txn_date, description=desc,
                       lines=[L.Line(account_id=bl.account_id, debit=amt if amt > 0 else ZERO,
                                     credit=-amt if amt < 0 else ZERO, description=desc),
                              L.Line(account_id=other.id, debit=-amt if amt < 0 else ZERO,
                                     credit=amt if amt > 0 else ZERO, description=desc)],
                       kind="manual", source_type="bank_line", source_id=bl.id, user=user,
                       reference=bl.reference)
    j.approved_by_user_id = user.id
    ln = (db.query(AccJournalLine).filter(AccJournalLine.journal_id == j.id,
                                          AccJournalLine.account_id == bl.account_id).first())
    _match(bl, ln, user)
    return j


def reconciliation(db, tenant_id, account_id, as_at: date) -> dict:
    acct = money_account(db, tenant_id, account_id)
    st = (db.query(AccBankStatement).filter(AccBankStatement.tenant_id == tenant_id,
                                            AccBankStatement.account_id == acct.id,
                                            AccBankStatement.period_to <= as_at,
                                            AccBankStatement.closing_balance.isnot(None))
          .order_by(AccBankStatement.period_to.desc(), AccBankStatement.id.desc()).first())
    first = (db.query(func.min(AccBankStatement.period_from)).filter(AccBankStatement.tenant_id == tenant_id,
                                                                    AccBankStatement.account_id == acct.id).scalar())
    ledger = L.account_balance(db, tenant_id, acct.id, as_at)
    bank_only = (db.query(AccBankLine).filter(AccBankLine.tenant_id == tenant_id, AccBankLine.account_id == acct.id,
                                              AccBankLine.status == "unmatched", AccBankLine.txn_date <= as_at)
                 .order_by(AccBankLine.txn_date).all())
    books_only = (_open_ledger_lines(db, tenant_id, acct.id, first, as_at)
                  .order_by(AccJournal.entry_date).limit(1000).all()) if first else []
    statement_balance = money(st.closing_balance) if st else None
    bank_only_total = sum((money(b.amount) for b in bank_only), ZERO)
    books_only_total = sum((money(D(ln.debit) - D(ln.credit)) for ln, _ in books_only), ZERO)
    difference = (money(statement_balance + books_only_total - ledger - bank_only_total)
                  if statement_balance is not None else None)
    return {
        "account": {"id": acct.id, "code": acct.code, "name": acct.name}, "as_at": as_at.isoformat(),
        "statement_balance": float(statement_balance) if statement_balance is not None else None,
        "statement_date": st.period_to.isoformat() if st else None,
        "ledger_balance": float(ledger),
        "in_statement_not_in_books": [{"id": b.id, "date": b.txn_date.isoformat(), "description": b.description,
                                       "reference": b.reference, "amount": float(b.amount)} for b in bank_only],
        "in_books_not_in_statement": [{"line_id": ln.id, "journal_id": j.id, "number": j.number,
                                       "date": j.entry_date.isoformat(), "description": ln.description or j.description,
                                       "amount": float(money(D(ln.debit) - D(ln.credit)))} for ln, j in books_only],
        "in_statement_not_in_books_total": float(bank_only_total),
        "in_books_not_in_statement_total": float(books_only_total),
        "difference": float(difference) if difference is not None else None,
        "reconciled": difference == 0 if difference is not None else False,
        "since": first.isoformat() if first else None,
    }


# =========================================================================== fixed assets
DEFAULT_CATEGORIES = [("Furniture and Fittings", "1500", 96), ("Computers and Equipment", "1510", 36),
                      ("Motor Vehicles", "1520", 60)]


def ensure_categories(db, tenant_id):
    if db.query(AccAssetCategory.id).filter(AccAssetCategory.tenant_id == tenant_id).first():
        return
    b = L.Books(db, tenant_id)
    for name, code, life in DEFAULT_CATEGORIES:
        if code in b.accounts and "1590" in b.accounts and "6190" in b.accounts:
            db.add(AccAssetCategory(tenant_id=tenant_id, name=name, asset_account_id=b.accounts[code].id,
                                    accum_account_id=b.accounts["1590"].id, expense_account_id=b.accounts["6190"].id,
                                    method="straight_line", useful_life_months=life, rate_pct=0))
    db.flush()


def nbv(a: AccAsset):
    return money(D(a.cost) - D(a.accumulated_depreciation))


def register_asset(db, tenant_id, *, user, category: AccAssetCategory, name, purchase_date: date,
                   in_service_date: date | None, cost, residual_value=0, funding="paid", paid_from_id=None,
                   accumulated=0, branch_id=None, custodian=None, serial_no=None, supplier_id=None,
                   method=None, useful_life_months=None, rate_pct=None, notes=None) -> AccAsset:
    cost, residual, accumulated = money(cost or 0), money(residual_value or 0), money(accumulated or 0)
    if cost <= 0:
        raise HTTPException(422, "Enter the cost of the asset")
    if residual < 0 or residual >= cost:
        raise HTTPException(422, "The residual value must be below the cost")
    if accumulated < 0 or accumulated > cost - residual:
        raise HTTPException(422, "Depreciation to date cannot be more than cost less residual value")
    if funding not in ("paid", "bill", "opening"):
        raise HTTPException(422, "funding must be paid, bill or opening")
    if funding != "opening" and accumulated:
        raise HTTPException(422, "Depreciation to date is only for assets owned before the books started")
    method = method or category.method
    if method not in ("straight_line", "reducing_balance"):
        raise HTTPException(422, "method must be straight_line or reducing_balance")
    life = int(useful_life_months or category.useful_life_months or 0)
    rate = D(rate_pct if rate_pct is not None else category.rate_pct or 0)
    if method == "straight_line" and life <= 0:
        raise HTTPException(422, "Enter the useful life in months")
    if method == "reducing_balance" and not (0 < rate <= 100):
        raise HTTPException(422, "Enter the yearly depreciation rate (1-100%)")
    settings = L.ensure_books(db, tenant_id)
    a = AccAsset(tenant_id=tenant_id, number=L.take_number(db, tenant_id, "asset"), name=name[:160],
                 category_id=category.id, branch_id=branch_id, custodian=(custodian or None) and custodian[:120],
                 serial_no=(serial_no or None) and serial_no[:80], purchase_date=purchase_date,
                 in_service_date=in_service_date or purchase_date, cost=cost, residual_value=residual,
                 method=method, useful_life_months=life, rate_pct=rate, accumulated_depreciation=accumulated,
                 status="active", funding=funding, supplier_id=supplier_id, notes=notes)
    db.add(a)
    db.flush()
    b = L.Books(db, tenant_id, settings)
    desc = f"Asset {a.number} {a.name}"
    if funding == "paid":
        src = db.get(ChartOfAccount, paid_from_id) if paid_from_id else None
        if src is None or src.tenant_id != tenant_id or not src.is_cash:
            raise HTTPException(422, "Choose the bank, M-Pesa or cash account the asset was paid from")
        j = L.post_journal(db, tenant_id, entry_date=purchase_date, description=f"{desc} bought",
                           lines=[L.Line(account_id=category.asset_account_id, debit=cost, asset_id=a.id,
                                         branch_id=branch_id, description=desc),
                                  L.Line(account_id=src.id, credit=cost, asset_id=a.id, description=desc)],
                           kind="auto", source_type="asset_acquired", source_id=a.id, user=user,
                           branch_id=branch_id, settings=settings)
        a.acquisition_journal_id = j.id
    elif funding == "opening":
        lines = [L.Line(account_id=category.asset_account_id, debit=cost, asset_id=a.id, description=desc),
                 L.line(b.account("opening_equity"), credit=cost - accumulated, description=desc)]
        if accumulated:
            lines.append(L.Line(account_id=category.accum_account_id, credit=accumulated, asset_id=a.id,
                                description="Depreciation before the books started"))
        on = settings.books_start or purchase_date
        if settings.lock_date and on <= settings.lock_date:
            raise HTTPException(409, "The books are closed for the opening date: reopen the period to add opening assets")
        j = L.post_journal(db, tenant_id, entry_date=on, description=f"{desc} (opening balance)", lines=lines,
                           kind="auto", source_type="asset_acquired", source_id=a.id, user=user,
                           branch_id=branch_id, settings=settings)
        a.acquisition_journal_id = j.id
    return a


def _monthly_charge(a: AccAsset) -> D:
    left = money(D(a.cost) - D(a.accumulated_depreciation) - D(a.residual_value))
    if left <= 0:
        return ZERO
    if a.method == "reducing_balance":
        charge = money(nbv(a) * D(a.rate_pct) / D(1200))
    else:
        charge = money((D(a.cost) - D(a.residual_value)) / D(max(1, a.useful_life_months)))
    return min(charge, left)


def depreciation_preview(db, tenant_id, period: date) -> list[dict]:
    end = L.month_end(period)
    out = []
    for a in (db.query(AccAsset).filter(AccAsset.tenant_id == tenant_id, AccAsset.status == "active",
                                        AccAsset.in_service_date <= end).order_by(AccAsset.number)):
        c = _monthly_charge(a)
        if c > 0:
            out.append({"asset_id": a.id, "number": a.number, "name": a.name, "amount": float(c),
                        "nbv_before": float(nbv(a)), "nbv_after": float(nbv(a) - c)})
    return out


def run_depreciation(db, tenant_id, period: date, user) -> AccDepreciationRun:
    end = L.month_end(period)
    settings = L.ensure_books(db, tenant_id)
    L.check_open(settings, end)
    last = (db.query(func.max(AccDepreciationRun.period)).filter(AccDepreciationRun.tenant_id == tenant_id).scalar())
    if last and end <= last:
        raise HTTPException(409, f"Depreciation has already been run up to {last.isoformat()}")
    if end > L.month_end(L.today()):
        raise HTTPException(422, "Depreciation cannot be run for a future month")
    cats = {c.id: c for c in db.query(AccAssetCategory).filter(AccAssetCategory.tenant_id == tenant_id)}
    run = AccDepreciationRun(tenant_id=tenant_id, period=end, total=0, run_by_user_id=getattr(user, "id", None))
    db.add(run)
    db.flush()
    lines, total = [], ZERO
    for a in (db.query(AccAsset).filter(AccAsset.tenant_id == tenant_id, AccAsset.status == "active",
                                        AccAsset.in_service_date <= end).with_for_update()):
        c = _monthly_charge(a)
        if c <= 0:
            continue
        cat = cats[a.category_id]
        desc = f"Depreciation {end:%b %Y}: {a.number} {a.name}"
        lines += [L.Line(account_id=cat.expense_account_id, debit=c, asset_id=a.id, branch_id=a.branch_id, description=desc),
                  L.Line(account_id=cat.accum_account_id, credit=c, asset_id=a.id, branch_id=a.branch_id, description=desc)]
        a.accumulated_depreciation = money(D(a.accumulated_depreciation) + c)
        if money(D(a.cost) - D(a.accumulated_depreciation)) <= money(a.residual_value):
            a.status = "fully_depreciated"
        db.add(AccDepreciationLine(run_id=run.id, asset_id=a.id, amount=c))
        total += c
    run.total = total
    if lines:
        j = L.post_journal(db, tenant_id, entry_date=end, description=f"Depreciation for {end:%B %Y}", lines=lines,
                           kind="system", source_type="depreciation", source_id=run.id, user=user, settings=settings)
        run.journal_id = j.id
    db.flush()
    return run


def dispose_asset(db, tenant_id, a: AccAsset, *, on: date, proceeds=0, received_into_id=None, user, note=None):
    if a.status == "disposed":
        raise HTTPException(409, "This asset has already been disposed of")
    if on < a.purchase_date:
        raise HTTPException(422, "The disposal date cannot be before the purchase date")
    proceeds = money(proceeds or 0)
    if proceeds < 0:
        raise HTTPException(422, "Proceeds cannot be negative")
    cat = db.get(AccAssetCategory, a.category_id)
    b = L.Books(db, tenant_id)
    desc = f"Disposal of {a.number} {a.name}"
    acc = money(a.accumulated_depreciation)
    lines = [L.Line(account_id=cat.accum_account_id, debit=acc, asset_id=a.id, description=desc),
             L.Line(account_id=cat.asset_account_id, credit=D(a.cost), asset_id=a.id, description=desc)]
    if proceeds:
        into = db.get(ChartOfAccount, received_into_id) if received_into_id else None
        if into is None or into.tenant_id != tenant_id or not into.is_cash:
            raise HTTPException(422, "Choose the bank, M-Pesa or cash account the sale money went into")
        lines.append(L.Line(account_id=into.id, debit=proceeds, asset_id=a.id, description=desc))
    gain = money(proceeds - nbv(a))
    if gain > 0:
        lines.append(L.line(b.account("other_income"), credit=gain, asset_id=a.id, description="Gain on disposal"))
    elif gain < 0:
        lines.append(L.line(b.account("other_expense"), debit=-gain, asset_id=a.id, description="Loss on disposal"))
    j = L.post_journal(db, tenant_id, entry_date=on, description=desc, lines=lines, kind="auto",
                       source_type="asset_disposed", source_id=a.id, user=user, branch_id=a.branch_id,
                       settings=b.settings)
    a.status, a.disposed_on, a.disposal_proceeds, a.disposal_journal_id = "disposed", on, proceeds, j.id
    if note:
        a.notes = ((a.notes or "") + f"\nDisposed {on.isoformat()}: {note}").strip()
    return j


# =========================================================================== budgets
def fy_months(start: date) -> list[date]:
    out, d = [], start
    for _ in range(12):
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def budget_dict(db, bud: AccBudget, settings) -> dict:
    accts = {a.id: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == bud.tenant_id)}
    by_acct = defaultdict(lambda: [0.0] * 12)
    for ln in db.query(AccBudgetLine).filter(AccBudgetLine.budget_id == bud.id):
        by_acct[ln.account_id][ln.month - 1] = float(ln.amount)
    start = date(bud.fiscal_year, settings.fiscal_year_start_month or 1, 1)
    return {"id": bud.id, "name": bud.name, "fiscal_year": bud.fiscal_year, "branch_id": bud.branch_id,
            "status": bud.status, "start": start.isoformat(),
            "months": [m.strftime("%b %Y") for m in fy_months(start)],
            "lines": [{"account_id": aid, "code": accts[aid].code, "name": accts[aid].name, "type": accts[aid].type,
                       "amounts": vals, "total": round(sum(vals), 2)}
                      for aid, vals in sorted(by_acct.items(), key=lambda kv: accts[kv[0]].code)]}


def save_budget_lines(db, bud: AccBudget, lines: list[dict]):
    if bud.status == "approved":
        raise HTTPException(409, "An approved budget cannot be changed: copy it to a new version")
    accts = {a.id: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == bud.tenant_id)}
    db.query(AccBudgetLine).filter(AccBudgetLine.budget_id == bud.id).delete()
    for ln in lines:
        a = accts.get(ln.get("account_id"))
        if a is None or a.type not in ("income", "expense"):
            raise HTTPException(422, "Budget lines are for income and expense accounts")
        amounts = ln.get("amounts") or []
        if len(amounts) != 12:
            raise HTTPException(422, "Give 12 monthly amounts for every budget line")
        for i, v in enumerate(amounts):
            v = money(v or 0)
            if v < 0:
                raise HTTPException(422, "Budget amounts cannot be negative")
            if v:
                db.add(AccBudgetLine(budget_id=bud.id, account_id=a.id, month=i + 1, amount=v))
    db.flush()


def lines_from_actuals(db, tenant_id, start: date, growth_pct=0, branch_id=None) -> list[dict]:
    """Last year's actual income and expenses per month, grown by a percentage."""
    prev = date(start.year - 1, start.month, 1)
    months = fy_months(prev)
    factor = D(1) + D(growth_pct or 0) / D(100)
    out = defaultdict(lambda: [0.0] * 12)
    accts = {a.id: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == tenant_id,
                                                              ChartOfAccount.type.in_(("income", "expense")))}
    for i, m in enumerate(months):
        for aid, (dr, cr) in L.movements(db, tenant_id, m, L.month_end(m), branch_id).items():
            a = accts.get(aid)
            if a:
                out[aid][i] = float(money(L.natural(a, dr, cr) * factor))
    return [{"account_id": aid, "amounts": v} for aid, v in out.items() if any(v)]


def budget_vs_actual(db, bud: AccBudget, settings, through: date | None = None) -> dict:
    start = date(bud.fiscal_year, settings.fiscal_year_start_month or 1, 1)
    months = fy_months(start)
    through = through or L.today()
    accts = {a.id: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == bud.tenant_id)}
    budget = defaultdict(lambda: [ZERO] * 12)
    for ln in db.query(AccBudgetLine).filter(AccBudgetLine.budget_id == bud.id):
        budget[ln.account_id][ln.month - 1] = D(ln.amount)
    actual = defaultdict(lambda: [ZERO] * 12)
    n_months = 0
    for i, m in enumerate(months):
        if m > through:
            break
        n_months = i + 1
        for aid, (dr, cr) in L.movements(db, bud.tenant_id, m, min(L.month_end(m), through), bud.branch_id).items():
            a = accts.get(aid)
            if a and a.type in ("income", "expense"):
                actual[aid][i] = L.natural(a, dr, cr)
    rows = []
    for aid in sorted(set(budget) | set(actual), key=lambda x: accts[x].code):
        a = accts[aid]
        b_ytd = sum(budget[aid][:n_months], ZERO)
        a_ytd = sum(actual[aid][:n_months], ZERO)
        var = a_ytd - b_ytd
        good = var >= 0 if a.type == "income" else var <= 0
        rows.append({"account_id": aid, "code": a.code, "name": a.name, "type": a.type,
                     "budget": [float(x) for x in budget[aid]], "actual": [float(x) for x in actual[aid]],
                     "budget_ytd": float(b_ytd), "actual_ytd": float(a_ytd), "variance": float(var),
                     "variance_pct": round(float(var / b_ytd * 100), 1) if b_ytd else None,
                     "favourable": good, "budget_year": float(sum(budget[aid], ZERO))})

    def tot(kind, key):
        return round(sum(r[key] for r in rows if r["type"] == kind), 2)
    return {"budget": {"id": bud.id, "name": bud.name, "fiscal_year": bud.fiscal_year, "branch_id": bud.branch_id},
            "months": [m.strftime("%b %Y") for m in months], "months_to_date": n_months, "rows": rows,
            "totals": {k: {"budget_ytd": tot(k, "budget_ytd"), "actual_ytd": tot(k, "actual_ytd")}
                       for k in ("income", "expense")}}


# =========================================================================== month-end
def _period_ref(prefix, end: date) -> str:
    return f"{prefix}-{end:%Y-%m}"


def _posted_ref(db, tenant_id, source_type, ref):
    return (db.query(AccJournal).filter(AccJournal.tenant_id == tenant_id, AccJournal.source_type == source_type,
                                        AccJournal.reference == ref, AccJournal.status == "posted").first())


def _dpd(loan: Loan, on: date) -> int:
    return max(0, (on - loan.due_date).days) if loan.due_date else 0


def provision_preview(db, tenant_id, period_end: date) -> dict:
    b = L.Books(db, tenant_id)
    cfg = db.query(EclProvisionConfig).filter(EclProvisionConfig.tenant_id == tenant_id).first()
    rates = {1: D(cfg.stage1_rate) if cfg else D("0.01"), 2: D(cfg.stage2_rate) if cfg else D("0.20"),
             3: D(cfg.stage3_rate) if cfg else D("0.60")}
    book = b.account("loan_book")
    per_loan = dict(db.query(AccJournalLine.loan_id, func.sum(AccJournalLine.debit - AccJournalLine.credit))
                    .join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
                    .filter(AccJournalLine.tenant_id == tenant_id, AccJournalLine.account_id == book.id,
                            AccJournalLine.loan_id.isnot(None), AccJournal.status.in_(L.COUNTED),
                            AccJournal.entry_date <= period_end)
                    .group_by(AccJournalLine.loan_id).all())
    stages = {1: ZERO, 2: ZERO, 3: ZERO}
    counts = {1: 0, 2: 0, 3: 0}
    loans = {ln.id: ln for ln in db.query(Loan).filter(Loan.id.in_([k for k, v in per_loan.items() if v and v > 0] or [-1]))}
    for lid, bal in per_loan.items():
        bal = money(bal or 0)
        if bal <= 0 or lid not in loans:
            continue
        ln = loans[lid]
        dpd = _dpd(ln, period_end)
        st = 3 if (ln.status == "defaulted" or dpd > 90) else 2 if dpd > 30 else 1
        stages[st] += bal
        counts[st] += 1
    required = money(sum((money(stages[s] * rates[s]) for s in stages), ZERO))
    current = -L.account_balance(db, tenant_id, b.account("provision").id, period_end)   # credit balance
    return {"period_end": period_end.isoformat(),
            "stages": [{"stage": s, "loans": counts[s], "exposure": float(stages[s]), "rate": float(rates[s]),
                        "provision": float(money(stages[s] * rates[s]))} for s in (1, 2, 3)],
            "required": float(required), "current": float(current), "adjustment": float(money(required - current)),
            "already_posted": _posted_ref(db, tenant_id, "provision", _period_ref("PROV", period_end)) is not None}


def post_provision(db, tenant_id, period_end: date, user) -> AccJournal | None:
    period_end = L.month_end(period_end)
    ref = _period_ref("PROV", period_end)
    if _posted_ref(db, tenant_id, "provision", ref):
        raise HTTPException(409, f"The loan-loss provision for {period_end:%B %Y} is already posted. "
                                 "Reverse it first to post it again.")
    p = provision_preview(db, tenant_id, period_end)
    adj = money(p["adjustment"])
    if adj == 0:
        return None
    b = L.Books(db, tenant_id)
    desc = f"Loan loss provision (IFRS 9) for {period_end:%B %Y}"
    return L.post_journal(db, tenant_id, entry_date=period_end, description=desc,
                          lines=[L.line(b.account("impairment"), debit=adj, description=desc),
                                 L.line(b.account("provision"), credit=adj, description=desc)],
                          kind="system", source_type="provision", user=user, reference=ref, settings=b.settings)


def accrual_preview(db, tenant_id, period_end: date) -> dict:
    rows, total = [], ZERO
    received = dict(db.query(Repayment.loan_id, func.coalesce(func.sum(Repayment.interest_component), 0))
                    .filter(Repayment.tenant_id == tenant_id,
                            Repayment.payment_date < datetime.combine(period_end + timedelta(days=1),
                                                                      datetime.min.time()) - L.NAIROBI)
                    .group_by(Repayment.loan_id).all())
    for loan in (db.query(Loan).filter(Loan.tenant_id == tenant_id, Loan.status.in_(("active", "overdue")),
                                       Loan.disbursement_date.isnot(None), Loan.disbursement_date <= period_end)):
        if _dpd(loan, period_end) > 90:
            continue                           # non-performing: no interest is accrued
        snap = loan_snapshot(loan, loan.product) if loan.product is not None else {}
        interest = money(snap.get("interest", 0) or 0)
        term = (loan.due_date - loan.disbursement_date).days if loan.due_date else 0
        if interest <= 0 or term <= 0:
            continue
        earned = money(interest * D(min(1, max(0, (period_end - loan.disbursement_date).days) / term)))
        due = money(earned - D(received.get(loan.id, 0) or 0))
        if due > 0:
            rows.append({"loan_id": loan.id, "account_number": loan.account_number, "amount": float(due),
                         "branch_id": loan.branch_id})
            total += due
    return {"period_end": period_end.isoformat(), "loans": len(rows), "total": float(total), "rows": rows,
            "already_posted": _posted_ref(db, tenant_id, "interest_accrual", _period_ref("ACCR", period_end)) is not None}


def post_accrual(db, tenant_id, period_end: date, user):
    period_end = L.month_end(period_end)
    ref = _period_ref("ACCR", period_end)
    if _posted_ref(db, tenant_id, "interest_accrual", ref):
        raise HTTPException(409, f"Accrued interest for {period_end:%B %Y} is already posted")
    p = accrual_preview(db, tenant_id, period_end)
    if not p["rows"]:
        return None
    b = L.Books(db, tenant_id)
    desc = f"Interest earned but not yet paid, {period_end:%B %Y}"
    lines = []
    for r in p["rows"]:
        lines.append(L.line(b.account("interest_receivable"), debit=r["amount"], loan_id=r["loan_id"],
                            branch_id=r["branch_id"], description=r["account_number"]))
    lines.append(L.line(b.account("interest_income"), credit=p["total"], description=desc))
    j = L.post_journal(db, tenant_id, entry_date=period_end, description=desc, lines=lines, kind="system",
                       source_type="interest_accrual", user=user, reference=ref, settings=b.settings)
    rev_lines = [L.Line(account_id=ln.account_id, debit=D(ln.credit), credit=D(ln.debit), loan_id=ln.loan_id,
                        branch_id=ln.branch_id, description=ln.description)
                 for ln in db.query(AccJournalLine).filter(AccJournalLine.journal_id == j.id)]
    L.post_journal(db, tenant_id, entry_date=period_end + timedelta(days=1),
                   description=f"Reversal of accrued interest for {period_end:%B %Y} (it is booked when paid)",
                   lines=rev_lines, kind="system", source_type="interest_accrual_reversal", user=user,
                   reference=ref, settings=b.settings)
    return j


def close_checklist(db, tenant_id, period_end: date) -> dict:
    from app.services import books_sources
    period_end = L.month_end(period_end)
    s = L.ensure_books(db, tenant_id)
    waiting = books_sources.pending_counts(db, tenant_id)
    pend_j = db.query(func.count(AccJournal.id)).filter(AccJournal.tenant_id == tenant_id,
                                                        AccJournal.status == "pending",
                                                        AccJournal.entry_date <= period_end).scalar() or 0
    pend_b = db.query(func.count(AccBill.id)).filter(AccBill.tenant_id == tenant_id, AccBill.status == "pending",
                                                     AccBill.bill_date <= period_end).scalar() or 0
    pend_p = db.query(func.count(AccPaymentOut.id)).filter(AccPaymentOut.tenant_id == tenant_id,
                                                           AccPaymentOut.status == "pending",
                                                           AccPaymentOut.paid_on <= period_end).scalar() or 0
    pend_c = db.query(func.count(AccExpenseClaim.id)).filter(AccExpenseClaim.tenant_id == tenant_id,
                                                             AccExpenseClaim.status == "submitted",
                                                             AccExpenseClaim.claim_date <= period_end).scalar() or 0
    has_assets = db.query(AccAsset.id).filter(AccAsset.tenant_id == tenant_id, AccAsset.status == "active",
                                              AccAsset.in_service_date <= period_end).first() is not None
    dep_done = db.query(AccDepreciationRun.id).filter(AccDepreciationRun.tenant_id == tenant_id,
                                                      AccDepreciationRun.period == period_end).first() is not None
    unrec = []
    for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == tenant_id, ChartOfAccount.is_cash.is_(True),
                                             ChartOfAccount.active.is_(True)):
        n = (db.query(func.count(AccBankLine.id)).filter(AccBankLine.tenant_id == tenant_id, AccBankLine.account_id == a.id,
                                                         AccBankLine.status == "unmatched",
                                                         AccBankLine.txn_date <= period_end).scalar() or 0)
        has_st = db.query(AccBankStatement.id).filter(AccBankStatement.tenant_id == tenant_id,
                                                      AccBankStatement.account_id == a.id).first() is not None
        unrec.append({"account_id": a.id, "code": a.code, "name": a.name, "statement_lines_unmatched": int(n),
                      "has_statements": has_st})
    tb = L.trial_balance(db, tenant_id, L.fy_start(period_end, s.fiscal_year_start_month), period_end)
    items = [
        {"key": "auto", "label": "Loans and payments posted to the books",
         "ok": waiting["loans"] == 0 and waiting["repayments"] == 0,
         "detail": f"{waiting['loans']} loans and {waiting['repayments']} payments waiting"},
        {"key": "approvals", "label": "Nothing waiting for approval",
         "ok": not (pend_j or pend_b or pend_p or pend_c),
         "detail": f"{pend_j} journals, {pend_b} bills, {pend_p} payments, {pend_c} expense claims waiting"},
        {"key": "depreciation", "label": "Depreciation run for the month", "ok": dep_done or not has_assets,
         "detail": "done" if dep_done else ("no assets" if not has_assets else "not run yet")},
        {"key": "provision", "label": "Loan loss provision posted",
         "ok": _posted_ref(db, tenant_id, "provision", _period_ref("PROV", period_end)) is not None, "detail": ""},
        {"key": "accrual", "label": "Accrued interest posted (optional)",
         "ok": _posted_ref(db, tenant_id, "interest_accrual", _period_ref("ACCR", period_end)) is not None,
         "optional": True, "detail": ""},
        {"key": "reconciled", "label": "Bank and M-Pesa statements matched",
         "ok": all(u["statement_lines_unmatched"] == 0 for u in unrec),
         "detail": ", ".join(f"{u['code']}: {u['statement_lines_unmatched']} unmatched" for u in unrec
                             if u["statement_lines_unmatched"]) or "all matched"},
        {"key": "trial_balance", "label": "Trial balance balances", "ok": tb["balanced"],
         "detail": f"debits {tb['total_debit']:,.2f} / credits {tb['total_credit']:,.2f}"},
    ]
    return {"period_end": period_end.isoformat(), "lock_date": s.lock_date.isoformat() if s.lock_date else None,
            "closed": bool(s.lock_date and s.lock_date >= period_end), "items": items,
            "ready": all(i["ok"] for i in items if not i.get("optional")), "accounts": unrec}
