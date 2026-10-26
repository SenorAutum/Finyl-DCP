"""
WP2 — one routine that applies money to a loan, used by EVERY channel
(paybill C2B callback, STK-push callback, manual entry, suspense allocation).

Before WP2 each channel had its own copy of the "record a repayment, reduce the
balance, maybe mark paid" logic, and they had drifted apart (e.g. a payment to a
defaulted loan was parked in suspense with no way out; an overpayment zeroed the
balance but left the loan un-paid). Centralising it here means the rules below
hold no matter how the money arrived:

  * Idempotent on the payment reference (M-Pesa TransID / bank ref) per DCP:
    the same reference can never be credited twice.
  * The caller must hold a row lock on the loan (SELECT ... FOR UPDATE) so two
    payments for one loan serialise.
  * Collectible statuses are active, overdue AND defaulted. Money for a loan in
    any other state (paid, pending, rejected ...) goes to suspense for review.
  * The payment is dated when the customer paid (M-Pesa TransTime / bank value
    date), not when we processed it.
  * Overpayment: only what is owed is applied; the loan is marked paid and the
    excess is parked in suspense (to be refunded or moved to another loan, with
    a second approver).
  * The split between interest, fees/penalties and principal follows the DCP's
    configured allocation order (see ALLOCATION_ORDERS / payment settings).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.core.money import D, money, split_interest_principal
from app.core.obs import log_money_event
from app.models import Loan, Repayment, SuspenseEntry, TenantIntegrationConfig

logger = logging.getLogger("finyl.repayments")

COLLECTIBLE_STATUSES = ("active", "overdue", "defaulted")
ZERO = Decimal("0.00")

# --------------------------------------------------------------------------- #
# Allocation order (per DCP)
# --------------------------------------------------------------------------- #
# CBK Non-Deposit Taking Credit Providers Regulations, 2026 (LN 191),
# regulation 56(2): a repayment is allocated to (a) due principal, (b)
# outstanding fees and charges, (c) interest due. That is the default.
# "proportional" is how the platform behaved before WP2 (each payment split
# interest:principal in the flat-rate ratio) and is kept only for a DCP that
# must reproduce historic statements; the 2025 draft's interest-first order is
# kept for the same reason. Neither is the regulation's order.
ALLOCATION_ORDERS = {
    "principal_fees_interest": ("principal", "fees", "interest"),
    "proportional": None,
    "interest_fees_principal": ("interest", "fees", "principal"),
}
ALLOCATION_ORDER_LABELS = {
    "principal_fees_interest": "Principal first, then fees/charges, then interest (CBK LN 191/2026 reg. 56(2))",
    "proportional": "Proportional interest/principal split (pre-2026 behaviour)",
    "interest_fees_principal": "Interest first, then fees/charges, then principal (2025 draft rules)",
}
DEFAULT_ALLOCATION_ORDER = "principal_fees_interest"
PAYMENTS_INTEGRATION = "payments"


# Early full repayment (CBK LN 191/2026 regulation 56(4)): a borrower who repays
# in full before maturity "shall not be required to pay interest for the
# remaining period to maturity". The platform charges flat interest for the whole
# term on disbursement, so on an early payoff the interest for the unused time is
# written off. How the used time is counted is a per-DCP setting; both options
# only ever charge for time the borrower actually had the money:
#   * daily (default) — interest x days used / days in the term (minimum 1 day).
#     The regulation speaks of "the remaining period", and leading Kenyan digital
#     lenders charge "only for the days" a loan is used, so a day is the unit.
#   * completed_periods — interest only for whole weeks/months (per the product's
#     tenure unit) fully completed; never more than the daily method.
# There is deliberately NO option that keeps the full term's interest.
EARLY_SETTLEMENT_METHODS = {
    "daily": "Day by day: interest only for the days the loan was held (CBK LN 191/2026 reg. 56(4))",
    "completed_periods": "Completed weeks/months only (charges less than day by day)",
}
DEFAULT_EARLY_SETTLEMENT = "daily"


def get_payment_settings(db, tenant_id: int) -> dict:
    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == PAYMENTS_INTEGRATION).first())
    cfg = dict((row.config or {}) if row else {})
    order = cfg.get("allocation_order")
    if order not in ALLOCATION_ORDERS:
        order = DEFAULT_ALLOCATION_ORDER
    early = cfg.get("early_settlement_method")
    if early not in EARLY_SETTLEMENT_METHODS:
        early = DEFAULT_EARLY_SETTLEMENT
    return {
        "allocation_order": order,
        "allocation_order_label": ALLOCATION_ORDER_LABELS[order],
        "allocation_order_is_default": cfg.get("allocation_order") not in ALLOCATION_ORDERS,
        "allocation_order_basis": cfg.get("allocation_order_basis"),
        "early_settlement_method": early,
        "early_settlement_label": EARLY_SETTLEMENT_METHODS[early],
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


def set_payment_settings(db, tenant_id: int, user_id: int, *, allocation_order: str | None = None,
                         basis: str | None = None,
                         early_settlement_method: str | None = None) -> dict:
    if allocation_order is not None and allocation_order not in ALLOCATION_ORDERS:
        raise ValueError(f"allocation_order must be one of {list(ALLOCATION_ORDERS)}")
    if (early_settlement_method is not None
            and early_settlement_method not in EARLY_SETTLEMENT_METHODS):
        raise ValueError("early_settlement_method must be one of "
                         f"{list(EARLY_SETTLEMENT_METHODS)}")
    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == PAYMENTS_INTEGRATION).first())
    cfg = dict((row.config or {}) if row else {})
    if allocation_order is not None:
        cfg["allocation_order"] = allocation_order
        cfg["allocation_order_basis"] = (basis or "").strip() or None
    if early_settlement_method is not None:
        cfg["early_settlement_method"] = early_settlement_method
    if row is None:
        row = TenantIntegrationConfig(tenant_id=tenant_id, integration=PAYMENTS_INTEGRATION,
                                      config=cfg, secrets={}, enabled=True,
                                      updated_by_user_id=user_id)
        db.add(row)
    else:
        row.config = cfg
        row.updated_by_user_id = user_id
    db.flush()
    return get_payment_settings(db, tenant_id)


# --------------------------------------------------------------------------- #
# What is owed, by component
# --------------------------------------------------------------------------- #
def owed_breakdown(db, loan: Loan) -> dict:
    """Split the loan's single outstanding_balance into principal / interest /
    fees (penalties & other charges) still owed.

    The platform stores one balance per loan; the components are derived from
    the original terms minus what earlier repayments were allocated to:
      principal owed = principal - principal already repaid
      interest owed  = flat interest (principal x rate%) - interest already repaid
      fees owed      = whatever remains of the balance (penalties accrued on top)
    If the balance is LOWER than principal+interest owed (e.g. a manual
    adjustment reduced it), interest is reduced first so the three parts always
    add up to exactly the outstanding balance."""
    sums = (db.query(func.coalesce(func.sum(Repayment.principal_component), 0),
                     func.coalesce(func.sum(Repayment.interest_component), 0),
                     func.coalesce(func.sum(Repayment.fee_component), 0))
            .filter(Repayment.loan_id == loan.id, Repayment.tenant_id == loan.tenant_id)
            .one())
    p_paid, i_paid, _f_paid = (money(s) for s in sums)
    outstanding = money(loan.outstanding_balance or 0)
    principal_total = money(loan.principal or 0)
    interest_total = money(D(loan.principal or 0) * D(loan.interest_rate or 0) / Decimal(100))

    principal_owed = max(principal_total - p_paid, ZERO)
    interest_owed = max(interest_total - i_paid, ZERO)
    fees_owed = outstanding - principal_owed - interest_owed
    if fees_owed < 0:
        # The balance is lower than the recorded split explains: money came in
        # without a principal/interest split (the CSV batch upload, older data).
        # Treat that gap like an ordinary payment of the time, split in the
        # loan's flat interest:principal ratio, rather than assuming it was all
        # interest (which would understate the interest still owed and, on an
        # early payoff, the interest the borrower is entitled to have dropped).
        gap = -fees_owed
        fees_owed = ZERO
        gi, gp = split_interest_principal(gap, loan.interest_rate)
        gi = min(gi, interest_owed)
        gp = gap - gi
        if gp > principal_owed:
            gi = min(interest_owed, gi + (gp - principal_owed))
            gp = principal_owed
        interest_owed -= gi
        principal_owed -= gp
    return {"principal": money(principal_owed), "interest": money(interest_owed),
            "fees": money(fees_owed), "total": outstanding,
            "interest_paid": money(interest_total - interest_owed)}


def allocate(db, loan: Loan, amount, order_key: str) -> dict:
    """Return {"interest", "fees", "principal"} for applying `amount` (which must
    not exceed the outstanding balance) under the given allocation order."""
    amount = money(amount)
    order = ALLOCATION_ORDERS.get(order_key)
    if order is None:  # proportional (legacy)
        interest, principal = split_interest_principal(amount, loan.interest_rate)
        return {"interest": interest, "fees": ZERO, "principal": principal}
    owed = owed_breakdown(db, loan)
    out = {"interest": ZERO, "fees": ZERO, "principal": ZERO}
    remaining = amount
    for part in order:
        take = min(remaining, owed[part])
        out[part] = money(take)
        remaining -= take
    if remaining > 0:
        # Rounding residue only (amount <= outstanding == sum of owed parts).
        out[order[-1]] = money(out[order[-1]] + remaining)
    return out


# --------------------------------------------------------------------------- #
# Early full repayment — payoff quote (CBK LN 191/2026 reg. 56(4))
# --------------------------------------------------------------------------- #
EAT_OFFSET = timedelta(hours=3)


def kenya_date(when: datetime | date | None) -> date:
    """Calendar day in Kenya (EAT) for a naive-UTC timestamp (or a date as-is)."""
    if when is None:
        return (datetime.utcnow() + EAT_OFFSET).date()
    if isinstance(when, datetime):
        return (when + EAT_OFFSET).date()
    return when


def _period_days(loan: Loan) -> int:
    product = getattr(loan, "product", None)
    # Same period lengths the disbursement step uses to set the due date.
    return 7 if product is not None and product.tenure_unit == "weeks" else 30


def earned_interest_fraction(loan: Loan, on: date, method: str) -> tuple[Decimal, dict]:
    """Share of the term's interest earned by day `on` (0..1), plus the counts
    used, so the quote can show the borrower how it was worked out."""
    start, due = loan.disbursement_date, loan.due_date
    term_days = (due - start).days
    elapsed = max((on - start).days, 0)
    if method == "completed_periods":
        period = _period_days(loan)
        total_periods = max(round(term_days / period), 1)
        done = min(elapsed // period, total_periods)
        return Decimal(done) / Decimal(total_periods), {
            "method": method, "periods_completed": done, "periods_in_term": total_periods,
            "days_used": elapsed, "days_in_term": term_days}
    used = min(max(elapsed, 1), term_days)  # at least one day's interest
    return Decimal(used) / Decimal(term_days), {
        "method": "daily", "days_used": used, "days_in_term": term_days}


def payoff_quote(db, loan: Loan, on: date | datetime | None = None,
                 method: str | None = None) -> dict:
    """What the borrower must pay on day `on` (Kenya date) to clear the loan.

    Before the due date only the interest earned so far is payable; the rest of
    the term's interest (the rebate) is written off when the loan is settled.
    On or after the due date the full outstanding balance is payable."""
    on = kenya_date(on)
    owed = owed_breakdown(db, loan)
    outstanding = owed["total"]
    base = {"as_of": on.isoformat(), "outstanding_balance": float(outstanding),
            "early": False, "rebate": 0.0, "payoff_amount": float(outstanding),
            "principal_owed": float(owed["principal"]), "fees_owed": float(owed["fees"]),
            "interest_owed": float(owed["interest"]), "credit": 0.0, "basis": None}
    if (loan.status not in COLLECTIBLE_STATUSES or outstanding <= 0
            or not loan.disbursement_date or not loan.due_date
            or (loan.due_date - loan.disbursement_date).days <= 0
            or on >= loan.due_date):
        return base

    method = method or get_payment_settings(db, loan.tenant_id)["early_settlement_method"]
    fraction, counts = earned_interest_fraction(loan, on, method)
    interest_total = money(D(loan.principal or 0) * D(loan.interest_rate or 0) / Decimal(100))
    earned = money(interest_total * fraction)
    interest_paid = owed["interest_paid"]   # recorded split + any unsplit payments
    earned_owed = earned - interest_paid
    credit = ZERO
    if earned_owed < 0:
        # Interest already paid exceeds what has been earned (possible under the
        # proportional split): the surplus counts towards the payoff.
        credit, earned_owed = -earned_owed, ZERO
    earned_owed = min(earned_owed, owed["interest"])
    rebate = money(owed["interest"] - earned_owed)
    raw = owed["principal"] + owed["fees"] + earned_owed - credit
    payoff = money(max(raw, ZERO))
    credit_left = money(max(-raw, ZERO))
    return {**base, "early": True, "rebate": float(rebate), "payoff_amount": float(payoff),
            "interest_owed": float(earned_owed), "credit": float(credit_left),
            "interest_full_term": float(interest_total), "interest_earned": float(earned),
            "basis": "CBK LN 191/2026 reg. 56(4)", **counts}


# --------------------------------------------------------------------------- #
# Suspense + duplicate detection
# --------------------------------------------------------------------------- #
def reference_already_used(db, tenant_id: int, ref: str | None,
                           include_suspense: bool = True) -> str | None:
    """'repayment' / 'suspense' when this payment reference was already recorded
    for this DCP, else None. Used by every channel before taking money in."""
    if not ref:
        return None
    if (db.query(Repayment.id)
            .filter(Repayment.tenant_id == tenant_id, Repayment.mpesa_ref == str(ref))
            .first()):
        return "repayment"
    if include_suspense and (db.query(SuspenseEntry.id)
            .filter(SuspenseEntry.tenant_id == tenant_id, SuspenseEntry.mpesa_ref == str(ref))
            .first()):
        return "suspense"
    return None


def record_suspense(db, *, tenant_id: int, reason: str, amount, ref, phone=None,
                    loan_id=None, suggested_loan_id=None, source="c2b", bill_ref=None,
                    payer_name=None, paid_at=None, raw_payload=None) -> SuspenseEntry:
    """Park money that cannot be applied automatically. Idempotent on
    (tenant_id, ref). Does NOT commit — the caller owns the transaction."""
    if ref:
        existing = (db.query(SuspenseEntry)
                    .filter(SuspenseEntry.tenant_id == tenant_id,
                            SuspenseEntry.mpesa_ref == str(ref)).first())
        if existing is not None:
            return existing
    entry = SuspenseEntry(tenant_id=tenant_id, source=source,
                          mpesa_ref=(str(ref) if ref else None), phone=phone,
                          amount=money(amount), reason=reason, status="open",
                          matched_loan_id=loan_id, suggested_loan_id=suggested_loan_id,
                          bill_ref=(str(bill_ref)[:60] if bill_ref else None),
                          payer_name=(str(payer_name)[:120] if payer_name else None),
                          paid_at=paid_at, raw_payload=raw_payload or {})
    db.add(entry)
    return entry


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #
@dataclass
class ApplyResult:
    status: str                      # applied | duplicate_ignored | suspense
    loan_id: int | None = None
    applied: Decimal = ZERO
    excess: Decimal = ZERO
    components: dict = field(default_factory=dict)
    outstanding_balance: Decimal | None = None
    loan_status: str | None = None
    suspense_id: int | None = None
    repayment_id: int | None = None
    ref: str | None = None
    rebate: Decimal = ZERO

    def as_dict(self) -> dict:
        return {
            "status": self.status, "loan_id": self.loan_id,
            "applied": float(self.applied), "excess": float(self.excess),
            "components": {k: float(v) for k, v in self.components.items()},
            "outstanding_balance": (float(self.outstanding_balance)
                                    if self.outstanding_balance is not None else None),
            "loan_status": self.loan_status, "suspense_id": self.suspense_id,
            "repayment_id": self.repayment_id, "ref": self.ref,
            "interest_rebate": float(self.rebate),
        }


def internal_ref(prefix: str = "MAN") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def apply_payment(db, *, tenant_id: int, loan: Loan, amount, ref: str | None,
                  method: str, paid_at: datetime | None = None, phone: str | None = None,
                  source: str = "c2b", bill_ref: str | None = None,
                  payer_name: str | None = None, actor_user_id: int | None = None,
                  send_receipt: bool = True, from_suspense: bool = False) -> ApplyResult:
    """Apply a received payment to `loan` (already row-locked by the caller).

    `from_suspense=True` when the money is being moved OUT of a suspense entry
    that carries the same reference (then only repayments count as duplicates).

    Never commits. Raises ValueError for a non-positive amount or a loan that
    belongs to another DCP."""
    amount = money(amount)
    if amount <= 0:
        raise ValueError("Payment amount must be positive")
    if loan.tenant_id != tenant_id:
        raise ValueError("Loan belongs to another DCP")
    ref = str(ref) if ref else internal_ref()
    paid_at = paid_at or datetime.utcnow()

    used = reference_already_used(db, tenant_id, ref, include_suspense=not from_suspense)
    if used is not None:
        existing = (db.query(Repayment)
                    .filter(Repayment.tenant_id == tenant_id, Repayment.mpesa_ref == ref).first())
        return ApplyResult(status="duplicate_ignored", loan_id=loan.id, ref=ref,
                           repayment_id=existing.id if existing else None,
                           outstanding_balance=money(loan.outstanding_balance or 0),
                           loan_status=loan.status)

    if loan.status not in COLLECTIBLE_STATUSES:
        entry = record_suspense(db, tenant_id=tenant_id, reason="closed_loan", amount=amount,
                                ref=ref, phone=phone, loan_id=None, suggested_loan_id=loan.id,
                                source=source, bill_ref=bill_ref, payer_name=payer_name,
                                paid_at=paid_at,
                                raw_payload={"review_reason": f"loan_status_{loan.status}"})
        db.flush()
        log_money_event("payment_to_suspense", tenant_id=tenant_id, user_id=actor_user_id,
                        loan_id=loan.id, amount=amount, phone=phone, ref=ref,
                        detail=f"loan_status={loan.status}")
        return ApplyResult(status="suspense", loan_id=loan.id, excess=amount, ref=ref,
                           suspense_id=entry.id, loan_status=loan.status,
                           outstanding_balance=money(loan.outstanding_balance or 0))

    outstanding = money(loan.outstanding_balance or 0)
    settings_now = get_payment_settings(db, tenant_id)
    order = settings_now["allocation_order"]

    # CBK LN 191/2026 reg. 56(4): repaid in full before maturity -> no interest
    # for the remaining period. If this payment covers the early payoff amount,
    # take only that and write off the unearned interest.
    quote = payoff_quote(db, loan, paid_at, settings_now["early_settlement_method"])
    rebate = ZERO
    interest_credit = ZERO
    if quote["early"] and amount >= money(quote["payoff_amount"]):
        applied = money(quote["payoff_amount"])
        rebate = money(quote["rebate"])
        interest_credit = money(quote["credit"])
        owed_now = {"principal": money(quote["principal_owed"]), "fees": money(quote["fees_owed"]),
                    "interest": money(quote["interest_owed"])}
        components = {"interest": ZERO, "fees": ZERO, "principal": ZERO}
        remaining = applied
        for part in (ALLOCATION_ORDERS.get(order) or ("principal", "fees", "interest")):
            take = min(remaining, owed_now[part])
            components[part] = money(take)
            remaining -= take
    else:
        applied = min(amount, outstanding)
        components = None
    excess = money(amount - applied)
    if components is None:
        components = {"interest": ZERO, "fees": ZERO, "principal": ZERO}
        if applied > 0:
            components = allocate(db, loan, applied, order)
    rep = None
    if applied > 0:
        rep = Repayment(tenant_id=tenant_id, loan_id=loan.id, amount=applied,
                        interest_component=components["interest"],
                        fee_component=components["fees"],
                        principal_component=components["principal"],
                        payment_date=paid_at, method=method[:20], mpesa_ref=ref)
        db.add(rep)
        loan.outstanding_balance = money(outstanding - applied)

    if quote["early"] and amount >= money(quote["payoff_amount"]):
        # Settled early: clear what is left (the unearned interest) and record it.
        loan.outstanding_balance = ZERO
        loan.interest_rebate = money(D(loan.interest_rebate or 0) + rebate)
        loan.early_settled_at = paid_at
        log_money_event("early_settlement_rebate", tenant_id=tenant_id, user_id=actor_user_id,
                        loan_id=loan.id, amount=rebate, ref=ref,
                        detail=(f"method={quote.get('method')} used={quote.get('days_used')}"
                                f"/{quote.get('days_in_term')}d payoff={quote['payoff_amount']}"))

    if money(loan.outstanding_balance or 0) <= 0 and loan.status in COLLECTIBLE_STATUSES:
        loan.status = "paid"
        try:
            from app.routers.lending import _release_active_lock
            _release_active_lock(db, loan, None)
        except Exception:  # noqa: BLE001 — lock bookkeeping must not block money
            logger.exception("active_lock_release_failed loan_id=%s", loan.id)

    suspense_id = None
    if excess > 0:
        entry = record_suspense(db, tenant_id=tenant_id, reason="overpayment", amount=excess,
                                # Distinct ref so the overpayment row is not confused
                                # with a fully unapplied receipt of the same code.
                                ref=f"{ref}-OVR", phone=phone, loan_id=loan.id,
                                suggested_loan_id=loan.id, source=source,
                                bill_ref=bill_ref, payer_name=payer_name, paid_at=paid_at,
                                raw_payload={"original_ref": ref,
                                             "amount_received": str(amount),
                                             "outstanding_at_receipt": str(outstanding)})
        db.flush()
        suspense_id = entry.id
    if interest_credit > 0:
        # Interest paid earlier exceeded what was earned by the payoff date:
        # owed back to the borrower — parked for refund with a second approver.
        record_suspense(db, tenant_id=tenant_id, reason="interest_refund_due",
                        amount=interest_credit, ref=f"{ref}-INT", phone=phone,
                        loan_id=loan.id, suggested_loan_id=loan.id, source=source,
                        paid_at=paid_at, raw_payload={"original_ref": ref,
                                                      "basis": "CBK LN 191/2026 reg. 56(4)"})
    db.flush()

    log_money_event("payment_applied", tenant_id=tenant_id, user_id=actor_user_id,
                    loan_id=loan.id, amount=amount, phone=phone, ref=ref,
                    detail=(f"method={method} applied={applied} excess={excess} "
                            f"outstanding={loan.outstanding_balance} status={loan.status}"))

    if send_receipt and applied > 0:
        try:
            from app.services import sms
            if loan.borrower is not None:
                sms.sms_payment_receipt(db, tenant_id, loan.borrower, loan, applied, ref)
        except Exception:  # noqa: BLE001 — an SMS failure never undoes a payment
            logger.exception("payment_receipt_sms_failed loan_id=%s", loan.id)

    return ApplyResult(status="applied", loan_id=loan.id, applied=applied, excess=excess,
                       components=components, outstanding_balance=money(loan.outstanding_balance),
                       loan_status=loan.status, suspense_id=suspense_id,
                       repayment_id=rep.id if rep is not None else None, ref=ref,
                       rebate=rebate)
