"""
WP5 reproductions of the review's payout findings, written against the code as
merged on main (fc35a3c). Each test asserts the CORRECT behaviour, so on the old
code it fails and shows the defect; after WP5 it passes and guards against it.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models import Loan, PaymentTransaction, PendingApproval, Repayment
from app.services.disbursement import (apply_b2c_result, execute_disbursement,
                                       execute_refund, mark_b2c_timeout)


def _approved(seed):
    loan = seed.make_loan(status="approved", principal="10000.00", interest_rate="10.0")
    seed.db.commit()
    return loan


def test_f06_rollback_after_accepted_payout_cannot_pay_twice(seed, mock_b2c):
    """F06: provider accepted the payout, then the request's local transaction
    rolled back (e.g. a later error). A second attempt must NOT call the
    provider again while the first payout's outcome is unknown."""
    db = seed.db
    loan = _approved(seed)
    execute_disbursement(db, seed.tenant.id, loan, seed.maker.id)
    db.rollback()                                   # request failed after the call
    db.refresh(loan)
    try:
        execute_disbursement(db, seed.tenant.id, loan, seed.checker.id)
        db.commit()
    except HTTPException:
        db.rollback()
    assert len(mock_b2c.calls) == 1, "the payout provider was called twice for one loan"


def test_f08_timeout_then_success_activates_the_loan(seed, mock_b2c):
    db = seed.db
    loan = _approved(seed)
    execute_disbursement(db, seed.tenant.id, loan, seed.maker.id)
    db.commit()
    txn = db.query(PaymentTransaction).filter(PaymentTransaction.loan_id == loan.id,
                                              PaymentTransaction.type == "b2c").one()
    mark_b2c_timeout(db, txn)
    db.commit()
    apply_b2c_result(db, seed.tenant.id, txn, 0, "RCPT123")
    db.commit()
    db.refresh(loan)
    db.refresh(txn)
    assert txn.status == "success"
    assert loan.status == "active"


def test_f07_two_refunds_cannot_reserve_the_same_overpayment(seed):
    from app.routers.payments import refund_payment
    from app.schemas import RefundRequest
    db = seed.db
    loan = seed.make_loan(status="paid", principal="10000.00", interest_rate="10.0",
                          outstanding_balance="0.00")
    db.add(Repayment(tenant_id=seed.tenant.id, loan_id=loan.id, amount=Decimal("11200"),
                     payment_date=date(2026, 10, 1), method="mpesa_c2b", mpesa_ref="OVR200"))
    db.commit()
    refund_payment(RefundRequest(loan_id=loan.id, amount=200), tenant_id=seed.tenant.id,
                   db=db, user=seed.recon, request=None)
    with pytest.raises(HTTPException):
        refund_payment(RefundRequest(loan_id=loan.id, amount=200), tenant_id=seed.tenant.id,
                       db=db, user=seed.recon, request=None)
    reserved = sum(Decimal(str(p.amount)) for p in db.query(PendingApproval)
                   .filter(PendingApproval.loan_id == loan.id).all())
    assert reserved == Decimal("200.00")


def test_refund_is_not_success_on_acknowledgement(seed, monkeypatch):
    """Section 7 item 3: a live B2C acknowledgement (ResponseCode 0 = 'request
    accepted') was recorded as a completed refund before Safaricom's result."""
    import app.services.mpesa as mpesa

    def _live_ack(phone, amount, remarks, creds=None):
        return {"request": {}, "response": {"ConversationID": "AG_X", "ResponseCode": "0"},
                "result": {"ResultCode": "0", "ConversationID": "AG_X",
                           "TransactionReceipt": "AG_X"}}
    monkeypatch.setattr(mpesa, "b2c_disburse", _live_ack)
    out = execute_refund(seed.db, seed.tenant.id, 200, "0712345678", None, seed.recon.id, "t")
    assert out["status"] == "processing"


def test_f25_integrity_check_runs(seed):
    from app.routers.access import integrity_check
    loan = seed.make_loan(status="active", principal="10000.00", interest_rate="10.0",
                          outstanding_balance="11000.00")
    seed.db.commit()
    out = integrity_check(tenant_id=seed.tenant.id, actor=seed.recon, db=seed.db)
    assert out["checks"]["total_loans_checked"] >= 1
    assert loan.id


def test_f26_overpayment_cash_counted_once_in_gl(seed):
    from app.routers.accounting import _build_gl_lines
    from app.services import repayments as rp
    db = seed.db
    loan = seed.make_loan(status="active", principal="10000.00", interest_rate="10.0",
                          outstanding_balance="11000.00")
    db.commit()
    locked = db.query(Loan).filter(Loan.id == loan.id).with_for_update().one()
    rp.apply_payment(db, tenant_id=seed.tenant.id, loan=locked, amount="11200", ref="GL1",
                     method="mpesa_c2b", send_receipt=False)
    db.commit()
    lines = _build_gl_lines(db, seed.tenant.id, date(2000, 1, 1), date(2100, 1, 1),
                            {"1010": "Cash"})
    cash_in = sum(Decimal(str(l["debit"])) for l in lines if l["account_code"] == "1010")
    assert cash_in == Decimal("11200.00")
