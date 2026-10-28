"""
WP5 (F23) — one pricing calculation shared by the quote, loan creation, the
payout and the balance, so what the borrower is shown is what they are charged.

Before WP5 the quote added product fees + 20% excise duty (e.g. KES 11,480 for
KES 10,000 at 10%) but the loan balance was set to principal + interest only
(KES 11,000): fees were quoted and never collected.

Each product now chooses how its fees are collected (product.rules
"fee_collection"):
  * "deduct"          (default) fees + excise are taken from the payout:
                       borrower receives KES 9,520, owes KES 11,000.
  * "add_to_balance"  borrower receives KES 10,000, owes KES 11,480; the fees are
                       repaid as "fees and charges" (after principal, before
                       interest, under LN 191/2026 reg. 56(2)).

A loan stores a snapshot of its price when it is created (loans.pricing_snapshot)
and that snapshot — not the product's current settings — drives the payout and
the balance. Fees are never refunded on early settlement (reg. 56(4) covers
interest only).
"""
from __future__ import annotations

import math
from decimal import Decimal

from app.core.money import D, money

FEE_COLLECTION_METHODS = {
    "deduct": "Deduct fees and excise duty from the payout",
    "add_to_balance": "Add fees and excise duty to the loan balance",
}
DEFAULT_FEE_COLLECTION = "deduct"
EXCISE_RATE = D("0.20")   # Kenya excise duty on fees charged by lenders


def fee_collection(product) -> str:
    method = ((getattr(product, "rules", None) or {}).get("fee_collection") or "").strip()
    return method if method in FEE_COLLECTION_METHODS else DEFAULT_FEE_COLLECTION


def quote_breakdown(product, principal) -> dict:
    """Decimal-accurate price of a loan of `principal` on `product`. No mutation.

    Fee configuration is read from ``product.rules`` (optional keys, default 0):
      - ``processing_fee_rate``: percent of principal
      - ``facility_fee``: flat amount
      - ``fee_collection``: deduct | add_to_balance (default deduct)
    Kenya excise duty is levied at 20% of total fees."""
    principal = money(principal)
    rate = D(product.interest_rate or 0)                 # % over the product tenure
    interest = money(principal * rate / D(100))

    rules = getattr(product, "rules", None) or {}
    try:
        proc_rate = D(str(rules.get("processing_fee_rate", 0) or 0))
    except Exception:  # noqa: BLE001
        proc_rate = D(0)
    try:
        facility_flat = D(str(rules.get("facility_fee", 0) or 0))
    except Exception:  # noqa: BLE001
        facility_flat = D(0)

    processing_fee = money(principal * proc_rate / D(100))
    facility_fee = money(facility_flat)
    total_fees = money(processing_fee + facility_fee)
    excise_duty = money(total_fees * EXCISE_RATE)
    fees_and_excise = money(total_fees + excise_duty)
    total_cost_of_credit = money(interest + fees_and_excise)
    method = fee_collection(product)

    if method == "deduct":
        # M-Pesa pays whole shillings; round the payout UP so the borrower is
        # never charged more than quoted (the DCP absorbs the cents).
        net = principal - fees_and_excise
        payout = money(Decimal(math.ceil(net))) if net > 0 else D("0.00")
        balance_at_disbursement = money(principal + interest)
        fees_in_balance = D("0.00")
    else:
        payout = money(Decimal(math.ceil(principal)))
        fees_in_balance = fees_and_excise
        balance_at_disbursement = money(principal + interest + fees_and_excise)

    tenure_value = max(1, int(getattr(product, "tenure_value", 1) or 1))
    unit = (getattr(product, "tenure_unit", None) or "weeks").lower()
    if unit == "weeks":
        years = D(tenure_value) * D(7) / D(365)
    elif unit == "months":
        years = D(tenure_value) / D(12)
    else:
        years = D(tenure_value) / D(365)
    if years <= 0:
        years = D(1)
    # Annualised on what the borrower actually receives (a deducted fee raises it).
    base = payout if payout > 0 else principal
    effective_annual_rate = (money((total_cost_of_credit / base) / years * D(100))
                             if base > 0 else D(0))

    return {
        "product_id": getattr(product, "id", None),
        "product_name": getattr(product, "name", None),
        "principal": float(principal),
        "interest_rate": float(rate),
        "interest_method": getattr(product, "interest_method", None),
        "tenure_value": tenure_value,
        "tenure_unit": getattr(product, "tenure_unit", None),
        "interest": float(interest),
        "processing_fee": float(processing_fee),
        "facility_fee": float(facility_fee),
        "total_fees": float(total_fees),
        "excise_duty": float(excise_duty),
        "excise_rate": float(EXCISE_RATE),
        "total_cost_of_credit": float(total_cost_of_credit),
        # What the borrower must repay (the balance on disbursement) and what
        # reaches their M-Pesa. Either way: repayable - received = cost of credit
        # (up to the shilling the payout is rounded in the borrower's favour).
        "total_repayable": float(balance_at_disbursement),
        "amount_received": float(payout),
        "effective_annual_rate": float(effective_annual_rate),
        "fee_collection": method,
        "fee_collection_label": FEE_COLLECTION_METHODS[method],
        "payout_amount": float(payout),
        "balance_at_disbursement": float(balance_at_disbursement),
        "fees_in_balance": float(fees_in_balance),
    }


def loan_snapshot(loan, product) -> dict:
    """The loan's agreed price: its stored snapshot when it matches the loan's
    principal, otherwise (older loans, or a principal changed after creation)
    a fresh calculation from the product."""
    snap = getattr(loan, "pricing_snapshot", None) or {}
    if snap and money(snap.get("principal", -1)) == money(loan.principal):
        return snap
    return quote_breakdown(product, loan.principal)
