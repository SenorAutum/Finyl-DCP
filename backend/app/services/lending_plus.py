"""WP16 — lending additions.

Loan actions (one person asks, another approves — nobody approves their own):
  writeoff    the loan leaves the loan book (Dr Loan write-offs, Cr Loans receivable
              and fees receivable). The balance is still owed: payments that come in
              afterwards are booked as recoveries (Cr Recoveries on written-off loans).
  waiver      part of the interest or fees owed is forgiven; the balance drops.
  reschedule  a new, later due date (and optionally a rescheduling fee added to the
              balance); an overdue loan becomes current again. The loan is flagged
              as rescheduled for CBK reporting.
Top-up        a new loan for the same client whose payout first settles the old loan
              (the new loan goes through the normal approval and payout).
Collateral    what secures a loan, with values, insurance and its status.
Groups        borrowers who guarantee each other (chamas), their meetings and loans.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func

from app.core.money import D, money
from app.models import (AccJournal, AccJournalLine, Borrower, LendingGroup, LendingGroupMember, Loan, LoanAction,
                        Product, Repayment, Tenant)
from app.services import ledger as L
from app.services import repayments as rp

ACTION_KINDS = ("writeoff", "waiver", "reschedule")
WRITEOFF_STATUSES = ("active", "overdue", "defaulted")
PERM_FOR = {"writeoff": "loans.writeoff", "reschedule": "loans.writeoff", "waiver": "loans.waive"}


def _ledger_balance(db, tenant_id, account_id, loan_id) -> D:
    v = (db.query(func.coalesce(func.sum(AccJournalLine.debit - AccJournalLine.credit), 0))
         .join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
         .filter(AccJournalLine.tenant_id == tenant_id, AccJournalLine.account_id == account_id,
                 AccJournalLine.loan_id == loan_id, AccJournal.status.in_(L.COUNTED)).scalar())
    return money(v or 0)


def check_request(db, loan: Loan, kind: str, details: dict) -> dict:
    """Validate an action and return its cleaned details (amounts as strings)."""
    owed = rp.owed_breakdown(db, loan)
    if kind == "writeoff":
        if loan.status not in WRITEOFF_STATUSES:
            raise HTTPException(409, f"A {loan.status} loan cannot be written off")
        if money(loan.outstanding_balance or 0) <= 0:
            raise HTTPException(409, "Nothing is owed on this loan")
        return {"amount": str(money(loan.outstanding_balance)), "principal": str(owed["principal"]),
                "interest": str(owed["interest"]), "fees": str(owed["fees"])}
    if kind == "waiver":
        if loan.status not in rp.COLLECTIBLE_STATUSES:
            raise HTTPException(409, f"A {loan.status} loan cannot get a waiver")
        part = details.get("component")
        if part not in ("interest", "fees"):
            raise HTTPException(422, "Choose what to waive: interest or fees (penalties and charges)")
        amount = money(details.get("amount") or 0)
        if amount <= 0:
            raise HTTPException(422, "Enter the amount to waive")
        if amount > owed[part]:
            raise HTTPException(422, f"Only {owed[part]:,.2f} of {part} is owed")
        return {"component": part, "amount": str(amount), "owed_before": str(owed[part])}
    if kind == "reschedule":
        if loan.status not in ("active", "overdue", "defaulted"):
            raise HTTPException(409, f"A {loan.status} loan cannot be rescheduled")
        try:
            new_due = datetime.strptime(str(details.get("new_due_date")), "%Y-%m-%d").date()
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "Give the new due date (YYYY-MM-DD)")
        if new_due <= L.today() or (loan.due_date and new_due <= loan.due_date):
            raise HTTPException(422, "The new due date must be after today and after the current due date")
        if new_due > L.today() + timedelta(days=366):
            raise HTTPException(422, "Reschedule by at most a year at a time")
        fee = money(details.get("fee") or 0)
        if fee < 0:
            raise HTTPException(422, "The fee cannot be negative")
        return {"new_due_date": new_due.isoformat(), "old_due_date": loan.due_date.isoformat() if loan.due_date else None,
                "fee": str(fee), "old_status": loan.status}
    raise HTTPException(422, f"kind must be one of {', '.join(ACTION_KINDS)}")


def request_action(db, tenant_id, loan: Loan, kind: str, details: dict, reason: str, user) -> LoanAction:
    if db.query(LoanAction.id).filter(LoanAction.loan_id == loan.id, LoanAction.kind == kind,
                                      LoanAction.status == "pending").first():
        raise HTTPException(409, f"A {kind} request for this loan is already waiting for approval")
    clean = check_request(db, loan, kind, details or {})
    a = LoanAction(tenant_id=tenant_id, loan_id=loan.id, kind=kind, status="pending", details=clean,
                   reason=reason, requested_by_user_id=user.id)
    db.add(a)
    db.flush()
    return a


def decide(db, a: LoanAction, user, approve: bool, note: str | None):
    if a.status != "pending":
        raise HTTPException(409, f"This request is {a.status}")
    if user.role != "super_admin" and a.requested_by_user_id == user.id:
        raise HTTPException(403, "Someone other than the person who asked must decide this")
    a.decided_by_user_id, a.decided_at, a.decision_note = user.id, datetime.utcnow(), note
    if not approve:
        a.status = "rejected"
        return a
    loan = db.query(Loan).filter(Loan.id == a.loan_id, Loan.tenant_id == a.tenant_id).with_for_update().first()
    # Re-check against the loan as it is now (payments may have arrived since).
    fresh = check_request(db, loan, a.kind, a.details or {})
    if a.kind == "writeoff":
        _writeoff(db, a, loan, user)
    elif a.kind == "waiver":
        _waiver(db, a, loan, D(fresh["amount"]), fresh["component"], user)
    elif a.kind == "reschedule":
        _reschedule(db, a, loan, fresh)
    a.status = "done"
    db.flush()
    return a


def _post(db, loan, description, lines, source_type, source_id, user):
    b = L.Books(db, loan.tenant_id)
    lines = [ln for ln in lines if ln.debit or ln.credit]
    if not lines:
        return None
    return L.post_journal(db, loan.tenant_id, entry_date=L.today(), description=description, lines=lines,
                          kind="auto", source_type=source_type, source_id=source_id, user=user,
                          branch_id=loan.branch_id, reference=loan.account_number, settings=b.settings, auto_date=True)


def _writeoff(db, a: LoanAction, loan: Loan, user):
    amount = money(loan.outstanding_balance or 0)
    # Bring the books up to date for this loan first (payout and payments).
    from app.services import books_sources
    try:
        with db.begin_nested():
            books_sources.sync(db, loan.tenant_id, force=True)
    except Exception:  # noqa: BLE001 — the write-off itself must still go through
        pass
    b = L.Books(db, loan.tenant_id)
    tags = dict(loan_id=loan.id, borrower_id=loan.borrower_id, branch_id=loan.branch_id)
    principal = max(_ledger_balance(db, loan.tenant_id, b.account("loan_book").id, loan.id), L.ZERO)
    fees = max(_ledger_balance(db, loan.tenant_id, b.account("fees_receivable").id, loan.id), L.ZERO)
    desc = f"Loan {loan.account_number} written off"
    j = _post(db, loan, desc, [L.line(b.account("writeoff"), debit=principal + fees, description=desc, **tags),
                               L.line(b.account("loan_book"), credit=principal, description="Principal written off", **tags),
                               L.line(b.account("fees_receivable"), credit=fees, description="Fees written off", **tags)],
              "loan_writeoff", a.id, user)
    a.journal_id = j.id if j else None
    loan.status = "written_off"
    loan.written_off_at = datetime.utcnow()
    loan.written_off_amount = amount
    a.details = {**(a.details or {}), "written_off_in_books": str(money(principal + fees))}


def _waiver(db, a: LoanAction, loan: Loan, amount, part, user):
    if part == "interest":
        loan.interest_waived = money(D(loan.interest_waived or 0) + amount)
    else:
        loan.fees_waived = money(D(loan.fees_waived or 0) + amount)
        b = L.Books(db, loan.tenant_id)
        rec = max(_ledger_balance(db, loan.tenant_id, b.account("fees_receivable").id, loan.id), L.ZERO)
        take = min(rec, amount)
        if take > 0:
            # What was added to the balance was fees plus 20% excise duty on them:
            # forgiving it takes back both, in that proportion.
            from app.services.pricing import loan_snapshot
            snap = loan_snapshot(loan, loan.product) if loan.product is not None else (loan.pricing_snapshot or {})
            fees, excise = D(str(snap.get("total_fees", 0) or 0)), D(str(snap.get("excise_duty", 0) or 0))
            excise_part = money(take * excise / (fees + excise)) if fees + excise > 0 else L.ZERO
            tags = dict(loan_id=loan.id, borrower_id=loan.borrower_id, branch_id=loan.branch_id)
            desc = f"Fees waived on {loan.account_number}"
            j = _post(db, loan, desc, [L.line(b.account("fee_income"), debit=take - excise_part, description=desc, **tags),
                                       L.line(b.account("excise"), debit=excise_part, description="Excise on waived fees", **tags),
                                       L.line(b.account("fees_receivable"), credit=take, description=desc, **tags)],
                      "loan_waiver", a.id, user)
            a.journal_id = j.id if j else None
    loan.outstanding_balance = money(D(loan.outstanding_balance or 0) - amount)
    if loan.outstanding_balance <= 0 and loan.status in rp.COLLECTIBLE_STATUSES:
        loan.outstanding_balance = L.ZERO
        loan.status = "paid"


def _reschedule(db, a: LoanAction, loan: Loan, fresh: dict):
    loan.due_date = date.fromisoformat(fresh["new_due_date"])
    fee = D(fresh["fee"])
    if fee > 0:
        loan.outstanding_balance = money(D(loan.outstanding_balance or 0) + fee)
        loan.penalties_charged = money(D(loan.penalties_charged or 0) + fee)
    if loan.status in ("overdue", "defaulted"):
        loan.status = "active"
    loan.restructure_count = (loan.restructure_count or 0) + 1
    loan.restructured_at = datetime.utcnow()


# --------------------------------------------------------------------------- top-up
def topup_rules(db, tenant_id) -> dict:
    """At least half of the old loan must have been repaid before a top-up."""
    return {"min_repaid_pct": 50}


def create_topup(db, tenant_id, old: Loan, *, principal, product_id: int | None, user) -> Loan:
    if old.status != "active":
        raise HTTPException(409, "Only a current (active, not overdue) loan can be topped up")
    if db.query(Loan.id).filter(Loan.topup_of_loan_id == old.id,
                                Loan.status.in_(("pending", "underwriting", "approved", "processing"))).first():
        raise HTTPException(409, "A top-up for this loan is already in progress")
    total_due = money(D(old.principal) * (1 + D(old.interest_rate or 0) / 100) + D(old.fees_in_balance or 0))
    paid = money(db.query(func.coalesce(func.sum(Repayment.amount), 0)).filter(Repayment.loan_id == old.id).scalar() or 0)
    pct = float(paid / total_due * 100) if total_due > 0 else 0
    need = topup_rules(db, tenant_id)["min_repaid_pct"]
    if pct < need:
        raise HTTPException(409, f"The client has repaid {pct:.0f}% of this loan; a top-up needs at least {need}%")
    product = db.query(Product).filter(Product.id == (product_id or old.product_id), Product.tenant_id == tenant_id,
                                       Product.active).first()
    if product is None:
        raise HTTPException(404, "Product not found or inactive")
    principal = money(principal)
    if not (D(product.min_amount) <= principal <= D(product.max_amount)):
        raise HTTPException(422, f"Amount must be between {product.min_amount} and {product.max_amount}")
    from app.services.pricing import quote_breakdown
    quote = quote_breakdown(product, principal)
    payoff = money(rp.payoff_quote(db, old, datetime.utcnow())["payoff_amount"])
    if money(quote["payout_amount"]) <= payoff:
        raise HTTPException(422, f"The new loan's payout ({quote['payout_amount']:,.2f}) must be more than what is "
                                 f"owed on the old loan ({payoff:,.2f})")
    db.query(Tenant).filter(Tenant.id == tenant_id).with_for_update().first()
    seq = db.query(Loan).filter(Loan.tenant_id == tenant_id).count() + 1
    while True:
        account_number = f"FL/FY{date.today().year}/{tenant_id}/{seq}"
        if not db.query(Loan.id).filter(Loan.tenant_id == tenant_id, Loan.account_number == account_number).first():
            break
        seq += 1
    new = Loan(tenant_id=tenant_id, account_number=account_number, borrower_id=old.borrower_id, product_id=product.id,
               staff_id=old.staff_id, branch_id=old.branch_id, principal=principal, interest_rate=product.interest_rate,
               status="pending", application_date=date.today(), loan_cycle_number=old.loan_cycle_number or 1,
               outstanding_balance=0, pricing_snapshot=quote, topup_of_loan_id=old.id, group_id=old.group_id)
    db.add(new)
    db.flush()
    return new


def topup_cash(db, loan: Loan, payout) -> tuple:
    """For a top-up loan at payout: (cash to send, amount that settles the old loan)."""
    if not loan.topup_of_loan_id:
        return money(payout), L.ZERO
    old = db.get(Loan, loan.topup_of_loan_id)
    if old is None or old.status not in rp.COLLECTIBLE_STATUSES or money(old.outstanding_balance or 0) <= 0:
        return money(payout), L.ZERO
    settle = money(rp.payoff_quote(db, old, datetime.utcnow())["payoff_amount"])
    if settle >= money(payout):
        raise HTTPException(422, "The old loan now needs more than this top-up pays out: change the amount")
    return money(money(payout) - settle), settle


def settle_old_loan(db, tenant_id, loan: Loan):
    """After a top-up payout succeeded: settle the old loan from it (once)."""
    snap = loan.pricing_snapshot or {}
    settle = money(snap.get("topup_settlement") or 0)
    if not loan.topup_of_loan_id or settle <= 0:
        return None
    old = db.query(Loan).filter(Loan.id == loan.topup_of_loan_id, Loan.tenant_id == tenant_id).with_for_update().first()
    if old is None:
        return None
    return rp.apply_payment(db, tenant_id=tenant_id, loan=old, amount=settle, ref=f"TOPUP-{loan.id}", method="topup",
                            source="topup", send_receipt=False)


# --------------------------------------------------------------------------- groups
def group_summary(db, g: LendingGroup) -> dict:
    members = db.query(LendingGroupMember).filter(LendingGroupMember.group_id == g.id,
                                                  LendingGroupMember.active.is_(True)).all()
    ids = [m.borrower_id for m in members]
    loans = (db.query(Loan).filter(Loan.tenant_id == g.tenant_id, Loan.borrower_id.in_(ids or [-1]),
                                   Loan.status.in_(("active", "overdue", "defaulted", "written_off"))).all())
    today = L.today()
    late = [ln for ln in loans if ln.due_date and ln.due_date < today and money(ln.outstanding_balance or 0) > 0]
    out = sum((money(ln.outstanding_balance or 0) for ln in loans), L.ZERO)
    at_risk = sum((money(ln.outstanding_balance or 0) for ln in late), L.ZERO)
    return {"members": len(members), "open_loans": len(loans), "outstanding": float(out),
            "in_arrears": float(at_risk), "par_pct": round(float(at_risk / out * 100), 1) if out else 0.0,
            "late_members": len({ln.borrower_id for ln in late})}


def check_member(db, tenant_id, borrower_id, group_id=None):
    b = db.get(Borrower, borrower_id)
    if b is None or b.tenant_id != tenant_id:
        raise HTTPException(404, "Client not found")
    other = (db.query(LendingGroupMember).filter(LendingGroupMember.tenant_id == tenant_id,
                                                 LendingGroupMember.borrower_id == borrower_id,
                                                 LendingGroupMember.active.is_(True)).first())
    if other and other.group_id != group_id:
        g = db.get(LendingGroup, other.group_id)
        raise HTTPException(409, f"This client is already a member of {g.name if g else 'another group'}")
    return b
