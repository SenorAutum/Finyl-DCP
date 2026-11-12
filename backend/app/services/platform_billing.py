"""WP13 — Finyl's charges to DCPs (Fincentral pricing model, Autum's prices).

Standard price list (each DCP can have its own):
  * onboarding and implementation: KES 50,000 once, on the first invoice;
  * monthly subscription: 1% of the DCP's outstanding loan book on the last day
    of the month, at least KES 30,000;
  * new branch set-up: KES 20,000 per branch beyond those covered by onboarding
    (1 by default), charged once on the next invoice;
  * SMS: the messages the DCP sent in the month, at the SMS rate card price.

Invoices are produced on the 1st of each month (Kenyan time) for the month
before, one per DCP and month (producing it twice is impossible). Payments can
be recorded by hand or arrive on Finyl's own paybill with the invoice number
as the account.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func

from app.models import (Branch, PlatformInvoice, PlatformInvoiceLine, PlatformPayment,
                        PlatformPricing, PlatformTenantDaily, SmsLog, Tenant, TenantBilling)

CENT = Decimal("0.01")


def _d(x) -> Decimal:
    return Decimal(str(x or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


class BillingError(ValueError):
    """Shown to the user."""


def pricing(db) -> PlatformPricing:
    row = db.query(PlatformPricing).order_by(PlatformPricing.id).first()
    if row is None:
        row = PlatformPricing(onboarding_fee=50000, subscription_rate_pct=1,
                              subscription_minimum=30000, branch_fee=20000, branches_included=1,
                              bill_sms=True, payment_terms_days=14, invoice_day=1)
        db.add(row)
        db.flush()
    return row


def pricing_dict(p: PlatformPricing) -> dict:
    return {"onboarding_fee": float(p.onboarding_fee), "subscription_rate_pct": float(p.subscription_rate_pct),
            "subscription_minimum": float(p.subscription_minimum), "branch_fee": float(p.branch_fee),
            "branches_included": p.branches_included, "bill_sms": p.bill_sms,
            "payment_terms_days": p.payment_terms_days, "invoice_day": p.invoice_day}


def billing_for(db, tenant_id: int) -> TenantBilling:
    row = db.get(TenantBilling, tenant_id)
    if row is None:
        row = TenantBilling(tenant_id=tenant_id, billing_active=False, branches_billed=0,
                            onboarding_invoiced=False)
        db.add(row)
        db.flush()
    return row


def switch_on(db, b: TenantBilling, start: date | None = None) -> None:
    """Start billing a DCP. The branches it already has are covered by the
    onboarding fee; only branches opened afterwards are charged as new."""
    from app.services.platform_metrics import today_kenya
    first_time = not b.billing_start
    b.billing_active = True
    if first_time:
        b.billing_start = start or today_kenya().replace(day=1)
        included = effective(db, b.tenant_id)["branches_included"]
        existing = db.query(func.count(Branch.id)).filter(Branch.tenant_id == b.tenant_id).scalar() or 0
        b.branches_billed = max(b.branches_billed or 0, existing - included)
    db.flush()


def effective(db, tenant_id: int) -> dict:
    """The prices that apply to this DCP (its own where set, else the standard)."""
    p, b = pricing(db), billing_for(db, tenant_id)

    def pick(own, std):
        return own if own is not None else std
    return {"onboarding_fee": _d(pick(b.onboarding_fee, p.onboarding_fee)),
            "subscription_rate_pct": Decimal(str(pick(b.subscription_rate_pct, p.subscription_rate_pct))),
            "subscription_minimum": _d(pick(b.subscription_minimum, p.subscription_minimum)),
            "branch_fee": _d(pick(b.branch_fee, p.branch_fee)),
            "branches_included": int(pick(b.branches_included, p.branches_included)),
            "bill_sms": bool(p.bill_sms), "payment_terms_days": int(p.payment_terms_days)}


def billing_dict(db, tenant_id: int) -> dict:
    b = billing_for(db, tenant_id)
    eff = effective(db, tenant_id)
    own = {k: (float(getattr(b, k)) if getattr(b, k) is not None else None)
           for k in ("onboarding_fee", "subscription_rate_pct", "subscription_minimum", "branch_fee")}
    own["branches_included"] = b.branches_included
    return {"tenant_id": tenant_id, "billing_active": bool(b.billing_active),
            "billing_start": b.billing_start.isoformat() if b.billing_start else None,
            "own_prices": own, "effective": {k: (float(v) if isinstance(v, Decimal) else v)
                                             for k, v in eff.items()},
            "onboarding_invoiced": bool(b.onboarding_invoiced), "branches_billed": b.branches_billed or 0,
            "billing_email": b.billing_email, "notes": b.notes}


def _book_at(db, tenant_id: int, day: date) -> Decimal:
    """The loan book on the last day of the period: the nightly snapshot of
    that day (or the closest one in the week before), else today's figure."""
    from app.services.platform_metrics import loan_book
    row = (db.query(PlatformTenantDaily.outstanding)
           .filter(PlatformTenantDaily.tenant_id == tenant_id,
                   PlatformTenantDaily.day <= day, PlatformTenantDaily.day >= day - timedelta(days=7))
           .order_by(PlatformTenantDaily.day.desc()).first())
    if row is not None:
        return _d(row[0])
    return _d(loan_book(db, tenant_id)["outstanding"])


def _next_number(db, period_start: date) -> str:
    prefix = f"INV-{period_start:%Y-%m}-"
    n = db.query(func.count(PlatformInvoice.id)).filter(PlatformInvoice.number.like(prefix + "%")).scalar()
    return f"{prefix}{int(n or 0) + 1:04d}"


def month_bounds(period: str) -> tuple[date, date]:
    """'2026-10' -> (1 Oct 2026, 31 Oct 2026)."""
    try:
        y, m = (int(x) for x in period.split("-"))
        start = date(y, m, 1)
    except (ValueError, TypeError):
        raise BillingError("The month must look like 2026-10.")
    end = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
    return start, end


def generate(db, tenant_id: int, period: str, user_id: int | None = None) -> PlatformInvoice:
    """The invoice for one DCP and month. Refuses a second one for the same month."""
    start, end = month_bounds(period)
    if db.query(PlatformInvoice).filter(PlatformInvoice.tenant_id == tenant_id,
                                        PlatformInvoice.period_start == start,
                                        PlatformInvoice.status != "void").first():
        raise BillingError(f"There is already an invoice for {start:%B %Y}.")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise BillingError("DCP not found")
    b = billing_for(db, tenant_id)
    if b.billing_start and end < b.billing_start:
        raise BillingError(f"Billing for {tenant.name} starts on {b.billing_start:%d %B %Y}.")
    eff = effective(db, tenant_id)
    lines = []
    if not b.onboarding_invoiced and eff["onboarding_fee"] > 0:
        lines.append(("onboarding", "System onboarding and implementation (one-off)", 1,
                      eff["onboarding_fee"], eff["onboarding_fee"]))
        b.onboarding_invoiced = True
    book = _book_at(db, tenant_id, end)
    pct = eff["subscription_rate_pct"]
    fee = _d(book * pct / 100)
    sub = max(fee, eff["subscription_minimum"])
    desc = (f"Monthly subscription, {start:%B %Y}: {pct.normalize()}% of the loan book of KES "
            f"{book:,.2f} on {end:%d %b %Y}")
    if sub > fee:
        desc += f" (minimum KES {eff['subscription_minimum']:,.0f} applies)"
    lines.append(("subscription", desc, 1, sub, sub))
    branches = db.query(func.count(Branch.id)).filter(Branch.tenant_id == tenant_id).scalar() or 0
    extra = max(0, branches - eff["branches_included"] - (b.branches_billed or 0))
    if extra and eff["branch_fee"] > 0:
        lines.append(("branch", f"New branch set-up ({extra} branch{'es' if extra > 1 else ''})",
                      extra, eff["branch_fee"], _d(eff["branch_fee"] * extra)))
        b.branches_billed = (b.branches_billed or 0) + extra
    if eff["bill_sms"]:
        since = datetime(start.year, start.month, start.day) - timedelta(hours=3)
        until = datetime(end.year, end.month, end.day) + timedelta(hours=21)
        cnt, rev = (db.query(func.count(SmsLog.id), func.coalesce(func.sum(SmsLog.sell_price_kes), 0))
                    .filter(SmsLog.tenant_id == tenant_id, SmsLog.billable == True,  # noqa: E712
                            SmsLog.sent_at >= since, SmsLog.sent_at < until).one())
        if cnt:
            rev = _d(rev)
            lines.append(("sms", f"SMS sent in {start:%B %Y}", int(cnt),
                          (rev / cnt).quantize(Decimal("0.0001")), rev))
    inv = PlatformInvoice(number=_next_number(db, start), tenant_id=tenant_id, period_start=start,
                          period_end=end, issued_at=datetime.utcnow(),
                          due_date=date.today() + timedelta(days=eff["payment_terms_days"]),
                          status="issued", total=_d(sum(x[4] for x in lines)), paid=Decimal("0"),
                          outstanding_book=book, created_by_user_id=user_id)
    db.add(inv)
    db.flush()
    for kind, desc, qty, unit, amount in lines:
        db.add(PlatformInvoiceLine(invoice_id=inv.id, kind=kind, description=desc[:300],
                                   quantity=qty, unit_price=unit, amount=_d(amount)))
    db.flush()
    return inv


def generate_due(db, today: date | None = None) -> dict:
    """Monthly job: last month's invoice for every DCP with billing switched on."""
    from app.services.platform_metrics import today_kenya
    today = today or today_kenya()
    last = today.replace(day=1) - timedelta(days=1)
    period = f"{last:%Y-%m}"
    made, skipped = 0, 0
    for b in db.query(TenantBilling).filter(TenantBilling.billing_active == True).all():  # noqa: E712
        try:
            generate(db, b.tenant_id, period)
            made += 1
        except BillingError:
            skipped += 1
    db.commit()
    return {"period": period, "made": made, "skipped": skipped}


def record_payment(db, inv: PlatformInvoice, amount, reference: str, paid_at: datetime,
                   channel: str = "manual", note: str | None = None, user_id: int | None = None):
    amount = _d(amount)
    if amount <= 0:
        raise BillingError("The amount must be above zero.")
    if inv.status == "void":
        raise BillingError("This invoice was cancelled.")
    reference = (reference or "").strip().upper()[:40]
    if not reference:
        raise BillingError("Enter the payment reference (M-Pesa code or bank reference).")
    if db.query(PlatformPayment).filter(PlatformPayment.reference == reference).first():
        raise BillingError("This reference is already recorded.")
    p = PlatformPayment(tenant_id=inv.tenant_id, invoice_id=inv.id, amount=amount, reference=reference,
                        channel=channel, paid_at=paid_at, note=note, recorded_by_user_id=user_id)
    db.add(p)
    inv.paid = _d(inv.paid) + amount
    inv.status = "paid" if inv.paid >= _d(inv.total) else "part_paid"
    db.flush()
    return p


def invoice_dict(db, inv: PlatformInvoice, lines: bool = False) -> dict:
    tenant = db.get(Tenant, inv.tenant_id)
    out = {"id": inv.id, "number": inv.number, "tenant_id": inv.tenant_id,
           "tenant": tenant.name if tenant else None,
           "period": f"{inv.period_start:%Y-%m}", "period_label": f"{inv.period_start:%B %Y}",
           "issued_at": inv.issued_at.isoformat() + "Z" if inv.issued_at else None,
           "due_date": inv.due_date.isoformat() if inv.due_date else None,
           "status": inv.status, "total": float(inv.total), "paid": float(inv.paid),
           "balance": float(_d(inv.total) - _d(inv.paid)),
           "overdue": bool(inv.status in ("issued", "part_paid") and inv.due_date
                           and inv.due_date < date.today()),
           "outstanding_book": float(inv.outstanding_book) if inv.outstanding_book is not None else None}
    if lines:
        out["lines"] = [{"kind": ln.kind, "description": ln.description, "quantity": float(ln.quantity),
                         "unit_price": float(ln.unit_price), "amount": float(ln.amount)}
                        for ln in db.query(PlatformInvoiceLine)
                        .filter(PlatformInvoiceLine.invoice_id == inv.id).order_by(PlatformInvoiceLine.id)]
        out["payments"] = [{"amount": float(p.amount), "reference": p.reference, "channel": p.channel,
                            "paid_at": p.paid_at.isoformat() + "Z"}
                           for p in db.query(PlatformPayment).filter(PlatformPayment.invoice_id == inv.id)
                           .order_by(PlatformPayment.paid_at)]
    return out


def revenue(db, tenant_ids=None, months: int = 12) -> list[dict]:
    """Invoiced and received per month, by kind of charge."""
    from app.services.platform_metrics import _add_months, today_kenya
    first = _add_months(today_kenya().replace(day=1), -(months - 1))
    q = (db.query(PlatformInvoice.period_start, PlatformInvoiceLine.kind,
                  func.coalesce(func.sum(PlatformInvoiceLine.amount), 0))
         .join(PlatformInvoiceLine, PlatformInvoiceLine.invoice_id == PlatformInvoice.id)
         .filter(PlatformInvoice.status != "void", PlatformInvoice.period_start >= first))
    if tenant_ids is not None:
        q = q.filter(PlatformInvoice.tenant_id.in_(sorted(tenant_ids) or [-1]))
    by = {}
    for start, kind, amt in q.group_by(PlatformInvoice.period_start, PlatformInvoiceLine.kind).all():
        row = by.setdefault(f"{start:%Y-%m}", {"month": f"{start:%Y-%m}", "label": f"{start:%b %Y}",
                                               "total": 0.0})
        row[kind] = float(amt)
        row["total"] += float(amt)
    month_col = (func.to_char(PlatformPayment.paid_at, "YYYY-MM") if db.bind.dialect.name == "postgresql"
                 else func.strftime("%Y-%m", PlatformPayment.paid_at))
    paid_q = db.query(month_col, func.coalesce(func.sum(PlatformPayment.amount), 0)).filter(
        PlatformPayment.invoice_id.isnot(None))
    if tenant_ids is not None:
        paid_q = paid_q.filter(PlatformPayment.tenant_id.in_(sorted(tenant_ids) or [-1]))
    for month, amt in paid_q.group_by(month_col).all():
        if month in by:
            by[month]["received"] = float(amt)
    return [by[k] for k in sorted(by)]
