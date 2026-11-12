"""WP13 — Finyl's loans to DCPs.

A facility is a credit line Finyl gives a DCP: a limit, a monthly interest
rate (reducing balance by default, or flat), and a term in months. Each
drawdown is repaid in equal monthly instalments over the term, starting a
month after it is paid out. Repayments go to Finyl's own M-Pesa paybill with
the facility number (e.g. FAC-0001) as the account, or are recorded by hand,
and are applied to the oldest instalment first, interest before principal.

Days late and the class (normal / watch / substandard / doubtful / loss) use
the same CBK bands as a DCP's own loan book.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func

from app.models import (PlatformDrawdown, PlatformFacility, PlatformInvoice, PlatformPayment,
                        PlatformUnmatchedPayment, Tenant)

CENT = Decimal("0.01")


def _d(x) -> Decimal:
    return Decimal(str(x or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


class LendingError(ValueError):
    """Shown to the user."""


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def schedule(fac: PlatformFacility, drawdowns: list[PlatformDrawdown]) -> list[dict]:
    """Every instalment of every drawdown, in due-date order."""
    r = Decimal(str(fac.interest_rate_pct)) / 100
    n = max(1, int(fac.term_months))
    out = []
    for dd in drawdowns:
        p = _d(dd.amount)
        if fac.interest_method == "flat" or r == 0:
            princ = [_d(p / n)] * n
            princ[-1] = p - sum(princ[:-1])
            ints = [_d(p * r)] * n if r else [Decimal("0")] * n
        else:
            pay = p * r / (1 - (1 + r) ** -n)
            bal, princ, ints = p, [], []
            for i in range(n):
                interest = _d(bal * r)
                pr = _d(pay - interest) if i < n - 1 else bal
                princ.append(pr)
                ints.append(interest)
                bal -= pr
        for i in range(n):
            out.append({"drawdown_id": dd.id, "due": _add_months(dd.disbursed_on, i + 1),
                        "principal": princ[i], "interest": ints[i]})
    out.sort(key=lambda x: (x["due"], x["drawdown_id"]))
    return out


def position(db, fac: PlatformFacility, today: date | None = None) -> dict:
    from app.services.platform_metrics import CLASSES, today_kenya
    today = today or today_kenya()
    dds = (db.query(PlatformDrawdown).filter(PlatformDrawdown.facility_id == fac.id)
           .order_by(PlatformDrawdown.disbursed_on, PlatformDrawdown.id).all())
    sched = schedule(fac, dds)
    paid_total = _d(db.query(func.coalesce(func.sum(PlatformPayment.amount), 0))
                    .filter(PlatformPayment.facility_id == fac.id).scalar())
    left = paid_total
    principal_paid = interest_paid = Decimal("0")
    oldest_unpaid = None
    rows = []
    for inst in sched:
        due_i, due_p = inst["interest"], inst["principal"]
        pi = min(left, due_i)
        left -= pi
        pp = min(left, due_p)
        left -= pp
        interest_paid += pi
        principal_paid += pp
        unpaid = (due_i - pi) + (due_p - pp)
        if unpaid > 0 and oldest_unpaid is None:
            oldest_unpaid = inst["due"]
        rows.append({"due": inst["due"].isoformat(), "principal": float(due_p),
                     "interest": float(due_i), "paid": float(pi + pp), "unpaid": float(unpaid)})
    drawn = _d(sum(_d(d.amount) for d in dds))
    due_to_date = sum((i["principal"] + i["interest"]) for i in sched if i["due"] <= today)
    arrears = max(Decimal("0"), _d(due_to_date) - paid_total)
    days_late = (today - oldest_unpaid).days if (oldest_unpaid and oldest_unpaid < today and arrears > 0) else 0
    klass = next(name for name, lo, hi in CLASSES if lo <= days_late <= hi)
    principal_out = drawn - principal_paid
    nxt = next((r for r in rows if r["unpaid"] > 0 and r["due"] >= today.isoformat()), None)
    return {"drawn": float(drawn), "available": float(max(Decimal("0"), _d(fac.limit_amount) - principal_out)),
            "principal_outstanding": float(principal_out),
            "total_outstanding": float(_d(sum(i["principal"] + i["interest"] for i in sched)) - paid_total),
            "paid": float(paid_total), "principal_paid": float(principal_paid),
            "interest_paid": float(interest_paid), "arrears": float(arrears),
            "days_late": days_late, "class": klass,
            "next_due": nxt, "schedule": rows}


def _next_number(db) -> str:
    n = db.query(func.count(PlatformFacility.id)).scalar() or 0
    return f"FAC-{int(n) + 1:04d}"


def create(db, tenant_id: int, body: dict, user_id: int) -> PlatformFacility:
    if db.get(Tenant, tenant_id) is None:
        raise LendingError("DCP not found")
    try:
        limit = _d(body.get("limit_amount"))
        rate = Decimal(str(body.get("interest_rate_pct")))
        term = int(body.get("term_months") or 12)
        start = date.fromisoformat(str(body.get("start_date") or date.today().isoformat()))
    except Exception:
        raise LendingError("Check the limit, interest rate, term and start date.")
    method = str(body.get("interest_method") or "reducing")
    if limit <= 0 or rate < 0 or rate > 20 or not (1 <= term <= 120) or method not in ("reducing", "flat"):
        raise LendingError("The limit must be above zero, the monthly rate 0-20%, the term 1-120 months, "
                           "and the method reducing or flat.")
    fac = PlatformFacility(number=_next_number(db), tenant_id=tenant_id, limit_amount=limit,
                           interest_rate_pct=rate, interest_method=method, term_months=term,
                           start_date=start, status="active",
                           purpose=(body.get("purpose") or "")[:200] or None,
                           notes=body.get("notes"), created_by_user_id=user_id)
    db.add(fac)
    db.flush()
    return fac


def drawdown(db, fac: PlatformFacility, amount, on: date, reference: str | None,
             note: str | None, user_id: int) -> PlatformDrawdown:
    amount = _d(amount)
    if fac.status != "active":
        raise LendingError(f"The facility is {fac.status}.")
    if amount <= 0:
        raise LendingError("The amount must be above zero.")
    pos = position(db, fac)
    if amount > _d(pos["available"]):
        raise LendingError(f"Only KES {pos['available']:,.2f} of the limit is available.")
    dd = PlatformDrawdown(facility_id=fac.id, amount=amount, disbursed_on=on,
                          reference=(reference or "")[:40] or None, note=note, created_by_user_id=user_id)
    db.add(dd)
    db.flush()
    return dd


def repay(db, fac: PlatformFacility, amount, reference: str, paid_at: datetime,
          channel: str = "manual", note: str | None = None, user_id: int | None = None) -> PlatformPayment:
    amount = _d(amount)
    reference = (reference or "").strip().upper()[:40]
    if amount <= 0 or not reference:
        raise LendingError("Enter an amount above zero and the payment reference.")
    if db.query(PlatformPayment).filter(PlatformPayment.reference == reference).first():
        raise LendingError("This reference is already recorded.")
    before = position(db, fac)
    p = PlatformPayment(tenant_id=fac.tenant_id, facility_id=fac.id, amount=amount, reference=reference,
                        channel=channel, paid_at=paid_at, note=note, recorded_by_user_id=user_id)
    db.add(p)
    db.flush()
    after = position(db, fac)
    p.principal = _d(after["principal_paid"] - before["principal_paid"])
    p.interest = _d(after["interest_paid"] - before["interest_paid"])
    if after["total_outstanding"] <= 0 and fac.status == "active" and after["drawn"] > 0:
        fac.status = "closed"
    db.flush()
    return p


def facility_dict(db, fac: PlatformFacility, detail: bool = False) -> dict:
    pos = position(db, fac)
    t = db.get(Tenant, fac.tenant_id)
    out = {"id": fac.id, "number": fac.number, "tenant_id": fac.tenant_id, "tenant": t.name if t else None,
           "limit_amount": float(fac.limit_amount), "interest_rate_pct": float(fac.interest_rate_pct),
           "interest_method": fac.interest_method, "term_months": fac.term_months,
           "start_date": fac.start_date.isoformat(), "status": fac.status, "purpose": fac.purpose,
           **{k: v for k, v in pos.items() if k != "schedule"}}
    if detail:
        out["schedule"] = pos["schedule"]
        out["drawdowns"] = [{"id": d.id, "amount": float(d.amount), "disbursed_on": d.disbursed_on.isoformat(),
                             "reference": d.reference, "note": d.note}
                            for d in db.query(PlatformDrawdown).filter(PlatformDrawdown.facility_id == fac.id)
                            .order_by(PlatformDrawdown.disbursed_on)]
        out["payments"] = [{"amount": float(p.amount), "reference": p.reference, "channel": p.channel,
                            "paid_at": p.paid_at.isoformat() + "Z", "principal": float(p.principal or 0),
                            "interest": float(p.interest or 0)}
                           for p in db.query(PlatformPayment).filter(PlatformPayment.facility_id == fac.id)
                           .order_by(PlatformPayment.paid_at)]
        out["notes"] = fac.notes
    return out


def overdue_alerts(db) -> dict:
    """Daily: tell Finyl about facilities in arrears and overdue invoices."""
    from app.services.platform_alerts import raise_alert
    n = 0
    for fac in db.query(PlatformFacility).filter(PlatformFacility.status == "active").all():
        pos = position(db, fac)
        if pos["arrears"] > 0 and pos["days_late"] >= 1:
            t = db.get(Tenant, fac.tenant_id)
            raise_alert("facility_overdue", f"{t.name if t else 'A DCP'} is {pos['days_late']} days late on "
                        f"{fac.number}", severity="critical" if pos["days_late"] > 30 else "warning",
                        tenant_id=fac.tenant_id, key=fac.number,
                        detail=f"Arrears KES {pos['arrears']:,.2f}; class {pos['class']}.")
            n += 1
    for inv in db.query(PlatformInvoice).filter(PlatformInvoice.status.in_(("issued", "part_paid")),
                                                PlatformInvoice.due_date < date.today()).all():
        t = db.get(Tenant, inv.tenant_id)
        raise_alert("invoice_overdue", f"{t.name if t else 'A DCP'}: invoice {inv.number} is overdue",
                    severity="warning", tenant_id=inv.tenant_id, key=inv.number,
                    detail=f"Balance KES {float(inv.total) - float(inv.paid):,.2f}, due {inv.due_date:%d %b %Y}.")
        n += 1
    return {"alerts": n}


def apply_finyl_paybill(db, body: dict) -> dict:
    """A payment into Finyl's own paybill (C2B confirmation). The account the
    payer typed picks the facility (FAC-0001), the invoice (INV-2026-10-0001)
    or, with a DCP code, that DCP's oldest unpaid invoice. Anything else waits
    in Finyl's unmatched payments list. Never counts a receipt twice."""
    from app.services import platform_billing as bill
    ref = str(body.get("TransID") or "").strip().upper()[:40]
    amount = _d(body.get("TransAmount"))
    account = " ".join(str(body.get("BillRefNumber") or "").split()).upper()
    payer = " ".join(str(body.get(k) or "") for k in ("FirstName", "MiddleName", "LastName")).strip()
    when = datetime.utcnow()
    tt = str(body.get("TransTime") or "")
    if len(tt) == 14 and tt.isdigit():
        when = datetime.strptime(tt, "%Y%m%d%H%M%S") - timedelta(hours=3)
    if not ref or amount <= 0:
        return {"result": "ignored"}
    if (db.query(PlatformPayment).filter(PlatformPayment.reference == ref).first()
            or db.query(PlatformUnmatchedPayment).filter(PlatformUnmatchedPayment.reference == ref).first()):
        return {"result": "duplicate"}
    fac = db.query(PlatformFacility).filter(func.upper(PlatformFacility.number) == account).first()
    if fac is not None:
        repay(db, fac, amount, ref, when, channel="mpesa_paybill", note=f"Paid by {payer}".strip())
        return {"result": "facility", "facility": fac.number}
    inv = db.query(PlatformInvoice).filter(func.upper(PlatformInvoice.number) == account).first()
    if inv is None and account:
        tenant = db.query(Tenant).filter(func.upper(Tenant.code) == account).first()
        if tenant is not None:
            inv = (db.query(PlatformInvoice)
                   .filter(PlatformInvoice.tenant_id == tenant.id,
                           PlatformInvoice.status.in_(("issued", "part_paid")))
                   .order_by(PlatformInvoice.period_start).first())
    if inv is not None and inv.status != "void":
        bill.record_payment(db, inv, amount, ref, when, channel="mpesa_paybill", note=f"Paid by {payer}".strip())
        return {"result": "invoice", "invoice": inv.number}
    db.add(PlatformUnmatchedPayment(reference=ref, amount=amount, account_entered=account[:60] or None,
                                    payer=payer[:120] or None, paid_at=when))
    db.flush()
    from app.services.platform_alerts import raise_alert
    raise_alert("payment_unroutable", "A payment to Finyl's paybill names no invoice or facility",
                severity="warning", key="finyl-paybill",
                detail=f"{ref}: KES {amount:,.2f} from {payer or 'unknown'}, account typed '{account}'. "
                       f"Allocate it under Finyl → Billing → Unmatched payments.")
    return {"result": "unmatched"}
