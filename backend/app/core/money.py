"""
MPESA-07 — precise money arithmetic.

M-Pesa amounts and loan balances are currency values, so the interest/principal
split and balance reductions must use decimal.Decimal (banker-safe, no binary
float drift), NOT native floats. Every helper here quantises to 2 decimal places
with ROUND_HALF_UP (standard money rounding) and returns a Decimal; callers store
it via SQLAlchemy Numeric columns (which accept Decimal directly).
"""
from decimal import Decimal, ROUND_HALF_UP

_CENTS = Decimal("0.01")


def D(value) -> Decimal:
    """Coerce any numeric/str/Decimal to Decimal via its string form (avoids
    re-introducing float error, e.g. Decimal(0.1))."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value if value is not None else 0))


def money(value) -> Decimal:
    """Quantise a value to 2dp money using ROUND_HALF_UP."""
    return D(value).quantize(_CENTS, rounding=ROUND_HALF_UP)


def split_interest_principal(amount, interest_rate) -> tuple[Decimal, Decimal]:
    """Split a repayment `amount` into (interest_component, principal_component)
    using the flat interest_rate (as a percentage, e.g. 10 for 10%).

    interest_share = rate / (100 + rate). Computed in Decimal, each component
    quantised to 2dp; principal is derived as amount - interest so the two
    components always sum back to the amount (no rounding gap).
    """
    amt = money(amount)
    rate = D(interest_rate)
    denom = Decimal(100) + rate
    if denom == 0:
        return Decimal("0.00"), amt
    interest = money(amt * (rate / denom))
    principal = money(amt - interest)
    return interest, principal


def reduce_balance(outstanding, amount) -> Decimal:
    """Reduce an outstanding balance by amount, floored at 0, quantised to 2dp."""
    new_bal = money(outstanding) - money(amount)
    if new_bal < 0:
        new_bal = Decimal("0.00")
    return new_bal
