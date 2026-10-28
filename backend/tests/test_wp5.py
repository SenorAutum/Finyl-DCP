"""
WP5 — payouts and money integrity: fee collection (F23), durable payouts and
manual resolution (F06), refund approval re-check (F07), refund results,
GL fees/excise/penalties (F26 follow-through), integrity check (F25).
"""
from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi import HTTPException

from app.models import Loan, PaymentTransaction, PendingApproval, Repayment
from app.routers import payments
from app.routers.accounting import _build_gl_lines
from app.routers.approvals import decide_pending_action
from app.schemas import ApprovalDecision, PayoutResolveIn
from app.services import disbursement as disb
from app.services import repayments as rp
from app.services import webhook_security as ws


def _product_fees(seed, collection):
    seed.product.rules = {"processing_fee_rate": 2.5, "facility_fee": 150,
                          "fee_collection": collection}
    seed.db.commit()


def _approved(seed):
    loan = seed.make_loan(status="approved", principal="10000.00", interest_rate="10.0")
    seed.db.commit()
    return loan


def _settle(seed, loan, receipt="RCPTOK1"):
    txn = (seed.db.query(PaymentTransaction)
           .filter(PaymentTransaction.loan_id == loan.id, PaymentTransaction.type == "b2c")
           .order_by(PaymentTransaction.id.desc()).first())
    body = {"Result": {"ResultCode": 0, "ConversationID": txn.mpesa_ref, "TransactionID": receipt}}
    payments._ingest_and_process(seed.db, ws.ENDPOINT_B2C_RESULT, body,
                                 payments._process_b2c_result, {"ResultCode": 0})
    seed.db.refresh(loan)
    seed.db.refresh(txn)
    return txn


def _gl_balanced(lines):
    dr = sum(Decimal(str(l["debit"])) for l in lines)
    cr = sum(Decimal(str(l["credit"])) for l in lines)
    return dr == cr


def _gl(seed, code):
    lines = _build_gl_lines(seed.db, seed.tenant.id, date(2000, 1, 1), date(2100, 1, 1), {})
    assert _gl_balanced(lines)
    return (sum(Decimal(str(l["debit"])) for l in lines if l["account_code"] == code),
            sum(Decimal(str(l["credit"])) for l in lines if l["account_code"] == code))


# --------------------------------------------------------------------------- #
# F23 — fee collection
# --------------------------------------------------------------------------- #
def test_deducted_fees_reduce_the_payout_not_the_balance(seed, mock_b2c):
    _product_fees(seed, "deduct")
    loan = _approved(seed)
    out = disb.execute_disbursement(seed.db, seed.tenant.id, loan, seed.maker.id)
    assert out["amount"] == 9520.0 and mock_b2c.calls[0]["amount"] == Decimal("9520.00")
    _settle(seed, loan)
    assert loan.status == "active"
    assert loan.outstanding_balance == Decimal("11000.00")
    # GL: receivable for the full principal, cash for what was sent, fee + excise.
    assert _gl(seed, "1000")[0] == Decimal("10000.00")
    assert _gl(seed, "1010")[1] == Decimal("9520.00")
    assert _gl(seed, "4010")[1] == Decimal("400.00")
    assert _gl(seed, "2000")[1] == Decimal("80.00")


def test_fees_added_to_balance_are_owed_and_repaid_as_fees(seed, mock_b2c):
    _product_fees(seed, "add_to_balance")
    loan = _approved(seed)
    out = disb.execute_disbursement(seed.db, seed.tenant.id, loan, seed.maker.id)
    assert out["amount"] == 10000.0
    _settle(seed, loan)
    assert loan.outstanding_balance == Decimal("11480.00")
    # Reg. 56(2): principal, then fees and charges, then interest.
    locked = seed.db.query(Loan).filter(Loan.id == loan.id).with_for_update().one()
    res = rp.apply_payment(seed.db, tenant_id=seed.tenant.id, loan=locked, amount="10300",
                           ref="FEEPAY1", method="test", send_receipt=False)
    seed.db.commit()
    assert res.components == {"principal": Decimal("10000.00"), "fees": Decimal("300.00"),
                              "interest": Decimal("0.00")}
    # GL: the 300 of fees splits into fee income and excise (80/480 of it).
    assert _gl(seed, "2000")[1] == Decimal("50.00")
    assert _gl(seed, "4010")[1] == Decimal("250.00")


def test_quote_matches_what_the_loan_charges(seed, mock_b2c):
    from app.routers.lending import _quote_breakdown
    _product_fees(seed, "add_to_balance")
    q = _quote_breakdown(seed.product, 10000)
    loan = _approved(seed)
    disb.execute_disbursement(seed.db, seed.tenant.id, loan, seed.maker.id)
    _settle(seed, loan)
    assert float(loan.outstanding_balance) == q["total_repayable"]


def test_loan_keeps_its_agreed_price_if_the_product_changes(seed, mock_b2c):
    from app.services import pricing
    _product_fees(seed, "deduct")
    loan = _approved(seed)
    loan.pricing_snapshot = pricing.quote_breakdown(seed.product, loan.principal)
    seed.db.commit()
    seed.product.rules = {"processing_fee_rate": 9, "fee_collection": "deduct"}  # changed later
    seed.db.commit()
    out = disb.execute_disbursement(seed.db, seed.tenant.id, loan, seed.maker.id)
    assert out["amount"] == 9520.0


# --------------------------------------------------------------------------- #
# F06 — unknown outcomes and manual resolution
# --------------------------------------------------------------------------- #
def _raise(exc):
    def _f(*a, **k):
        raise exc
    return _f


def test_unknown_outcome_blocks_retry_until_resolved(seed, monkeypatch, mock_b2c):
    import app.services.mpesa as mpesa
    loan = _approved(seed)
    monkeypatch.setattr(mpesa, "b2c_disburse", _raise(httpx.ReadTimeout("slow")))
    with pytest.raises(HTTPException) as exc:
        disb.execute_disbursement(seed.db, seed.tenant.id, loan, seed.maker.id)
    assert exc.value.status_code == 502
    txn = seed.db.query(PaymentTransaction).filter(PaymentTransaction.loan_id == loan.id).one()
    assert txn.status == "uncertain"
    seed.db.refresh(loan)
    assert loan.status == "processing"
    with pytest.raises(HTTPException) as exc:
        disb.execute_disbursement(seed.db, seed.tenant.id, loan, seed.checker.id)
    assert exc.value.status_code == 409

    # The initiator cannot resolve it; another approver checks M-Pesa: not paid.
    with pytest.raises(HTTPException) as exc:
        payments.resolve_payout(txn.id, PayoutResolveIn(outcome="not_paid", note="checked"),
                                None, tenant_id=seed.tenant.id, db=seed.db, user=seed.maker)
    assert exc.value.status_code == 403
    payments.resolve_payout(txn.id, PayoutResolveIn(outcome="not_paid", note="Not on portal"),
                            None, tenant_id=seed.tenant.id, db=seed.db, user=seed.checker)
    seed.db.refresh(loan)
    assert loan.status == "approved"
    monkeypatch.undo()
    import app.services.mpesa as m2
    monkeypatch.setattr(m2, "b2c_disburse", lambda *a, **k: {
        "request": {}, "response": {"ResponseCode": "0", "ConversationID": "AG_RETRY"},
        "result": {"ResultCode": None, "ConversationID": "AG_RETRY"}})
    out = disb.execute_disbursement(seed.db, seed.tenant.id, loan, seed.checker.id)
    assert out["status"] == "processing"


def test_resolve_as_paid_activates_the_loan(seed, monkeypatch):
    import app.services.mpesa as mpesa
    loan = _approved(seed)
    monkeypatch.setattr(mpesa, "b2c_disburse", _raise(httpx.RemoteProtocolError("dropped")))
    with pytest.raises(HTTPException):
        disb.execute_disbursement(seed.db, seed.tenant.id, loan, seed.maker.id)
    txn = seed.db.query(PaymentTransaction).filter(PaymentTransaction.loan_id == loan.id).one()
    with pytest.raises(HTTPException) as exc:            # receipt required
        payments.resolve_payout(txn.id, PayoutResolveIn(outcome="paid", note="seen"),
                                None, tenant_id=seed.tenant.id, db=seed.db, user=seed.checker)
    assert exc.value.status_code == 422
    seed.db.rollback()
    payments.resolve_payout(txn.id, PayoutResolveIn(outcome="paid", receipt="qkx12abc",
                                                    note="Seen on portal 26 Oct"),
                            None, tenant_id=seed.tenant.id, db=seed.db, user=seed.checker)
    seed.db.refresh(loan)
    seed.db.refresh(txn)
    assert loan.status == "active" and txn.status == "success" and txn.mpesa_ref == "QKX12ABC"


def test_refused_payout_is_safe_to_retry(seed, monkeypatch):
    import app.services.mpesa as mpesa
    loan = _approved(seed)
    monkeypatch.setattr(mpesa, "b2c_disburse", _raise(httpx.ConnectError("down")))
    with pytest.raises(HTTPException):
        disb.execute_disbursement(seed.db, seed.tenant.id, loan, seed.maker.id)
    seed.db.refresh(loan)
    assert loan.status == "approved"
    assert seed.db.query(PaymentTransaction).filter(
        PaymentTransaction.loan_id == loan.id).one().status == "failed"


# --------------------------------------------------------------------------- #
# F07 — approval re-check; refund failure
# --------------------------------------------------------------------------- #
def test_refund_approval_rechecks_what_is_still_refundable(seed, mock_b2c):
    db = seed.db
    loan = seed.make_loan(status="paid", principal="10000.00", interest_rate="10.0",
                          outstanding_balance="0.00")
    db.add(Repayment(tenant_id=seed.tenant.id, loan_id=loan.id, amount=Decimal("11200"),
                     payment_date=datetime(2026, 10, 1), method="mpesa_c2b", mpesa_ref="OV1"))
    # Two requests that slipped in before the fix, together over-reserving.
    for _ in range(2):
        db.add(PendingApproval(tenant_id=seed.tenant.id, action_type="refund", loan_id=loan.id,
                               amount=200, phone="0712345678", status="pending_approval",
                               maker_user_id=seed.recon.id))
    db.commit()
    p1, p2 = db.query(PendingApproval).order_by(PendingApproval.id).all()
    checker = seed.make_user("reconciliation_officer", "rchk@test.dcp")
    db.commit()
    decide_pending_action(p1.id, ApprovalDecision(action="approve"), None,
                          tenant_id=seed.tenant.id, user=checker, db=db)
    with pytest.raises(HTTPException) as exc:
        decide_pending_action(p2.id, ApprovalDecision(action="approve"), None,
                              tenant_id=seed.tenant.id, user=checker, db=db)
    assert exc.value.status_code == 409
    assert len(mock_b2c.calls) == 1


def test_failed_refund_reopens_the_suspense_receipt(seed, mock_b2c):
    db = seed.db
    entry = rp.record_suspense(db, tenant_id=seed.tenant.id, reason="unmatched", amount=300,
                               ref="QRF1", phone="254712345678")
    entry.status = "refund_processing"
    db.commit()
    disb.execute_refund(db, seed.tenant.id, 300, "254712345678", None, seed.recon.id, "t",
                        context={"suspense_id": entry.id})
    txn = db.query(PaymentTransaction).filter(PaymentTransaction.type == "refund").one()
    body = {"Result": {"ResultCode": 2001, "ConversationID": txn.mpesa_ref}}
    payments._ingest_and_process(db, ws.ENDPOINT_B2C_RESULT, body, payments._process_b2c_result,
                                 {"ResultCode": 0})
    db.refresh(entry)
    db.refresh(txn)
    assert txn.status == "failed" and entry.status == "open"


def test_refund_amount_rounds_up_to_whole_shillings(seed, mock_b2c):
    out = disb.execute_refund(seed.db, seed.tenant.id, "64.29", "254712345678", None,
                              seed.recon.id, "t")
    assert out["amount"] == 65.0


# --------------------------------------------------------------------------- #
# F25 — integrity check
# --------------------------------------------------------------------------- #
def test_integrity_reports_mismatches_and_counts_penalties(seed):
    from app.routers.access import integrity_check
    db = seed.db
    good = seed.make_loan(status="overdue", principal="10000.00", interest_rate="10.0",
                          outstanding_balance="11100.00", penalties_charged=Decimal("100"))
    bad = seed.make_loan(status="active", principal="10000.00", interest_rate="10.0",
                         outstanding_balance="7000.00")
    db.commit()
    out = integrity_check(tenant_id=seed.tenant.id, actor=seed.recon, db=db)
    ids = [m["loan_id"] for m in out["mismatches"]]
    assert bad.id in ids and good.id not in ids
    assert out["healthy"] is False


def test_penalty_accrual_is_recorded(seed):
    from app.routers.notifications import _accrue_overdue_penalty
    loan = seed.make_loan(status="active", principal="10000.00", interest_rate="10.0",
                          outstanding_balance="11000.00")
    loan.product = seed.product
    seed.product.penalty_rate = 1.0
    added = _accrue_overdue_penalty(loan)
    assert added == Decimal("100.00")
    assert loan.penalties_charged == Decimal("100.00")
