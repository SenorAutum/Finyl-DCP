"""
Repayment reconciliation + suspense-account allocation.

Covers:
  * reconcile_payment splits a receipt into interest/principal (proportional
    Decimal split) and reduces the outstanding balance.
  * A duplicate M-Pesa receipt (same mpesa_ref) is ignored — no double-credit.
  * Unmatched money is parked in the suspense account; recording is idempotent on
    the receipt so a duplicate callback does not create a second suspense row.
  * Allocating an open suspense entry to a loan applies exactly once (idempotent).

All calls invoke the router functions directly with a test session (no HTTP).
"""
from decimal import Decimal

from app.models import Repayment, SuspenseEntry
from app.routers.payments import reconcile_payment, _record_suspense, allocate_suspense
from app.schemas import ReconcileRequest, SuspenseAllocateIn


def _repayments(db, loan_id):
    return db.query(Repayment).filter(Repayment.loan_id == loan_id).all()


def test_reconcile_splits_and_reduces_balance(seed):
    db = seed.db
    loan = seed.make_loan(status="active", principal="10000.00",
                          interest_rate="10.0", outstanding_balance="11000.00")

    out = reconcile_payment(
        body=ReconcileRequest(loan_id=loan.id, amount=1100.0, mpesa_ref="RCN1"),
        tenant_id=seed.tenant.id, db=db, user=seed.recon, request=None)

    assert out["status"] == "reconciled"
    reps = _repayments(db, loan.id)
    assert len(reps) == 1
    rep = reps[0]
    # 1100 @ 10% flat: interest_share = 10/110 -> 100.00 interest, 1000.00 principal
    assert rep.interest_component == Decimal("100.00")
    assert rep.principal_component == Decimal("1000.00")
    assert rep.interest_component + rep.principal_component == Decimal("1100.00")
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("9900.00")


def test_duplicate_receipt_not_double_credited(seed):
    db = seed.db
    loan = seed.make_loan(status="active", principal="10000.00",
                          interest_rate="10.0", outstanding_balance="11000.00")

    first = reconcile_payment(
        body=ReconcileRequest(loan_id=loan.id, amount=1100.0, mpesa_ref="DUP1"),
        tenant_id=seed.tenant.id, db=db, user=seed.recon, request=None)
    assert first["status"] == "reconciled"
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("9900.00")

    # Same receipt again -> ignored, balance unchanged, still ONE repayment.
    second = reconcile_payment(
        body=ReconcileRequest(loan_id=loan.id, amount=1100.0, mpesa_ref="DUP1"),
        tenant_id=seed.tenant.id, db=db, user=seed.recon, request=None)
    assert second["status"] == "duplicate_ignored"
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("9900.00")
    assert len(_repayments(db, loan.id)) == 1


def test_full_repayment_marks_loan_paid(seed):
    db = seed.db
    loan = seed.make_loan(status="active", principal="10000.00",
                          interest_rate="10.0", outstanding_balance="11000.00")
    out = reconcile_payment(
        body=ReconcileRequest(loan_id=loan.id, amount=11000.0, mpesa_ref="FULL1"),
        tenant_id=seed.tenant.id, db=db, user=seed.recon, request=None)
    assert out["loan_status"] == "paid"
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("0.00")
    assert loan.status == "paid"


def test_record_suspense_idempotent_on_ref(seed):
    db = seed.db
    e1 = _record_suspense(db, tenant_id=seed.tenant.id, reason="unmatched",
                          amount=500, ref="SUSP-REF-1", phone="0712345678",
                          source="c2b")
    db.commit()
    e2 = _record_suspense(db, tenant_id=seed.tenant.id, reason="unmatched",
                          amount=500, ref="SUSP-REF-1", phone="0712345678",
                          source="c2b")
    db.commit()
    assert e1.id == e2.id                                    # same row returned
    rows = db.query(SuspenseEntry).filter(
        SuspenseEntry.tenant_id == seed.tenant.id,
        SuspenseEntry.mpesa_ref == "SUSP-REF-1").all()
    assert len(rows) == 1                                    # no duplicate parked
    assert rows[0].status == "open"
    assert rows[0].amount == Decimal("500.00")


def test_allocate_suspense_applies_once(seed):
    db = seed.db
    loan = seed.make_loan(status="active", principal="10000.00",
                          interest_rate="10.0", outstanding_balance="11000.00")
    entry = _record_suspense(db, tenant_id=seed.tenant.id, reason="overpayment",
                             amount=1100, ref="ALLOC-1", phone="0712345678",
                             source="c2b")
    db.commit()

    out = allocate_suspense(
        entry.id, SuspenseAllocateIn(loan_id=loan.id), None,
        tenant_id=seed.tenant.id, db=db, user=seed.recon)
    assert out["status"] == "allocated"
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("9900.00")
    assert len(_repayments(db, loan.id)) == 1

    # Allocating the same (now resolved) entry again -> no-op.
    out2 = allocate_suspense(
        entry.id, SuspenseAllocateIn(loan_id=loan.id), None,
        tenant_id=seed.tenant.id, db=db, user=seed.recon)
    assert out2["status"] == "already_resolved"
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("9900.00")     # unchanged
    assert len(_repayments(db, loan.id)) == 1                 # no second repayment
