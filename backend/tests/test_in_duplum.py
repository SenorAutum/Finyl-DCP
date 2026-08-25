"""
CBK *in duplum* statutory cap: total interest + charges on a loan may never
exceed the outstanding principal (charges capped at 100% of principal).

Asserts CURRENT behaviour of app.core.money.apply_in_duplum / in_duplum_cap.
"""
from decimal import Decimal

import pytest

from app.core.money import apply_in_duplum, in_duplum_cap, money


# --------------------------------------------------------------------------- #
# apply_in_duplum — caps a total-charges figure at the principal.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("principal,charges,expected", [
    ("1000.00", "500.00", "500.00"),    # below cap -> unchanged
    ("1000.00", "999.99", "999.99"),    # just below
    ("1000.00", "1000.00", "1000.00"),  # exactly at the boundary -> unchanged
    ("1000.00", "1000.01", "1000.00"),  # just beyond -> capped
    ("1000.00", "5000.00", "1000.00"),  # far beyond -> capped
])
def test_apply_in_duplum_caps_at_principal(principal, charges, expected):
    result = apply_in_duplum(principal, charges)
    assert result == Decimal(expected)
    # The invariant the rule protects: charges never exceed principal.
    assert result <= money(principal)


def test_apply_in_duplum_returns_decimal():
    assert isinstance(apply_in_duplum(1000, 200), Decimal)


# --------------------------------------------------------------------------- #
# in_duplum_cap — the max ADDITIONAL charge allowable this period.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("principal,accrued,new_charge,expected", [
    ("1000.00", "600.00", "300.00", "300.00"),  # fits within headroom (400)
    ("1000.00", "600.00", "400.00", "400.00"),  # exactly fills headroom
    ("1000.00", "600.00", "500.00", "400.00"),  # exceeds headroom -> clipped to 400
    ("1000.00", "1000.00", "100.00", "0.00"),   # at cap -> no more charges
    ("1000.00", "1200.00", "100.00", "0.00"),   # already over cap -> 0
    ("1000.00", "0.00", "50.00", "50.00"),      # fresh loan, small charge
])
def test_in_duplum_cap_headroom(principal, accrued, new_charge, expected):
    result = in_duplum_cap(principal, accrued, new_charge)
    assert result == Decimal(expected)
    # Invariant: applying the returned charge never pushes accrued over principal.
    assert money(accrued) + result <= money(principal) + Decimal("0.00") \
        or money(accrued) >= money(principal)


def test_in_duplum_cap_never_negative():
    assert in_duplum_cap(1000, 2000, 100) == Decimal("0.00")
    assert in_duplum_cap(1000, 2000, 100) >= 0


def test_in_duplum_cap_at_boundary_keeps_interest_le_principal():
    # Starting from zero accrued, no single capped charge can breach the principal.
    principal = "5000.00"
    accrued = Decimal("0.00")
    # Try to pile on charges larger than principal in one go.
    allowed = in_duplum_cap(principal, accrued, "9999.00")
    assert allowed == Decimal("5000.00")     # clipped to full principal headroom
    assert accrued + allowed == money(principal)
