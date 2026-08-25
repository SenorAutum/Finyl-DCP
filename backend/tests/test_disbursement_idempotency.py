"""
Disbursement idempotency + the async B2C state machine.

Covers:
  * execute_disbursement is guarded by an atomic approved->processing UPDATE, so
    calling it twice for the same loan pays out exactly ONCE (the second call is
    rejected with 409). This is also the logical concurrency / row-lock guard
    test: two racing callers cannot both move the money.
  * apply_b2c_result is idempotent — a repeated result delivery is a safe no-op.
  * A failed result reverts the loan to `approved` for retry.

External Daraja I/O is stubbed (mock_b2c fixture) — deterministic, no network.
"""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models import PaymentTransaction, Loan
from app.services.disbursement import execute_disbursement, apply_b2c_result


def _txns_for(db, loan_id):
    return db.query(PaymentTransaction).filter(
        PaymentTransaction.loan_id == loan_id,
        PaymentTransaction.type == "b2c").all()


def test_disbursement_pays_out_once(seed, mock_b2c):
    db = seed.db
    loan = seed.make_loan(status="approved", principal="10000.00")

    result = execute_disbursement(db, seed.tenant.id, loan, seed.maker.id)
    db.commit()

    assert result["status"] == "processing"
    assert loan.status == "processing"            # NOT active yet (async payout)
    assert len(mock_b2c.calls) == 1               # exactly one payout attempted
    assert len(_txns_for(db, loan.id)) == 1       # exactly one transaction row


def test_second_disbursement_rejected_409(seed, mock_b2c):
    """Idempotency / lock guard: the second attempt loses the atomic race and is
    rejected, so no double payout and still only one transaction."""
    db = seed.db
    loan = seed.make_loan(status="approved", principal="10000.00")

    execute_disbursement(db, seed.tenant.id, loan, seed.maker.id)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        execute_disbursement(db, seed.tenant.id, loan, seed.maker.id)
    assert exc.value.status_code == 409
    db.rollback()

    assert len(mock_b2c.calls) == 1               # still ONE payout
    assert len(_txns_for(db, loan.id)) == 1       # still ONE transaction


def test_apply_b2c_result_success_then_idempotent(seed, mock_b2c):
    db = seed.db
    loan = seed.make_loan(status="approved", principal="10000.00",
                          interest_rate="10.0")
    execute_disbursement(db, seed.tenant.id, loan, seed.maker.id)
    db.commit()
    txn = _txns_for(db, loan.id)[0]
    assert txn.status == "processing"

    # First definitive result: settled OK -> loan active, balance = total_due.
    out1 = apply_b2c_result(db, seed.tenant.id, txn, "0", "RCPT123")
    db.commit()
    assert out1["applied"] is True
    assert txn.status == "success"
    assert txn.mpesa_ref == "RCPT123"
    db.refresh(loan)
    assert loan.status == "active"
    assert loan.outstanding_balance == Decimal("11000.00")   # total_due

    # Repeat delivery of the same result -> no-op (idempotent).
    out2 = apply_b2c_result(db, seed.tenant.id, txn, "0", "RCPT-DUP")
    db.commit()
    assert out2["applied"] is False
    db.refresh(loan)
    assert txn.status == "success"
    assert txn.mpesa_ref == "RCPT123"                        # unchanged
    assert loan.outstanding_balance == Decimal("11000.00")   # not doubled


def test_apply_b2c_result_failure_reverts_to_approved(seed, mock_b2c):
    db = seed.db
    loan = seed.make_loan(status="approved", principal="10000.00")
    execute_disbursement(db, seed.tenant.id, loan, seed.maker.id)
    db.commit()
    txn = _txns_for(db, loan.id)[0]

    out = apply_b2c_result(db, seed.tenant.id, txn, "2001", None)
    db.commit()
    assert out["applied"] is True
    assert out["status"] == "failed"
    db.refresh(loan)
    assert txn.status == "failed"
    assert loan.status == "approved"     # reverted so it can be retried
