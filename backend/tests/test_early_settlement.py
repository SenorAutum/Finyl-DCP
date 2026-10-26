"""
Early full repayment — CBK Non-Deposit Taking Credit Providers Regulations, 2026
(Legal Notice 191), regulation 56(4): "where the loan is repaid in full prior to
its maturity, the borrower shall not be required to pay interest for the
remaining period to maturity."

Worked example used throughout: KES 10,000 at 10% flat for 4 weeks
= KES 1,000 interest, disbursed 10 Oct 2026, due 7 Nov 2026 (28 days).
"""
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models import Loan, Repayment, SuspenseEntry, TenantIntegrationConfig
from app.routers import payments
from app.schemas import PaymentSettingsIn
from app.services import repayments as rp
from app.services import webhook_security as ws

DISBURSED = date(2026, 10, 10)
DUE = date(2026, 11, 7)


def _loan(seed, outstanding="11000.00", **kw):
    loan = seed.make_loan(status="active", principal="10000.00", interest_rate="10.0",
                          outstanding_balance=outstanding, disbursement_date=DISBURSED,
                          due_date=DUE, **kw)
    seed.db.commit()
    return loan


def _pay(seed, loan, amount, when_utc, ref):
    db = seed.db
    locked = db.query(Loan).filter(Loan.id == loan.id).with_for_update().one()
    res = rp.apply_payment(db, tenant_id=seed.tenant.id, loan=locked, amount=amount, ref=ref,
                           method="test", paid_at=when_utc, send_receipt=False)
    db.commit()
    db.refresh(loan)
    return res


# --------------------------------------------------------------------------- #
# The quote
# --------------------------------------------------------------------------- #
def test_halfway_payoff_charges_half_the_interest(seed):
    q = rp.payoff_quote(seed.db, _loan(seed), date(2026, 10, 24))      # day 14 of 28
    assert q["early"] is True
    assert q["days_used"] == 14 and q["days_in_term"] == 28
    assert q["payoff_amount"] == 10500.0
    assert q["rebate"] == 500.0
    assert q["basis"] == "CBK LN 191/2026 reg. 56(4)"


def test_same_day_payoff_charges_one_day(seed):
    q = rp.payoff_quote(seed.db, _loan(seed), DISBURSED)
    assert q["days_used"] == 1
    assert q["payoff_amount"] == 10035.71                               # 1000 x 1/28


def test_on_or_after_due_date_full_interest_is_payable(seed):
    loan = _loan(seed)
    for day in (DUE, date(2026, 11, 20)):
        q = rp.payoff_quote(seed.db, loan, day)
        assert q["early"] is False
        assert q["payoff_amount"] == 11000.0 and q["rebate"] == 0.0


def test_penalties_and_fees_are_not_written_off(seed):
    q = rp.payoff_quote(seed.db, _loan(seed, outstanding="11100.00"), date(2026, 10, 24))
    assert q["fees_owed"] == 100.0
    assert q["payoff_amount"] == 10600.0                                # 10,000 + 100 + 500


def test_completed_periods_method_never_charges_more_than_daily(seed):
    loan = _loan(seed)
    daily = rp.payoff_quote(seed.db, loan, date(2026, 10, 20), "daily")        # day 10
    weeks = rp.payoff_quote(seed.db, loan, date(2026, 10, 20), "completed_periods")
    assert weeks["periods_completed"] == 1 and weeks["periods_in_term"] == 4
    assert weeks["payoff_amount"] == 10250.0
    assert weeks["payoff_amount"] <= daily["payoff_amount"]


def test_no_dates_means_no_early_quote(seed):
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    seed.db.commit()
    assert rp.payoff_quote(seed.db, loan, date(2026, 10, 24))["early"] is False


# --------------------------------------------------------------------------- #
# Applying payments
# --------------------------------------------------------------------------- #
def test_early_full_payment_closes_loan_and_records_rebate(seed):
    loan = _loan(seed, active_lock=True)
    res = _pay(seed, loan, "10500", datetime(2026, 10, 24, 9, 0), "EARLY1")
    assert res.applied == Decimal("10500.00") and res.rebate == Decimal("500.00")
    assert loan.status == "paid"
    assert loan.outstanding_balance == Decimal("0.00")
    assert loan.interest_rebate == Decimal("500.00")
    assert loan.early_settled_at == datetime(2026, 10, 24, 9, 0)
    assert loan.active_lock is False
    rep = seed.db.query(Repayment).filter(Repayment.mpesa_ref == "EARLY1").one()
    assert (rep.principal_component, rep.interest_component) == (Decimal("10000.00"),
                                                                 Decimal("500.00"))


def test_paying_the_full_term_amount_early_refunds_the_difference(seed):
    loan = _loan(seed)
    res = _pay(seed, loan, "11000", datetime(2026, 10, 24, 9, 0), "EARLY2")
    assert res.applied == Decimal("10500.00")
    assert loan.status == "paid"
    excess = seed.db.query(SuspenseEntry).filter(SuspenseEntry.reason == "overpayment").one()
    assert excess.amount == Decimal("500.00")                           # back to the borrower


def test_part_payment_early_gets_no_rebate_until_cleared(seed):
    loan = _loan(seed)
    first = _pay(seed, loan, "5500", datetime(2026, 10, 17, 9, 0), "PART1")   # day 7
    assert first.rebate == 0 and loan.status == "active"
    assert loan.outstanding_balance == Decimal("5500.00")
    # Day 14: 500 interest earned, 500 already paid in the proportional split.
    q = rp.payoff_quote(seed.db, loan, date(2026, 10, 24))
    assert q["payoff_amount"] == 5000.0 and q["rebate"] == 500.0
    last = _pay(seed, loan, "5000", datetime(2026, 10, 24, 9, 0), "PART2")
    assert last.rebate == Decimal("500.00") and loan.status == "paid"


def test_interest_paid_ahead_is_credited_against_the_payoff(seed):
    # Interest can only be paid ahead under the (selectable) proportional split.
    rp.set_payment_settings(seed.db, seed.tenant.id, seed.recon.id, allocation_order="proportional")
    loan = _loan(seed)
    _pay(seed, loan, "9900", datetime(2026, 10, 10, 9, 0), "AHEAD1")   # day 1: 900 interest
    q = rp.payoff_quote(seed.db, loan, date(2026, 10, 11))             # day 1 again (1 day)
    # 1,000 principal left; 35.71 earned vs 900 paid -> 864.29 credit.
    assert q["payoff_amount"] == 135.71
    _pay(seed, loan, "135.71", datetime(2026, 10, 10, 10, 0), "AHEAD2")
    assert loan.status == "paid"


def test_late_payment_gets_no_rebate(seed):
    loan = _loan(seed)
    res = _pay(seed, loan, "11000", datetime(2026, 11, 10, 9, 0), "LATE1")
    assert res.rebate == 0 and loan.status == "paid"
    assert seed.db.query(SuspenseEntry).count() == 0


def test_paybill_early_payoff_uses_mpesa_payment_date(seed):
    db = seed.db
    db.add(TenantIntegrationConfig(tenant_id=seed.tenant.id, integration="daraja",
                                   config={"shortcode": "400200"}, secrets={}, enabled=True))
    db.commit()
    loan = _loan(seed)
    body = {"TransID": "PBEARLY1", "TransTime": "20261024143015", "TransAmount": "10500",
            "BusinessShortCode": "400200", "BillRefNumber": loan.account_number,
            "MSISDN": "254712345678"}
    payments._ingest_and_process(db, ws.ENDPOINT_C2B_CALLBACK, body,
                                 payments._process_c2b_callback, {"ResultCode": 0})
    db.refresh(loan)
    assert loan.status == "paid" and loan.interest_rebate == Decimal("500.00")


# --------------------------------------------------------------------------- #
# Setting
# --------------------------------------------------------------------------- #
def test_default_method_is_daily_and_can_be_changed(seed):
    assert rp.get_payment_settings(seed.db, seed.tenant.id)["early_settlement_method"] == "daily"
    out = payments.update_payment_settings(
        PaymentSettingsIn(early_settlement_method="completed_periods"), None,
        tenant_id=seed.tenant.id, db=seed.db, user=seed.recon)
    assert out["early_settlement_method"] == "completed_periods"
    assert out["allocation_order"] == "principal_fees_interest"         # untouched


def test_no_method_keeps_full_term_interest(seed):
    with pytest.raises(HTTPException) as exc:
        payments.update_payment_settings(
            PaymentSettingsIn(early_settlement_method="full_term"), None,
            tenant_id=seed.tenant.id, db=seed.db, user=seed.recon)
    assert exc.value.status_code == 422


def test_unsplit_payment_does_not_shrink_the_rebate(seed):
    """A payment recorded without a principal/interest split (CSV batch upload,
    older data) is treated as an ordinary payment — the borrower still gets the
    unearned interest dropped."""
    loan = _loan(seed, outstanding="5500.00")
    seed.db.add(Repayment(tenant_id=seed.tenant.id, loan_id=loan.id, amount=Decimal("5500"),
                          payment_date=datetime(2026, 10, 17), method="batch_upload",
                          mpesa_ref="CSV1"))
    seed.db.commit()
    q = rp.payoff_quote(seed.db, loan, date(2026, 10, 24))
    assert q["payoff_amount"] == 5000.0 and q["rebate"] == 500.0
