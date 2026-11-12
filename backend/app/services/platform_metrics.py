"""WP13 — the figures on the Finyl command centre, per DCP.

All amounts are in shillings. "Days late" for a DCP's loan is counted from
its due date (Kenyan date). Loan-book classes follow CBK's prudential
guidelines: normal (current), watch (1-30 days), substandard (31-90),
doubtful (91-180), loss (over 180).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.models import (Borrower, Branch, Complaint, CrbCheck, FaceValidationLog, Loan,
                        MpesaStatementAnalysis, PaymentTransaction, PlatformTenantDaily,
                        Repayment, SmsLog, Staff, Tenant)

CLASSES = [("normal", 0, 0), ("watch", 1, 30), ("substandard", 31, 90),
           ("doubtful", 91, 180), ("loss", 181, 10 ** 6)]
OPEN = ("active", "overdue", "defaulted")
NOT_DISBURSED = ("pending", "underwriting", "approved", "processing", "rejected")


def _f(x) -> float:
    return float(x or 0)


def today_kenya() -> date:
    return (datetime.utcnow() + timedelta(hours=3)).date()


def _utc_start(d: date) -> datetime:
    """Kenyan midnight of `d` as naive UTC."""
    return datetime(d.year, d.month, d.day) - timedelta(hours=3)


# --------------------------------------------------------------------------- #
# Loan book
# --------------------------------------------------------------------------- #
def loan_book(db, tenant_id: int, today: date | None = None) -> dict:
    today = today or today_kenya()
    classes = {name: {"count": 0, "amount": 0.0} for name, _, _ in CLASSES}
    outstanding = par30 = par90 = 0.0
    loans = 0
    for status, due, bal in (db.query(Loan.status, Loan.due_date, Loan.outstanding_balance)
                             .filter(Loan.tenant_id == tenant_id, Loan.status.in_(OPEN)).all()):
        bal = _f(bal)
        if bal <= 0:
            continue
        loans += 1
        outstanding += bal
        dpd = max(0, (today - due).days) if due else 0
        for name, lo, hi in CLASSES:
            if lo <= dpd <= hi:
                classes[name]["count"] += 1
                classes[name]["amount"] += bal
                break
        if dpd > 30:
            par30 += bal
        if dpd > 90:
            par90 += bal
    status_counts = dict(db.query(Loan.status, func.count(Loan.id))
                         .filter(Loan.tenant_id == tenant_id).group_by(Loan.status).all())
    return {"outstanding": round(outstanding, 2), "open_loans": loans,
            "par30": round(par30, 2), "par90": round(par90, 2),
            "par30_pct": round(100 * par30 / outstanding, 1) if outstanding else 0.0,
            "classes": classes,
            "status_counts": {k: int(v) for k, v in status_counts.items()},
            "pending": int(sum(status_counts.get(s, 0) for s in ("pending", "underwriting", "approved")))}


# --------------------------------------------------------------------------- #
# Money out and money in
# --------------------------------------------------------------------------- #
def flows(db, tenant_id: int, start: date, end: date) -> dict:
    """Disbursed (principal of loans paid out) and collected (repayments) between
    two Kenyan dates, both included."""
    disbursed, count = (db.query(func.coalesce(func.sum(Loan.principal), 0), func.count(Loan.id))
                        .filter(Loan.tenant_id == tenant_id,
                                Loan.disbursement_date >= start, Loan.disbursement_date <= end,
                                ~Loan.status.in_(NOT_DISBURSED)).one())
    collected, rcount = (db.query(func.coalesce(func.sum(Repayment.amount), 0), func.count(Repayment.id))
                         .filter(Repayment.tenant_id == tenant_id,
                                 Repayment.payment_date >= _utc_start(start),
                                 Repayment.payment_date < _utc_start(end + timedelta(days=1))).one())
    d, c = _f(disbursed), _f(collected)
    return {"disbursed": round(d, 2), "loans_disbursed": int(count),
            "collected": round(c, 2), "repayments": int(rcount),
            "collection_ratio": round(100 * c / d, 1) if d else None}


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def monthly(db, tenant_id: int, months: int = 12, today: date | None = None) -> list[dict]:
    today = today or today_kenya()
    first = _add_months(_month_start(today), -(months - 1))
    out = []
    for i in range(months):
        start = _add_months(first, i)
        end = min(_add_months(start, 1) - timedelta(days=1), today)
        f = flows(db, tenant_id, start, end)
        out.append({"month": start.strftime("%Y-%m"), "label": start.strftime("%b %Y"),
                    "disbursed": f["disbursed"], "collected": f["collected"]})
    return out


# --------------------------------------------------------------------------- #
# SMS and other paid services
# --------------------------------------------------------------------------- #
def sms_usage(db, tenant_id: int, since: datetime, until: datetime | None = None) -> dict:
    q = db.query(SmsLog).filter(SmsLog.tenant_id == tenant_id, SmsLog.sent_at >= since)
    if until is not None:
        q = q.filter(SmsLog.sent_at < until)
    sent = q.filter(SmsLog.billable == True)  # noqa: E712
    revenue, cost, count = (sent.with_entities(func.coalesce(func.sum(SmsLog.sell_price_kes), 0),
                                               func.coalesce(func.sum(SmsLog.cost_price_kes), 0),
                                               func.count(SmsLog.id)).one())
    failed = q.filter(SmsLog.status == "failed").count()
    by_type = dict(sent.with_entities(SmsLog.trigger_type, func.count(SmsLog.id))
                   .group_by(SmsLog.trigger_type).all())
    return {"sent": int(count), "failed": int(failed), "revenue": round(_f(revenue), 2),
            "cost": round(_f(cost), 2), "margin": round(_f(revenue) - _f(cost), 2),
            "by_type": {k or "other": int(v) for k, v in by_type.items()}}


def services_usage(db, tenant_id: int, since: datetime) -> dict:
    mpesa = {t: {"count": int(c), "amount": round(_f(a), 2)} for t, c, a in
             db.query(PaymentTransaction.type, func.count(PaymentTransaction.id),
                      func.coalesce(func.sum(PaymentTransaction.amount), 0))
             .filter(PaymentTransaction.tenant_id == tenant_id,
                     PaymentTransaction.created_at >= since)
             .group_by(PaymentTransaction.type).all()}
    crb = (db.query(func.count(CrbCheck.id))
           .filter(CrbCheck.tenant_id == tenant_id, CrbCheck.created_at >= since).scalar())
    face = (db.query(func.count(FaceValidationLog.id))
            .filter(FaceValidationLog.tenant_id == tenant_id,
                    FaceValidationLog.validated_at >= since).scalar())
    stmts = (db.query(func.count(MpesaStatementAnalysis.id))
             .filter(MpesaStatementAnalysis.tenant_id == tenant_id).scalar())
    return {"mpesa": mpesa, "crb_checks": int(crb or 0), "face_checks": int(face or 0),
            "statement_analyses_total": int(stmts or 0)}


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
def operations(db, tenant_id: int) -> dict:
    now = datetime.utcnow()
    staff = db.query(func.count(Staff.id)).filter(Staff.tenant_id == tenant_id,
                                                   Staff.active == True).scalar()  # noqa: E712
    clients = db.query(func.count(Borrower.id)).filter(Borrower.tenant_id == tenant_id).scalar()
    branches = db.query(func.count(Branch.id)).filter(Branch.tenant_id == tenant_id).scalar()
    open_c = (db.query(func.count(Complaint.id))
              .filter(Complaint.tenant_id == tenant_id, Complaint.status != "resolved").scalar())
    breached = (db.query(func.count(Complaint.id))
                .filter(Complaint.tenant_id == tenant_id, Complaint.status != "resolved",
                        Complaint.sla_deadline < now).scalar())
    last_loan = db.query(func.max(Loan.created_at)).filter(Loan.tenant_id == tenant_id).scalar()
    last_pay = db.query(func.max(Repayment.payment_date)).filter(Repayment.tenant_id == tenant_id).scalar()
    last = max([x for x in (last_loan, last_pay) if x] or [None]) if (last_loan or last_pay) else None
    return {"staff": int(staff or 0), "clients": int(clients or 0), "branches": int(branches or 0),
            "complaints_open": int(open_c or 0), "complaints_breached": int(breached or 0),
            "last_activity": last.isoformat() + "Z" if last else None,
            "days_since_activity": (now - last).days if last else None}


# --------------------------------------------------------------------------- #
# Health score
# --------------------------------------------------------------------------- #
def health(book: dict, last30: dict, ops: dict, alerts: dict | None = None) -> dict:
    """0-100 and green / amber / red, with the reasons. Starts at 100."""
    score, reasons = 100, []
    par = book.get("par30_pct") or 0
    if par > 30:
        score -= 55
        reasons.append(f"{par}% of the loan book is more than 30 days late")
    elif par > 15:
        score -= 35
        reasons.append(f"{par}% of the loan book is more than 30 days late")
    elif par > 5:
        score -= 15
        reasons.append(f"{par}% of the loan book is more than 30 days late")
    ratio = last30.get("collection_ratio")
    if ratio is not None and ratio < 50:
        score -= 20
        reasons.append(f"collected only {ratio}% of what was disbursed in 30 days")
    if ops.get("complaints_breached"):
        score -= 10
        reasons.append(f"{ops['complaints_breached']} complaint(s) past the 14-day limit")
    if ops.get("days_since_activity") is not None and ops["days_since_activity"] > 14:
        score -= 15
        reasons.append(f"no loans or payments for {ops['days_since_activity']} days")
    if alerts and alerts.get("critical"):
        score -= 15
        reasons.append(f"{alerts['critical']} critical alert(s) open")
    score = max(0, score)
    return {"score": score, "band": "green" if score >= 75 else "amber" if score >= 50 else "red",
            "reasons": reasons}


def summary(db, tenant: Tenant, days: int = 30, alerts: dict | None = None) -> dict:
    """The one-line view of a DCP on the command centre."""
    today = today_kenya()
    book = loan_book(db, tenant.id, today)
    window = flows(db, tenant.id, today - timedelta(days=days - 1), today)
    last30 = window if days == 30 else flows(db, tenant.id, today - timedelta(days=29), today)
    ops = operations(db, tenant.id)
    sms = sms_usage(db, tenant.id, _utc_start(today - timedelta(days=days - 1)))
    return {"id": tenant.id, "name": tenant.name, "code": tenant.code, "active": tenant.active,
            "color": tenant.logo_color, "book": book, "window": window, "sms": sms,
            "operations": ops, "health": health(book, last30, ops, alerts)}


# --------------------------------------------------------------------------- #
# Nightly snapshot (trend lines and month-end loan book for billing)
# --------------------------------------------------------------------------- #
def snapshot(db, day: date | None = None) -> dict:
    from app.services.platform_alerts import raise_alert, recent_counts
    day = day or today_kenya()
    counts = recent_counts(db)
    n = 0
    for t in db.query(Tenant).filter(Tenant.is_internal.is_(False)).all():
        book = loan_book(db, t.id, day)
        f = flows(db, t.id, day, day)
        ops = operations(db, t.id)
        sms = sms_usage(db, t.id, _utc_start(day), _utc_start(day + timedelta(days=1)))
        h = health(book, flows(db, t.id, day - timedelta(days=29), day), ops, counts.get(t.id))
        row = (db.query(PlatformTenantDaily)
               .filter(PlatformTenantDaily.tenant_id == t.id, PlatformTenantDaily.day == day).first())
        if row is None:
            row = PlatformTenantDaily(tenant_id=t.id, day=day)
            db.add(row)
        prev = (db.query(PlatformTenantDaily.health_score)
                .filter(PlatformTenantDaily.tenant_id == t.id, PlatformTenantDaily.day < day)
                .order_by(PlatformTenantDaily.day.desc()).first())
        row.outstanding = Decimal(str(book["outstanding"]))
        row.par30 = Decimal(str(book["par30"]))
        row.disbursed = Decimal(str(f["disbursed"]))
        row.collected = Decimal(str(f["collected"]))
        row.clients = ops["clients"]
        row.active_loans = book["open_loans"]
        row.sms_sent = sms["sent"]
        row.sms_revenue = Decimal(str(sms["revenue"]))
        row.health_score = h["score"]
        if t.active and h["band"] == "red" and (prev is None or (prev[0] or 100) >= 50):
            raise_alert("dcp_health", f"{t.name}'s health score dropped to {h['score']}",
                        severity="warning", tenant_id=t.id, key=str(day),
                        detail="; ".join(h["reasons"]))
        n += 1
    db.commit()
    return {"tenants": n, "day": day.isoformat()}


def trend(db, tenant_id: int, days: int = 90) -> list[dict]:
    since = today_kenya() - timedelta(days=days)
    rows = (db.query(PlatformTenantDaily)
            .filter(PlatformTenantDaily.tenant_id == tenant_id, PlatformTenantDaily.day >= since)
            .order_by(PlatformTenantDaily.day).all())
    return [{"day": r.day.isoformat(), "outstanding": _f(r.outstanding), "par30": _f(r.par30),
             "health": r.health_score, "clients": r.clients} for r in rows]


def book_on(db, tenant_id: int, day: date) -> float:
    """The loan book on a day: the nightly snapshot, else today's figure."""
    row = (db.query(PlatformTenantDaily.outstanding)
           .filter(PlatformTenantDaily.tenant_id == tenant_id, PlatformTenantDaily.day <= day)
           .order_by(PlatformTenantDaily.day.desc()).first())
    if row is not None:
        return _f(row[0])
    return loan_book(db, tenant_id)["outstanding"]
