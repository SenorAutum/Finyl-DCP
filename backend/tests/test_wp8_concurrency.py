"""
WP8 (review step 5) - two people (or two M-Pesa messages) at the same moment.

The earlier tests call things one after another in a single database session.
These start two real threads, each with its own database connection, released
at the same instant, the way two staff members or two copies of the server on
AWS would act. They prove the database locks hold, not just the code order.
"""
import threading
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.models import Loan, PaymentTransaction, PendingApproval, Repayment

pytestmark = pytest.mark.skipif(
    __import__("os").environ.get("TEST_DATABASE_URL", "").startswith("sqlite"),
    reason="needs PostgreSQL row locks")


def _race(db, fn, n=2):
    """Run fn(session) in n threads at once; returns (results, errors)."""
    Session = sessionmaker(bind=db.get_bind())
    gate = threading.Barrier(n)
    results, errors = [], []

    def worker():
        s = Session()
        try:
            gate.wait()
            results.append(fn(s))
            s.commit()
        except Exception as exc:              # noqa: BLE001 - the loser is expected to fail
            s.rollback()
            errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    db.expire_all()
    return results, errors


def test_two_disbursements_at_once_pay_out_once(seed, mock_b2c):
    from app.services.disbursement import execute_disbursement
    db = seed.db
    loan = seed.make_loan(status="approved", principal="10000.00")
    db.commit()

    def go(s):
        return execute_disbursement(s, seed.tenant.id, s.get(Loan, loan.id), seed.maker.id)

    results, errors = _race(db, go)
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], HTTPException) and errors[0].status_code == 409
    assert len(mock_b2c.calls) == 1
    assert db.query(PaymentTransaction).filter_by(loan_id=loan.id, type="b2c").count() == 1


def test_two_refund_requests_at_once_cannot_double_reserve(seed):
    from app.routers.payments import refund_payment
    from app.schemas import RefundRequest
    db = seed.db
    loan = seed.make_loan(status="paid", principal="10000.00", interest_rate="10.0",
                          outstanding_balance="0.00")
    db.add(Repayment(tenant_id=seed.tenant.id, loan_id=loan.id, amount=Decimal("11200"),
                     payment_date=date(2026, 10, 1), method="mpesa_c2b", mpesa_ref="OVRRACE"))
    db.commit()

    def go(s):
        return refund_payment(RefundRequest(loan_id=loan.id, amount=200),
                              tenant_id=seed.tenant.id, db=s, user=seed.recon, request=None)

    results, errors = _race(db, go)
    reserved = sum(Decimal(str(p.amount)) for p in
                   db.query(PendingApproval).filter(PendingApproval.loan_id == loan.id).all())
    assert reserved == Decimal("200.00"), f"reserved {reserved} against a KES 200 overpayment"
    assert len(results) == 1


def test_same_paybill_payment_delivered_twice_at_once_counts_once(seed):
    from app.routers.payments import _process_c2b_callback
    db = seed.db
    loan = seed.make_loan(status="active", principal="10000.00", outstanding_balance="11000.00")
    db.commit()
    body = {"TransactionType": "Pay Bill", "TransID": "QKRACE0001", "TransTime": "20261101120000",
            "TransAmount": "1000.00", "BusinessShortCode": "", "BillRefNumber": loan.account_number,
            "MSISDN": "254700000001", "FirstName": "Race"}

    results, errors = _race(db, lambda s: _process_c2b_callback(s, dict(body)))
    assert db.query(Repayment).filter(Repayment.mpesa_ref == "QKRACE0001").count() == 1
    assert Decimal(str(db.get(Loan, loan.id).outstanding_balance)) == Decimal("10000.00")


def test_payout_result_and_timeout_arriving_together_settle_once(seed, mock_b2c):
    from app.services.disbursement import apply_b2c_result, execute_disbursement, mark_b2c_timeout
    db = seed.db
    loan = seed.make_loan(status="approved", principal="10000.00")
    execute_disbursement(db, seed.tenant.id, loan, seed.maker.id)
    db.commit()
    txn_id = db.query(PaymentTransaction).filter_by(loan_id=loan.id, type="b2c").one().id

    turn = iter(["result", "timeout"])
    lock = threading.Lock()

    def go(s):
        with lock:
            which = next(turn)
        txn = s.query(PaymentTransaction).filter_by(id=txn_id).with_for_update().one()
        if which == "result":
            return apply_b2c_result(s, seed.tenant.id, txn, 0, "QKB2CRACE")
        return mark_b2c_timeout(s, txn)

    _race(db, go)
    txn = db.get(PaymentTransaction, txn_id)
    # Whatever order they land in, a confirmed success wins: the loan is paid out
    # and active, never left "processing" by a late timeout.
    assert txn.status == "success"
    assert db.get(Loan, loan.id).status == "active"
