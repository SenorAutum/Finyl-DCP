"""
WP2 — maker-checker for money coming IN (manual payments, suspense
allocations) and for suspense refunds.

Every manual action that changes a balance or sends money out is either applied
at once (only when the DCP has set a `manual_payment` threshold and the amount is
at or under it) or parked as a PendingApproval for a DIFFERENT authorised user to
approve in the Approvals inbox. With no threshold configured, everything is
parked (fail closed, like disbursements — rbac.requires_maker_checker).

Action types added to pending_approvals.action_type:
  manual_payment     -> approver needs reconcile.approve
  suspense_allocate  -> approver needs reconcile.approve
  suspense_refund    -> approver needs refund.approve (refunds are ALWAYS parked)
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException

from app.core import customer_number as cn
from app.core.money import money
from app.models import Borrower, Loan, PendingApproval, SuspenseEntry
from app.services import mpesa
from app.services import repayments as rp

MANUAL_THRESHOLD = "manual_payment"
MANUAL_CHANNELS = {"mpesa_paybill", "mpesa_till", "bank", "cash", "other"}
MANUAL_ACTIONS = ("manual_payment", "suspense_allocate", "suspense_refund")

# action_type -> (permission the checker needs, per-DCP approver type)
ACTION_RULES = {
    "disbursement": ("disburse.approve", "disbursement"),
    "refund": ("refund.approve", "refund"),
    "suspense_refund": ("refund.approve", "refund"),
    "manual_payment": ("reconcile.approve", "manual_payment"),
    "suspense_allocate": ("reconcile.approve", "manual_payment"),
}

_CLOCK_SKEW = timedelta(minutes=10)


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def clean_reference(value) -> str | None:
    s = "".join(str(value or "").split()).upper()
    return s[:40] or None


def check_paid_at(value) -> datetime:
    """When the customer paid. Must be given, and not in the future."""
    if value is None:
        raise HTTPException(422, "paid_at (when the customer paid) is required")
    if value.tzinfo is not None:
        value = (value - value.utcoffset()).replace(tzinfo=None)
    if value > datetime.utcnow() + _CLOCK_SKEW:
        raise HTTPException(422, "paid_at cannot be in the future")
    return value


def resolve_manual_loan(db, tenant_id: int, loan_id, customer_number) -> Loan:
    if loan_id:
        loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == tenant_id).first()
        if loan is None:
            raise HTTPException(404, "Loan not found")
        return loan
    if customer_number:
        num = cn.normalise(customer_number)
        if not cn.is_valid(num):
            raise HTTPException(422, "Customer number is not valid (check the digits)")
        borrower = (db.query(Borrower)
                    .filter(Borrower.tenant_id == tenant_id,
                            Borrower.customer_number == num).first())
        if borrower is None:
            raise HTTPException(404, "Customer not found")
        loans = (db.query(Loan)
                 .filter(Loan.tenant_id == tenant_id, Loan.borrower_id == borrower.id,
                         Loan.status.in_(rp.COLLECTIBLE_STATUSES)).all())
        if not loans:
            raise HTTPException(400, "This customer has no open loan")
        if len(loans) > 1:
            raise HTTPException(409, "This customer has more than one open loan — "
                                     "choose the loan by its ID")
        return loans[0]
    raise HTTPException(422, "Provide loan_id or customer_number")


def _pending_with_reference(db, tenant_id, ref) -> PendingApproval | None:
    if not ref:
        return None
    rows = (db.query(PendingApproval)
            .filter(PendingApproval.tenant_id == tenant_id,
                    PendingApproval.action_type == "manual_payment",
                    PendingApproval.status == "pending_approval").all())
    for p in rows:
        if (p.details or {}).get("reference") == ref:
            return p
    return None


def ensure_reference_free(db, tenant_id: int, ref: str | None) -> None:
    """409 when this transaction reference was already recorded for this DCP —
    as a repayment, a suspense receipt, or a manual entry awaiting approval."""
    if not ref:
        return
    used = rp.reference_already_used(db, tenant_id, ref)
    if used is not None:
        raise HTTPException(409, f"Reference {ref} has already been recorded ({used})")
    if _pending_with_reference(db, tenant_id, ref) is not None:
        raise HTTPException(409, f"Reference {ref} is already awaiting approval")


def _refund_phone(entry: SuspenseEntry, override) -> str:
    phone = mpesa.normalise_msisdn(override or entry.phone or "")
    if len(phone) != 12 or not (phone.startswith("2547") or phone.startswith("2541")):
        raise HTTPException(
            422, "No usable M-Pesa number for this refund (the payer's number may be "
                 "masked). Supply refund_phone after confirming it with the payer.")
    return phone


# --------------------------------------------------------------------------- #
# Maker side — park for approval
# --------------------------------------------------------------------------- #
def park_manual_payment(db, *, tenant_id, user, loan, amount, details, reason) -> PendingApproval:
    p = PendingApproval(tenant_id=tenant_id, action_type="manual_payment", loan_id=loan.id,
                        amount=money(amount), phone=details.get("payer_phone"),
                        reason=reason, status="pending_approval", maker_user_id=user.id,
                        maker_at=datetime.utcnow(),
                        details={**details, "account_number": loan.account_number})
    db.add(p)
    db.flush()
    return p


def park_suspense_allocation(db, *, tenant_id, user, entry, loan) -> PendingApproval:
    p = PendingApproval(tenant_id=tenant_id, action_type="suspense_allocate", loan_id=loan.id,
                        amount=money(entry.amount), phone=entry.phone,
                        reason=f"Allocate suspense #{entry.id}", status="pending_approval",
                        maker_user_id=user.id, maker_at=datetime.utcnow(),
                        details={"suspense_id": entry.id, "reference": entry.mpesa_ref,
                                 "bill_ref": entry.bill_ref, "payer_name": entry.payer_name,
                                 "account_number": loan.account_number})
    entry.status = "pending_approval"
    db.add(p)
    db.flush()
    return p


def park_suspense_refund(db, *, tenant_id, user, entry, refund_phone, note) -> PendingApproval:
    phone = _refund_phone(entry, refund_phone)
    override = bool(refund_phone) and phone != mpesa.normalise_msisdn(entry.phone or "")
    if override and not (note or "").strip():
        raise HTTPException(422, "A note is required when refunding to a number other "
                                 "than the one that paid")
    p = PendingApproval(tenant_id=tenant_id, action_type="suspense_refund",
                        loan_id=entry.matched_loan_id, amount=money(entry.amount), phone=phone,
                        reason=(note or f"Refund suspense #{entry.id}")[:200],
                        status="pending_approval", maker_user_id=user.id,
                        maker_at=datetime.utcnow(),
                        details={"suspense_id": entry.id, "reference": entry.mpesa_ref,
                                 "payer_phone": entry.phone, "phone_overridden": override,
                                 "note": note})
    entry.status = "pending_approval"
    db.add(p)
    db.flush()
    return p


# --------------------------------------------------------------------------- #
# Execution (immediate, or by the checker on approval)
# --------------------------------------------------------------------------- #
def apply_manual(db, *, tenant_id, loan_id, amount, details, actor_user_id) -> dict:
    loan = (db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == tenant_id)
            .with_for_update().first())
    if loan is None:
        raise HTTPException(404, "Loan not found")
    if loan.status not in rp.COLLECTIBLE_STATUSES:
        raise HTTPException(409, f"Loan is no longer collectible (status={loan.status})")
    paid_at = details.get("paid_at")
    paid_at = datetime.fromisoformat(paid_at) if isinstance(paid_at, str) else paid_at
    channel = details.get("channel") or "other"
    res = rp.apply_payment(db, tenant_id=tenant_id, loan=loan, amount=amount,
                           ref=details.get("reference"), method=f"manual_{channel}"[:20],
                           paid_at=paid_at, phone=details.get("payer_phone"), source="manual",
                           payer_name=details.get("payer_name"), actor_user_id=actor_user_id)
    return res.as_dict()


def allocate_entry(db, *, tenant_id, entry: SuspenseEntry, loan_id, resolver_user_id) -> dict:
    loan = (db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == tenant_id)
            .with_for_update().first())
    if loan is None:
        raise HTTPException(404, "Loan not found")
    if loan.status not in rp.COLLECTIBLE_STATUSES:
        raise HTTPException(409, f"Loan is no longer collectible (status={loan.status})")
    # Keep the M-Pesa code on the repayment when it is free; otherwise derive a
    # suspense-scoped reference so the idempotency guarantee still holds.
    ref = entry.mpesa_ref
    if not ref:
        ref = f"SUS-{entry.id}"
    elif rp.reference_already_used(db, tenant_id, ref, include_suspense=False):
        ref = f"SUS-{entry.id}-{ref}"[:30]
    res = rp.apply_payment(db, tenant_id=tenant_id, loan=loan, amount=entry.amount, ref=ref,
                           method="suspense_allocation", paid_at=entry.paid_at or entry.created_at,
                           phone=entry.phone, source="manual", bill_ref=entry.bill_ref,
                           payer_name=entry.payer_name, actor_user_id=resolver_user_id,
                           from_suspense=True)
    entry.status = "allocated"
    entry.matched_loan_id = loan.id
    entry.resolved_at = datetime.utcnow()
    entry.resolved_by_user_id = resolver_user_id
    return {"status": "allocated", "suspense_id": entry.id, "loan_id": loan.id,
            "applied": float(res.applied), "excess": float(res.excess),
            "excess_suspense_id": res.suspense_id,
            "outstanding_balance": float(loan.outstanding_balance or 0),
            "loan_status": loan.status}


def _locked_entry(db, tenant_id, p) -> SuspenseEntry:
    sid = (p.details or {}).get("suspense_id")
    entry = (db.query(SuspenseEntry)
             .filter(SuspenseEntry.id == sid, SuspenseEntry.tenant_id == tenant_id)
             .with_for_update().first())
    if entry is None:
        raise HTTPException(404, "Suspense entry not found")
    if entry.status != "pending_approval":
        raise HTTPException(409, f"Suspense entry is no longer awaiting approval "
                                 f"(status={entry.status})")
    return entry


def execute_approved(db, tenant_id: int, p: PendingApproval, checker) -> dict:
    """Run the side-effect of an approved manual-money action."""
    if p.action_type == "manual_payment":
        details = p.details or {}
        if rp.reference_already_used(db, tenant_id, details.get("reference")):
            raise HTTPException(409, "This reference was recorded by another route while "
                                     "awaiting approval — reject this request")
        return apply_manual(db, tenant_id=tenant_id, loan_id=p.loan_id, amount=p.amount,
                            details=details, actor_user_id=checker.id)
    if p.action_type == "suspense_allocate":
        entry = _locked_entry(db, tenant_id, p)
        return allocate_entry(db, tenant_id=tenant_id, entry=entry, loan_id=p.loan_id,
                              resolver_user_id=checker.id)
    if p.action_type == "suspense_refund":
        from app.services.disbursement import execute_refund
        entry = _locked_entry(db, tenant_id, p)
        result = execute_refund(db, tenant_id, float(p.amount), p.phone, p.loan_id, checker.id,
                                p.reason or f"Refund suspense #{entry.id}")
        entry.status = "refunded"
        entry.resolved_at = datetime.utcnow()
        entry.resolved_by_user_id = checker.id
        payload = dict(entry.raw_payload or {})
        payload["refund"] = {"pending_id": p.id, "phone": p.phone, "result": result,
                             "note": (p.details or {}).get("note")}
        entry.raw_payload = payload
        return {"status": "refunded", "suspense_id": entry.id, **result}
    raise HTTPException(400, f"Unsupported action type {p.action_type}")


def on_rejected(db, tenant_id: int, p: PendingApproval) -> None:
    """A rejected suspense action releases the entry back to `open`."""
    if p.action_type in ("suspense_allocate", "suspense_refund"):
        sid = (p.details or {}).get("suspense_id")
        entry = (db.query(SuspenseEntry)
                 .filter(SuspenseEntry.id == sid, SuspenseEntry.tenant_id == tenant_id)
                 .with_for_update().first())
        if entry is not None and entry.status == "pending_approval":
            entry.status = "open"
