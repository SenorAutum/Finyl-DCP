"""
WP2 — per-DCP paybill matching, customer numbers, allocation order, manual
payments and suspense actions with a second approver.

Router/service functions are called directly with the isolated test session,
in the same style as test_reconciliation.py.
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.core import customer_number as cn
from app.models import (ApprovalThreshold, Borrower, Loan, MpesaWebhookEvent,
                        PaymentTransaction, PendingApproval, Repayment, SuspenseEntry,
                        Tenant, TenantIntegrationConfig, TenantModule)
from app.routers import payments
from app.routers.approvals import decide_pending_action, pending_actions
from app.schemas import (ApprovalDecision, DarajaConfigIn, ManualPaymentIn,
                         PaymentSettingsIn, SuspenseAllocateIn, SuspenseRefundIn)
from app.services import repayments as rp
from app.services import webhook_security as ws

PAYBILL_A = "400200"
PAYBILL_B = "400300"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _paybill(db, tenant_id, shortcode):
    db.add(TenantIntegrationConfig(tenant_id=tenant_id, integration="daraja",
                                   config={"shortcode": shortcode}, secrets={}, enabled=True))
    db.commit()


def _second_dcp(seed):
    """A second DCP with its own borrower, loan and paybill."""
    db = seed.db
    t2 = Tenant(name="Other DCP", code="ODCP")
    db.add(t2)
    db.flush()
    db.add(TenantModule(tenant_id=t2.id, module_key="payments", enabled=True))
    b2 = Borrower(tenant_id=t2.id, first_name="Otieno", last_name="Other",
                  national_id="99887766", phone="0799000111")
    db.add(b2)
    db.flush()
    loan2 = Loan(tenant_id=t2.id, account_number="FL/O/0001", borrower_id=b2.id,
                 product_id=seed.product.id, principal="5000.00", interest_rate="10.0",
                 status="active", outstanding_balance="5500.00")
    db.add(loan2)
    db.commit()
    _paybill(db, t2.id, PAYBILL_B)
    return t2, b2, loan2


def _c2b(bill_ref, amount="500", trans_id="QJK1ABC", shortcode=PAYBILL_A,
         msisdn="254712345678", trans_time="20261024143015"):
    body = {"TransactionType": "Pay Bill", "TransID": trans_id, "TransTime": trans_time,
            "TransAmount": amount, "BusinessShortCode": shortcode,
            "BillRefNumber": bill_ref, "MSISDN": msisdn,
            "FirstName": "JANE", "LastName": "WANJIKU"}
    if shortcode is None:
        body.pop("BusinessShortCode")
    return body


def _ingest(db, body):
    return payments._ingest_and_process(
        db, ws.ENDPOINT_C2B_CALLBACK, body, payments._process_c2b_callback,
        {"ResultCode": 0, "ResultDesc": "Confirmation received successfully"})


def _threshold(db, tenant_id, amount):
    db.add(ApprovalThreshold(tenant_id=tenant_id, scope_type="role", scope_key="all",
                             threshold_type="manual_payment", amount=amount))
    db.commit()


def _manual(seed, user=None, **kw):
    body = dict(channel="bank", amount=1100.0, reference="EQ-778899",
                paid_at=datetime.utcnow() - timedelta(hours=2),
                note="Equity statement line 24/10", loan_id=None)
    body.update(kw)
    return payments.record_manual_payment(
        ManualPaymentIn(**body), None, tenant_id=seed.tenant.id, db=seed.db,
        user=user or seed.recon)


def _decide(seed, pid, user, action="approve"):
    return decide_pending_action(pid, ApprovalDecision(action=action), None,
                                 tenant_id=seed.tenant.id, user=user, db=seed.db)


@pytest.fixture()
def recon2(seed):
    return seed.make_user("reconciliation_officer", "recon2@test.dcp")


# --------------------------------------------------------------------------- #
# Customer numbers
# --------------------------------------------------------------------------- #
def test_borrower_gets_customer_number_automatically(seed):
    num = seed.borrower.customer_number
    assert num and len(num) == 7 and num.isdigit()
    assert cn.is_valid(num)


def test_customer_numbers_unique_within_dcp(seed):
    db = seed.db
    nums = set()
    for i in range(40):
        b = Borrower(tenant_id=seed.tenant.id, first_name=f"C{i}", last_name="X",
                     national_id=f"3000{i:04d}", phone=f"07110{i:05d}")
        db.add(b)
        db.flush()
        nums.add(b.customer_number)
    assert len(nums) == 40


def test_check_digit_catches_every_single_digit_typo():
    num = "4927318" if cn.is_valid("4927318") else cn.generate_candidate()
    for pos in range(7):
        for d in "0123456789":
            if d == num[pos]:
                continue
            typo = num[:pos] + d + num[pos + 1:]
            assert not cn.is_valid(typo), typo


# --------------------------------------------------------------------------- #
# Paybill (C2B) — each DCP's paybill only ever credits that DCP
# --------------------------------------------------------------------------- #
def test_payment_to_dcp_a_paybill_cannot_credit_dcp_b_loan(seed):
    """The pre-WP2 code matched BillRefNumber across ALL DCPs: money paid into
    DCP A's paybill with DCP B's account number reduced DCP B's loan."""
    db = seed.db
    _paybill(db, seed.tenant.id, PAYBILL_A)
    _t2, _b2, loan_b = _second_dcp(seed)

    ack = _ingest(db, _c2b(loan_b.account_number, amount="1000", trans_id="XDCP1",
                           shortcode=PAYBILL_A))
    assert ack["ResultCode"] == 0
    db.refresh(loan_b)
    assert loan_b.outstanding_balance == Decimal("5500.00")          # untouched
    assert db.query(Repayment).filter(Repayment.loan_id == loan_b.id).count() == 0
    entry = db.query(SuspenseEntry).filter(SuspenseEntry.mpesa_ref == "XDCP1").one()
    assert entry.tenant_id == seed.tenant.id                         # DCP A's suspense
    assert entry.reason == "unmatched"


def test_paybill_payment_by_loan_account_number(seed):
    db = seed.db
    _paybill(db, seed.tenant.id, PAYBILL_A)
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    _ingest(db, _c2b(loan.account_number.lower(), amount="1100", trans_id="ACC1"))
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("9900.00")
    rep = db.query(Repayment).filter(Repayment.loan_id == loan.id).one()
    assert rep.method == "mpesa_c2b"
    # Dated when the customer paid (24 Oct 14:30:15 EAT = 11:30:15 UTC).
    assert rep.payment_date == datetime(2026, 10, 24, 11, 30, 15)


def test_paybill_payment_by_customer_number(seed):
    db = seed.db
    _paybill(db, seed.tenant.id, PAYBILL_A)
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    _ingest(db, _c2b(seed.borrower.customer_number, amount="2200", trans_id="CUST1"))
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("8800.00")


def test_customer_number_typo_goes_to_review_with_phone_suggestion(seed):
    db = seed.db
    _paybill(db, seed.tenant.id, PAYBILL_A)
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    good = seed.borrower.customer_number
    typo = good[:-1] + str((int(good[-1]) + 1) % 10)
    _ingest(db, _c2b(typo, amount="500", trans_id="TYPO1", msisdn="254712345678"))
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("11000.00")           # not guessed
    entry = db.query(SuspenseEntry).filter(SuspenseEntry.mpesa_ref == "TYPO1").one()
    assert entry.suggested_loan_id == loan.id                        # hint only
    assert entry.bill_ref == typo
    assert entry.payer_name == "JANE WANJIKU"


def test_blank_account_number_is_recorded_not_dropped(seed):
    """Pre-WP2 a confirmation without BillRefNumber failed validation and was
    acknowledged as 'Ignored (unparseable)' — the money left no trace."""
    db = seed.db
    _paybill(db, seed.tenant.id, PAYBILL_A)
    body = _c2b("", amount="300", trans_id="BLANK1")
    body.pop("BillRefNumber")
    _ingest(db, body)
    entry = db.query(SuspenseEntry).filter(SuspenseEntry.mpesa_ref == "BLANK1").one()
    assert entry.amount == Decimal("300.00")
    assert entry.status == "open"


def test_unknown_paybill_is_dead_lettered_not_guessed(seed):
    db = seed.db
    _paybill(db, seed.tenant.id, PAYBILL_A)
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    _ingest(db, _c2b(loan.account_number, trans_id="UNK1", shortcode="777777"))
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("11000.00")
    ev = db.query(MpesaWebhookEvent).one()
    assert ev.processing_status == "failed"
    assert "not registered" in (ev.last_error or "")


def test_defaulted_loan_payment_is_applied(seed):
    """Pre-WP2 money for a defaulted loan was parked in suspense and allocating
    it back was refused (loan not collectible) — it could never reach the loan."""
    db = seed.db
    _paybill(db, seed.tenant.id, PAYBILL_A)
    loan = seed.make_loan(status="defaulted", outstanding_balance="11000.00")
    db.commit()
    _ingest(db, _c2b(loan.account_number, amount="1000", trans_id="DEF1"))
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("10000.00")
    assert db.query(SuspenseEntry).count() == 0


def test_overpayment_closes_loan_and_parks_only_the_excess(seed):
    db = seed.db
    _paybill(db, seed.tenant.id, PAYBILL_A)
    loan = seed.make_loan(status="active", outstanding_balance="1000.00", active_lock=True)
    db.commit()
    _ingest(db, _c2b(loan.account_number, amount="1250", trans_id="OVR1"))
    db.refresh(loan)
    assert loan.status == "paid"
    assert loan.outstanding_balance == Decimal("0.00")
    assert loan.active_lock is False
    rep = db.query(Repayment).filter(Repayment.loan_id == loan.id).one()
    assert rep.amount == Decimal("1000.00")                           # only what was owed
    entry = db.query(SuspenseEntry).filter(SuspenseEntry.reason == "overpayment").one()
    assert entry.amount == Decimal("250.00")
    assert entry.matched_loan_id == loan.id


def test_duplicate_confirmation_is_not_double_credited(seed):
    db = seed.db
    _paybill(db, seed.tenant.id, PAYBILL_A)
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    _ingest(db, _c2b(loan.account_number, amount="1000", trans_id="DUPC1"))
    _ingest(db, _c2b(loan.account_number, amount="1000", trans_id="DUPC1"))
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("10000.00")
    assert db.query(Repayment).filter(Repayment.mpesa_ref == "DUPC1").count() == 1


def test_paybill_cannot_be_registered_by_two_dcps(seed):
    from app.routers.settings import update_daraja
    db = seed.db
    _paybill(db, seed.tenant.id, PAYBILL_A)
    t2, _b2, _l2 = _second_dcp(seed)
    admin2 = seed.make_user("system_admin", "admin2@test.dcp")
    admin2.tenant_id = t2.id
    db.commit()
    with pytest.raises(HTTPException) as exc:
        update_daraja(DarajaConfigIn(shortcode=PAYBILL_A), None,
                      tenant_id=t2.id, db=db, actor=admin2)
    assert exc.value.status_code == 409


# --------------------------------------------------------------------------- #
# Allocation order
# --------------------------------------------------------------------------- #
def _penalised_loan(seed):
    # principal 10,000 @ 10% flat = 1,000 interest, plus a 100 penalty.
    loan = seed.make_loan(status="active", principal="10000.00", interest_rate="10.0",
                          outstanding_balance="11100.00")
    seed.db.commit()
    return loan


def _apply(seed, loan, amount, ref):
    db = seed.db
    locked = db.query(Loan).filter(Loan.id == loan.id).with_for_update().one()
    res = rp.apply_payment(db, tenant_id=seed.tenant.id, loan=locked, amount=amount,
                           ref=ref, method="test", send_receipt=False)
    db.commit()
    return res


def test_default_allocation_follows_regulation_56_2(seed):
    """LN 191/2026 reg. 56(2): due principal, then fees and charges, then interest."""
    assert (rp.get_payment_settings(seed.db, seed.tenant.id)["allocation_order"]
            == "principal_fees_interest")
    res = _apply(seed, _penalised_loan(seed), "1100", "AL0")
    assert res.components == {"interest": Decimal("0.00"), "fees": Decimal("0.00"),
                              "principal": Decimal("1100.00")}


def test_proportional_split_still_selectable(seed):
    rp.set_payment_settings(seed.db, seed.tenant.id, seed.recon.id,
                            allocation_order="proportional")
    res = _apply(seed, _penalised_loan(seed), "1100", "AL0P")
    assert res.components == {"interest": Decimal("100.00"), "fees": Decimal("0.00"),
                              "principal": Decimal("1000.00")}


def test_interest_fees_principal_order(seed):
    rp.set_payment_settings(seed.db, seed.tenant.id, seed.recon.id,
                            allocation_order="interest_fees_principal", basis="test")
    res = _apply(seed, _penalised_loan(seed), "1500", "AL1")
    assert res.components == {"interest": Decimal("1000.00"), "fees": Decimal("100.00"),
                              "principal": Decimal("400.00")}


def test_principal_fees_interest_order(seed):
    rp.set_payment_settings(seed.db, seed.tenant.id, seed.recon.id,
                            allocation_order="principal_fees_interest", basis="test")
    loan = _penalised_loan(seed)
    first = _apply(seed, loan, "10050", "AL2")
    assert first.components == {"principal": Decimal("10000.00"), "fees": Decimal("50.00"),
                                "interest": Decimal("0.00")}
    second = _apply(seed, loan, "1050", "AL3")                       # settles the rest
    assert second.components == {"principal": Decimal("0.00"), "fees": Decimal("50.00"),
                                 "interest": Decimal("1000.00")}
    seed.db.refresh(loan)
    assert loan.status == "paid"


def test_invalid_allocation_order_rejected(seed):
    with pytest.raises(HTTPException) as exc:
        payments.update_payment_settings(PaymentSettingsIn(allocation_order="lifo"), None,
                                         tenant_id=seed.tenant.id, db=seed.db,
                                         user=seed.recon)
    assert exc.value.status_code == 422


# --------------------------------------------------------------------------- #
# Manual payments (maker-checker)
# --------------------------------------------------------------------------- #
def test_manual_payment_waits_for_a_second_person(seed, recon2):
    db = seed.db
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    out = _manual(seed, loan_id=loan.id)
    assert out["status"] == "pending_approval"
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("11000.00")           # nothing yet

    with pytest.raises(HTTPException) as exc:                        # maker can't approve
        _decide(seed, out["pending_id"], seed.recon)
    assert exc.value.status_code == 403

    listed = pending_actions(tenant_id=seed.tenant.id, user=recon2, db=db)
    row = next(r for r in listed if r["id"] == out["pending_id"])
    assert row["reference"] == "EQ-778899" and row["channel"] == "bank"
    assert row["can_approve"] is True

    _decide(seed, out["pending_id"], recon2)
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("9900.00")
    rep = db.query(Repayment).filter(Repayment.mpesa_ref == "EQ-778899").one()
    assert rep.method == "manual_bank"


def test_manual_payment_below_threshold_applies_at_once(seed):
    db = seed.db
    _threshold(db, seed.tenant.id, 5000)
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    out = _manual(seed, loan_id=loan.id, amount=1000.0, reference="QJX123")
    assert out["status"] == "applied"
    db.refresh(loan)
    assert loan.outstanding_balance == Decimal("10000.00")


def test_manual_payment_reference_can_only_be_used_once(seed):
    db = seed.db
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    _manual(seed, loan_id=loan.id, reference="SAME-REF")
    with pytest.raises(HTTPException) as exc:                        # already pending
        _manual(seed, loan_id=loan.id, reference="same-ref")
    assert exc.value.status_code == 409

    _threshold(db, seed.tenant.id, 1_000_000)
    _manual(seed, loan_id=loan.id, reference="APPLIED-REF")
    with pytest.raises(HTTPException) as exc:                        # already a repayment
        _manual(seed, loan_id=loan.id, reference="APPLIED-REF")
    assert exc.value.status_code == 409


def test_manual_payment_by_customer_number(seed):
    db = seed.db
    _threshold(db, seed.tenant.id, 1_000_000)
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    out = _manual(seed, customer_number=seed.borrower.customer_number, reference="CN-1")
    assert out["loan_id"] == loan.id and out["status"] == "applied"


@pytest.mark.parametrize("override,code", [
    ({"reference": None}, 422),                                      # bank needs a ref
    ({"note": "  "}, 422),                                           # evidence required
    ({"paid_at": datetime.utcnow() + timedelta(days=1)}, 422),       # future date
    ({"channel": "crypto"}, 422),
    ({"amount": 0}, 422),
])
def test_manual_payment_validation(seed, override, code):
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    seed.db.commit()
    with pytest.raises(HTTPException) as exc:
        _manual(seed, loan_id=loan.id, **override)
    assert exc.value.status_code == code


def test_manual_payment_cannot_target_another_dcps_loan(seed):
    _t2, _b2, loan_b = _second_dcp(seed)
    with pytest.raises(HTTPException) as exc:
        _manual(seed, loan_id=loan_b.id)
    assert exc.value.status_code == 404


def test_role_without_reconcile_approve_cannot_approve(seed):
    db = seed.db
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    out = _manual(seed, loan_id=loan.id)
    with pytest.raises(HTTPException) as exc:
        _decide(seed, out["pending_id"], seed.checker)               # disbursement officer
    assert exc.value.status_code == 403


def test_legacy_reconcile_endpoint_now_requires_approval(seed):
    from app.schemas import ReconcileRequest
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    seed.db.commit()
    out = payments.reconcile_payment(
        body=ReconcileRequest(loan_id=loan.id, amount=500.0, mpesa_ref="LEG1"),
        tenant_id=seed.tenant.id, db=seed.db, user=seed.recon, request=None)
    assert out["status"] == "pending_approval"


# --------------------------------------------------------------------------- #
# Suspense actions
# --------------------------------------------------------------------------- #
def _open_entry(seed, ref="SUS-REF", amount=1100, phone="254712345678"):
    e = rp.record_suspense(seed.db, tenant_id=seed.tenant.id, reason="unmatched",
                           amount=amount, ref=ref, phone=phone)
    seed.db.commit()
    return e


def test_suspense_allocation_with_approval(seed, recon2):
    db = seed.db
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    entry = _open_entry(seed)
    out = payments.allocate_suspense(entry.id, SuspenseAllocateIn(loan_id=loan.id), None,
                                     tenant_id=seed.tenant.id, db=db, user=seed.recon)
    assert out["status"] == "pending_approval"
    db.refresh(entry)
    assert entry.status == "pending_approval"
    # Held: cannot be refunded or allocated again while awaiting approval.
    again = payments.refund_suspense(entry.id, SuspenseRefundIn(note="x"), None,
                                     tenant_id=seed.tenant.id, db=db, user=seed.recon)
    assert again["status"] == "already_resolved"

    _decide(seed, out["pending_id"], recon2)
    db.refresh(entry)
    db.refresh(loan)
    assert entry.status == "allocated" and entry.matched_loan_id == loan.id
    assert loan.outstanding_balance == Decimal("9900.00")
    rep = db.query(Repayment).filter(Repayment.loan_id == loan.id).one()
    assert rep.mpesa_ref == "SUS-REF"                                 # M-Pesa code kept


def test_rejected_allocation_reopens_the_entry(seed, recon2):
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    entry = _open_entry(seed, ref="REJ-1")
    out = payments.allocate_suspense(entry.id, SuspenseAllocateIn(loan_id=loan.id), None,
                                     tenant_id=seed.tenant.id, db=seed.db, user=seed.recon)
    _decide(seed, out["pending_id"], recon2, action="reject")
    seed.db.refresh(entry)
    assert entry.status == "open"


def test_suspense_refund_pays_out_after_approval(seed, recon2, mock_b2c):
    db = seed.db
    entry = _open_entry(seed, ref="RF-1", amount=300)
    out = payments.refund_suspense(entry.id, SuspenseRefundIn(note="Not our customer"), None,
                                   tenant_id=seed.tenant.id, db=db, user=seed.recon)
    assert out["status"] == "pending_approval"
    assert mock_b2c.calls == []                                      # nothing sent yet
    _decide(seed, out["pending_id"], recon2)
    assert len(mock_b2c.calls) == 1
    assert mock_b2c.calls[0]["phone"] == "254712345678"
    db.refresh(entry)
    assert entry.status == "refunded"
    assert db.query(PaymentTransaction).filter(PaymentTransaction.type == "refund").count() == 1


def test_refund_to_masked_number_needs_explicit_phone_and_note(seed):
    entry = _open_entry(seed, ref="RF-2", phone="2547 ***** 678")
    with pytest.raises(HTTPException) as exc:
        payments.refund_suspense(entry.id, SuspenseRefundIn(), None,
                                 tenant_id=seed.tenant.id, db=seed.db, user=seed.recon)
    assert exc.value.status_code == 422
    seed.db.rollback()
    with pytest.raises(HTTPException) as exc:
        payments.refund_suspense(entry.id, SuspenseRefundIn(refund_phone="0722000111"), None,
                                 tenant_id=seed.tenant.id, db=seed.db, user=seed.recon)
    assert exc.value.status_code == 422
    seed.db.rollback()
    out = payments.refund_suspense(
        entry.id, SuspenseRefundIn(refund_phone="0722000111", note="Payer called; confirmed"),
        None, tenant_id=seed.tenant.id, db=seed.db, user=seed.recon)
    assert out["phone"] == "254722000111"
    p = seed.db.get(PendingApproval, out["pending_id"])
    assert p.details["phone_overridden"] is True
