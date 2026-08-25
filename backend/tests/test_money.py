"""
Decimal money arithmetic — the core interest / fee / excise maths and the
loan-pricing quote. Asserts CURRENT behaviour of app.core.money and
app.routers.lending._quote_breakdown.

Money is handled as decimal.Decimal end-to-end (no binary float drift), quantised
to 2dp with ROUND_HALF_UP.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.money import (D, money, split_interest_principal, reduce_balance)
from app.routers.lending import _quote_breakdown


# --------------------------------------------------------------------------- #
# money() — quantisation + ROUND_HALF_UP, string-based (no float error)
# --------------------------------------------------------------------------- #
def test_money_returns_decimal_2dp():
    v = money("100")
    assert isinstance(v, Decimal)
    assert v == Decimal("100.00")


@pytest.mark.parametrize("raw,expected", [
    ("2.345", "2.35"),   # half rounds up
    ("2.344", "2.34"),   # below half rounds down
    ("0.005", "0.01"),   # half rounds up at the cent
    ("0.004", "0.00"),
    (2.5, "2.50"),
    (1000, "1000.00"),
])
def test_money_half_up_rounding(raw, expected):
    assert money(raw) == Decimal(expected)


def test_D_avoids_float_reintroduction():
    # D coerces via str(), so a classic binary-float value stays exact.
    assert D(0.1) == Decimal("0.1")
    assert D("0.1") + D("0.2") == Decimal("0.3")


# --------------------------------------------------------------------------- #
# split_interest_principal — proportional interest/principal split that always
# sums back to the amount (no rounding gap).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("amount,rate", [
    (1000, 10),
    (1500, 15),
    (999.99, 12.5),
    (25000, 33.33),
    (1, 10),
])
def test_split_sums_back_exactly(amount, rate):
    interest, principal = split_interest_principal(amount, rate)
    assert isinstance(interest, Decimal) and isinstance(principal, Decimal)
    assert interest + principal == money(amount)
    assert interest >= 0 and principal >= 0


def test_split_known_value():
    # interest_share = rate/(100+rate) = 10/110 → 1000 * 10/110 = 90.9090.. → 90.91
    interest, principal = split_interest_principal(1000, 10)
    assert interest == Decimal("90.91")
    assert principal == Decimal("909.09")


def test_split_zero_denominator_guard():
    # rate == -100 → denominator 0 → all treated as principal (no crash).
    interest, principal = split_interest_principal(500, -100)
    assert interest == Decimal("0.00")
    assert principal == Decimal("500.00")


def test_no_float_drift_over_many_splits():
    # Summing the components of many splits must equal the summed amounts exactly.
    total_interest = Decimal("0.00")
    total_principal = Decimal("0.00")
    total_amount = Decimal("0.00")
    for cents in range(1, 501):  # 0.01 .. 5.00 and beyond
        amt = Decimal(cents) / Decimal(100) * Decimal(37)  # arbitrary messy values
        i, p = split_interest_principal(amt, 13.5)
        total_interest += i
        total_principal += p
        total_amount += money(amt)
    assert total_interest + total_principal == total_amount


def test_reduce_balance_floored_at_zero():
    assert reduce_balance("1000.00", "300.00") == Decimal("700.00")
    assert reduce_balance("100.00", "250.00") == Decimal("0.00")  # never negative
    assert isinstance(reduce_balance(100, 50), Decimal)


# --------------------------------------------------------------------------- #
# _quote_breakdown — interest + fees + 20% Kenya excise on fees.
# --------------------------------------------------------------------------- #
def _product(interest_rate, rules, tenure_value=4, tenure_unit="weeks"):
    return SimpleNamespace(
        id=1, name="Test Product", interest_rate=interest_rate,
        interest_method="flat", tenure_value=tenure_value,
        tenure_unit=tenure_unit, rules=rules,
    )


def test_quote_scenario_one():
    prod = _product(10.0, {"processing_fee_rate": 2.5, "facility_fee": 150})
    q = _quote_breakdown(prod, 10000)
    assert q["interest"] == 1000.00          # 10% of 10 000
    assert q["processing_fee"] == 250.00     # 2.5% of 10 000
    assert q["facility_fee"] == 150.00
    assert q["total_fees"] == 400.00
    assert q["excise_duty"] == 80.00         # 20% of 400
    assert q["excise_rate"] == 0.20
    assert q["total_cost_of_credit"] == 1480.00   # 1000 + 400 + 80
    assert q["total_repayable"] == 11480.00       # principal + TCC


def test_quote_scenario_two():
    prod = _product(15.0, {"processing_fee_rate": 1.5, "facility_fee": 300},
                    tenure_value=3, tenure_unit="months")
    q = _quote_breakdown(prod, 25000)
    assert q["interest"] == 3750.00
    assert q["processing_fee"] == 375.00
    assert q["facility_fee"] == 300.00
    assert q["total_fees"] == 675.00
    assert q["excise_duty"] == 135.00        # 20% of 675
    assert q["total_cost_of_credit"] == 4560.00
    assert q["total_repayable"] == 29560.00


def test_quote_excise_is_exactly_20pct_of_fees():
    prod = _product(20.0, {"processing_fee_rate": 3.333, "facility_fee": 99.99})
    q = _quote_breakdown(prod, 12345)
    # Excise must equal money(total_fees * 0.20) regardless of messy inputs.
    assert money(q["excise_duty"]) == money(Decimal(str(q["total_fees"])) * Decimal("0.20"))


def test_quote_no_fees_zero_excise():
    prod = _product(10.0, {})  # no rules -> no fees -> no excise
    q = _quote_breakdown(prod, 5000)
    assert q["processing_fee"] == 0.00
    assert q["facility_fee"] == 0.00
    assert q["total_fees"] == 0.00
    assert q["excise_duty"] == 0.00
    assert q["total_cost_of_credit"] == 500.00   # interest only
    assert q["total_repayable"] == 5500.00


# --------------------------------------------------------------------------- #
# Loan.total_due — flat principal + interest, as exact Decimal money.
# --------------------------------------------------------------------------- #
def test_loan_total_due_is_decimal(seed):
    loan = seed.make_loan(principal="10000.00", interest_rate="10.0")
    td = loan.total_due
    assert isinstance(td, Decimal)
    assert td == Decimal("11000.00")


def test_loan_total_due_fractional_rate(seed):
    loan = seed.make_loan(principal="7500.00", interest_rate="12.5")
    # 7500 * 1.125 = 8437.50
    assert loan.total_due == Decimal("8437.50")
