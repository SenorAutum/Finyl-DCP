"""
WP4 — CSV upload safety, M-Pesa reversals, unique loan numbers, audit
savepoint, customer number in SMS.
"""
import asyncio
import io
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import UploadFile

from app.core.deps import write_audit
from app.models import (ApprovalThreshold, AuditLog, Loan, PaymentTransaction,
                        PendingApproval, Repayment, SuspenseEntry)
from app.routers import payments
from app.routers.access import payment_upload
from app.routers.approvals import decide_pending_action
from app.schemas import ApprovalDecision, SuspenseRefundIn
from app.services import payment_approvals as pay_appr
from app.services import repayments as rp
from app.services import sms
from app.services import webhook_security as ws


@pytest.fixture()
def recon2(seed):
    return seed.make_user("reconciliation_officer", "recon2@test.dcp")


def _decide(seed, pid, user, action="approve"):
    return decide_pending_action(pid, ApprovalDecision(action=action), None,
                                 tenant_id=seed.tenant.id, user=user, db=seed.db)


def _upload(seed, text, user=None):
    f = UploadFile(file=io.BytesIO(text.encode()), filename="statement.csv")
    return asyncio.run(payment_upload(None, f, tenant_id=seed.tenant.id,
                                      actor=user or seed.recon, db=seed.db))


# --------------------------------------------------------------------------- #
# Audit savepoint (open item from WP1)
# --------------------------------------------------------------------------- #
def test_failed_audit_no_longer_discards_the_callers_work(seed):
    """Before WP4 a failed audit insert called db.rollback(): the caller's
    pending change was silently thrown away while the handler reported success."""
    db = seed.db
    loan = seed.make_loan(status="active", outstanding_balance="1000.00")
    db.commit()
    loan.outstanding_balance = Decimal("900.00")
    write_audit(db, tenant_id=seed.tenant.id, user=seed.recon,
                action="x" * 200)                      # too long for the column -> fails
    db.commit()
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("900.00")
    assert db.query(AuditLog).filter(AuditLog.action.like("xxx%")).count() == 0


def test_audit_accepts_decimal_details(seed):
    write_audit(seed.db, tenant_id=seed.tenant.id, user=seed.recon, action="t.decimal",
                details={"amount": Decimal("12.50")})
    seed.db.commit()
    row = seed.db.query(AuditLog).filter(AuditLog.action == "t.decimal").one()
    assert row.details["amount"] == "12.50"


# --------------------------------------------------------------------------- #
# Unique loan account numbers (open item from WP1)
# --------------------------------------------------------------------------- #
def test_database_refuses_duplicate_loan_account_number(seed):
    db = seed.db
    a = seed.make_loan(status="active")
    db.commit()
    with pytest.raises(IntegrityError):
        db.add(Loan(tenant_id=seed.tenant.id, account_number=a.account_number,
                    borrower_id=a.borrower_id, product_id=a.product_id,
                    principal="1000.00", interest_rate="10.0", status="pending"))
        db.flush()
    db.rollback()


# --------------------------------------------------------------------------- #
# Customer number in SMS
# --------------------------------------------------------------------------- #
def test_sms_context_and_defaults_carry_paybill_and_customer_number(seed):
    loan = seed.make_loan(status="active", outstanding_balance="500.00")
    ctx = sms._base_context(seed.db, seed.tenant.id, seed.borrower, loan)
    assert ctx["customer_number"] == seed.borrower.customer_number
    assert "paybill" in ctx
    for key in ("loan_disbursed", "repayment_reminder"):
        assert "{{paybill}}" in sms.DEFAULT_TEMPLATES[key]
        assert "{{customer_number}}" in sms.DEFAULT_TEMPLATES[key]


# --------------------------------------------------------------------------- #
# CSV payment upload
# --------------------------------------------------------------------------- #
def test_upload_rows_wait_for_approval_and_are_checked(seed):
    db = seed.db
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    db.add(Repayment(tenant_id=seed.tenant.id, loan_id=loan.id, amount=Decimal("1"),
                     payment_date=loan.created_at, method="mpesa_c2b", mpesa_ref="OLDREF1"))
    db.commit()
    csv_text = (
        "account_number,amount,mpesa_ref,date\n"
        f"{loan.account_number},1000,QA1,2026-10-20\n"          # parked
        f"{loan.account_number},500,QA1,2026-10-20\n"           # repeated in file
        f"{loan.account_number},500,OLDREF1,2026-10-20\n"       # already recorded
        "FL/NOPE/1,700,QA2,2026-10-20\n"                         # not matched
        f"{loan.account_number},-5,QA3,2026-10-20\n"            # negative
        f"{loan.account_number},abc,QA4,2026-10-20\n"           # bad amount
        f"{seed.borrower.customer_number},250,QA5,\n"            # customer number -> parked
    )
    out = _upload(seed, csv_text)
    assert out["pending_approval"] == 2 and out["applied"] == 0
    assert out["unmatched"] == 1 and len(out["duplicates"]) == 2 and len(out["errors"]) == 2
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("11000.00")      # nothing applied yet
    assert db.query(PendingApproval).filter(
        PendingApproval.action_type == "manual_payment").count() == 2


def test_upload_under_threshold_applies_with_payment_date(seed):
    db = seed.db
    db.add(ApprovalThreshold(tenant_id=seed.tenant.id, scope_type="role", scope_key="all",
                             threshold_type="manual_payment", amount=100000))
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    out = _upload(seed, f"account_number,amount,mpesa_ref,date\n{loan.account_number},1000,QB1,2026-10-20 14:00\n")
    assert out["applied"] == 1
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("10000.00")
    rep = db.query(Repayment).filter(Repayment.mpesa_ref == "QB1").one()
    assert rep.method == "manual_mpesa_paybill"
    assert rep.payment_date.hour == 11                            # 14:00 EAT -> 11:00 UTC


# --------------------------------------------------------------------------- #
# M-Pesa reversal of wrong payments
# --------------------------------------------------------------------------- #
def _receipt(seed, ref="QWRONG1", reason="unmatched", amount=750):
    e = rp.record_suspense(seed.db, tenant_id=seed.tenant.id, reason=reason, amount=amount,
                           ref=ref, phone="2547****678", source="c2b")
    seed.db.commit()
    return e


def test_reversal_needs_a_second_person_then_settles(seed, recon2):
    db = seed.db
    entry = _receipt(seed)
    out = payments.reverse_suspense(entry.id, SuspenseRefundIn(note="Customer paid wrong paybill"),
                                    None, tenant_id=seed.tenant.id, db=db, user=seed.recon)
    assert out["status"] == "pending_approval"
    with pytest.raises(HTTPException) as exc:
        _decide(seed, out["pending_id"], seed.recon)                # maker cannot approve
    assert exc.value.status_code == 403
    res = _decide(seed, out["pending_id"], recon2)
    assert res["result"]["simulated"] is True
    db.refresh(entry)
    assert entry.status == "reversed"
    txn = db.query(PaymentTransaction).filter(PaymentTransaction.type == "reversal").one()
    assert txn.status == "success"
    assert txn.raw_payload["original_transaction_id"] == "QWRONG1"


def test_overpayment_excess_cannot_be_reversed(seed):
    entry = _receipt(seed, ref="QOVR1-OVR", reason="overpayment")
    assert pay_appr.reversal_eligibility(entry)[0] is False
    with pytest.raises(HTTPException) as exc:
        payments.reverse_suspense(entry.id, SuspenseRefundIn(note="x"), None,
                                  tenant_id=seed.tenant.id, db=seed.db, user=seed.recon)
    assert exc.value.status_code == 422


def test_reversal_requires_a_reason(seed):
    entry = _receipt(seed, ref="QNOTE1")
    with pytest.raises(HTTPException) as exc:
        payments.reverse_suspense(entry.id, SuspenseRefundIn(note="  "), None,
                                  tenant_id=seed.tenant.id, db=seed.db, user=seed.recon)
    assert exc.value.status_code == 422


def test_rejected_reversal_reopens_the_receipt(seed, recon2):
    entry = _receipt(seed, ref="QREJ1")
    out = payments.reverse_suspense(entry.id, SuspenseRefundIn(note="wrong"), None,
                                    tenant_id=seed.tenant.id, db=seed.db, user=seed.recon)
    _decide(seed, out["pending_id"], recon2, action="reject")
    seed.db.refresh(entry)
    assert entry.status == "open"


def _live_style_txn(seed, entry, conv="AG_TEST_REV_1"):
    """A reversal accepted by (real) Safaricom and awaiting its result."""
    entry.status = "reversal_pending"
    txn = PaymentTransaction(tenant_id=seed.tenant.id, type="reversal", amount=entry.amount,
                             mpesa_ref=conv, status="processing",
                             raw_payload={"suspense_id": entry.id,
                                          "original_transaction_id": entry.mpesa_ref})
    seed.db.add(txn)
    seed.db.commit()
    return txn


def _callback(seed, endpoint, processor, body):
    return payments._ingest_and_process(seed.db, endpoint, body, processor, {"ResultCode": 0})


def test_failed_reversal_result_returns_receipt_to_queue(seed):
    entry = _receipt(seed, ref="QFAIL1")
    txn = _live_style_txn(seed, entry)
    body = {"Result": {"ResultCode": "R000002", "ResultDesc": "Invalid TransactionID",
                       "ConversationID": "AG_TEST_REV_1"}}
    _callback(seed, ws.ENDPOINT_REVERSAL_RESULT, payments._process_reversal_result, body)
    seed.db.refresh(entry)
    seed.db.refresh(txn)
    assert txn.status == "failed" and entry.status == "open"
    assert entry.raw_payload["reversal_failed"]["result_code"] == "R000002"


def test_reversal_success_callback_is_idempotent(seed):
    entry = _receipt(seed, ref="QOK1")
    txn = _live_style_txn(seed, entry, conv="AG_TEST_REV_2")
    body = {"Result": {"ResultCode": 0, "ResultDesc": "ok", "ConversationID": "AG_TEST_REV_2",
                       "TransactionID": "RVS123"}}
    for _ in range(2):
        _callback(seed, ws.ENDPOINT_REVERSAL_RESULT, payments._process_reversal_result, body)
    seed.db.refresh(entry)
    seed.db.refresh(txn)
    assert txn.status == "success" and entry.status == "reversed"
    assert entry.raw_payload["reversal"]["receipt"] == "RVS123"


def test_reversal_timeout_keeps_receipt_pending(seed):
    entry = _receipt(seed, ref="QTIME1")
    txn = _live_style_txn(seed, entry, conv="AG_TEST_REV_3")
    _callback(seed, ws.ENDPOINT_REVERSAL_TIMEOUT, payments._process_reversal_timeout,
              {"Result": {"ConversationID": "AG_TEST_REV_3"}})
    seed.db.refresh(entry)
    seed.db.refresh(txn)
    assert txn.status == "timed_out" and entry.status == "reversal_pending"
