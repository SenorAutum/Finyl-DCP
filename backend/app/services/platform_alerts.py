"""WP13 — alerts when something fails, and who is told.

Anywhere in Finyl a failure can call ``raise_alert``. The same problem
repeating (same kind, DCP and key) does not create a new alert; it raises the
count and the "last seen" time of the open one. A job sends new alerts (and
open ones that keep repeating, at most every 6 hours) by email, and SMS where
a number is given, to the recipients whose filters match: lowest severity,
kinds of alert, and DCPs.

Raising an alert never breaks the work that failed: it uses its own database
session and swallows its own errors.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import text

from app.core.database import SessionLocal
from app.models import PlatformAlert, PlatformAlertRecipient, Tenant

logger = logging.getLogger("finyl.alerts")
SEVERITIES = {"info": 0, "warning": 1, "critical": 2}
RENOTIFY_AFTER = timedelta(hours=6)

KINDS = {
    "server_error": "Unexpected error on the server",
    "job_failed": "Background job failed",
    "webhook_failed": "Payment notification could not be processed",
    "webhook_dead": "Payment notification gave up after retries",
    "payment_unroutable": "Payment to a number no DCP owns",
    "sms_failed": "SMS could not be sent",
    "bank_notice_refused": "Bank notification refused",
    "mpesa_pull_failed": "Missed-payment check failed",
    "mpesa_pull_recovered": "Missed M-Pesa payments recovered",
    "payout_failed": "Loan payout failed",
    "facility_overdue": "A DCP is late repaying Finyl",
    "invoice_overdue": "A DCP's invoice is overdue",
    "dcp_health": "A DCP's health score turned red",
}


def raise_alert(kind: str, title: str, detail: str = "", severity: str = "warning",
                tenant_id: int | None = None, key: str | None = None) -> None:
    """Record (or repeat) an alert. Never raises."""
    db = None
    try:
        severity = severity if severity in SEVERITIES else "warning"
        dedupe = f"{kind}:{tenant_id or '-'}:{key or ''}"[:200]
        db = SessionLocal()
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL lock_timeout = '3s'"))   # never hold up the caller for long
        now = datetime.utcnow()
        row = (db.query(PlatformAlert)
               .filter(PlatformAlert.dedupe_key == dedupe, PlatformAlert.status != "resolved")
               .order_by(PlatformAlert.id.desc()).first())
        if row is None:
            db.add(PlatformAlert(tenant_id=tenant_id, kind=kind, severity=severity,
                                 title=title[:200], detail=(detail or "")[:4000],
                                 dedupe_key=dedupe, first_seen=now, last_seen=now))
        else:
            row.count = (row.count or 1) + 1
            row.last_seen = now
            row.detail = (detail or row.detail or "")[:4000]
            if SEVERITIES[severity] > SEVERITIES.get(row.severity, 1):
                row.severity = severity
        db.commit()
    except Exception:                                    # alerts must never break the caller
        logger.exception("could not record alert kind=%s", kind)
        try:
            if db is not None:
                db.rollback()
        except Exception:
            pass
    finally:
        if db is not None:
            db.close()


def _matches(rec: PlatformAlertRecipient, alert: PlatformAlert) -> bool:
    if not rec.active:
        return False
    if SEVERITIES.get(alert.severity, 1) < SEVERITIES.get(rec.min_severity or "warning", 1):
        return False
    if rec.kinds and alert.kind not in rec.kinds:
        return False
    if rec.tenant_ids and alert.tenant_id not in {int(t) for t in rec.tenant_ids}:
        return False
    return True


def _message(db, alert: PlatformAlert) -> tuple[str, str]:
    from app.core.config import settings
    tenant = db.get(Tenant, alert.tenant_id) if alert.tenant_id else None
    where = tenant.name if tenant else "Finyl platform"
    subject = f"[Finyl {alert.severity.upper()}] {where}: {alert.title}"[:180]
    base = (settings.DARAJA_CALLBACK_BASE_URL or "").rstrip("/")
    body = "\n".join([
        f"{KINDS.get(alert.kind, alert.kind)} — {alert.severity}",
        f"DCP: {where}",
        f"What happened: {alert.title}",
        f"Times seen: {alert.count} (first {alert.first_seen:%d %b %Y %H:%M} UTC, "
        f"last {alert.last_seen:%d %b %Y %H:%M} UTC)",
        "",
        alert.detail or "",
        "",
        f"Open the alert list: {base}/platform/alerts" if base else "Open Finyl → Alerts.",
        "You receive this because you are on Finyl's alert list.",
    ])
    return subject, body


def deliver_pending(db) -> dict:
    """Job: tell the right people about new (or still repeating) alerts."""
    from app.services import mailer
    now = datetime.utcnow()
    recipients = db.query(PlatformAlertRecipient).filter(PlatformAlertRecipient.active == True).all()  # noqa: E712
    sent = 0
    for alert in (db.query(PlatformAlert).filter(PlatformAlert.status == "open")
                  .order_by(PlatformAlert.id).limit(200).all()):
        due = alert.notified_at is None or (alert.last_seen > alert.notified_at
                                            and now - alert.notified_at >= RENOTIFY_AFTER)
        if not due:
            continue
        subject, body = _message(db, alert)
        for rec in recipients:
            if not _matches(rec, alert):
                continue
            try:
                if rec.email and mailer.send_email(rec.email, subject, body):
                    sent += 1
                if rec.phone and alert.severity == "critical":
                    from app.services import sms
                    sms.send_platform_sms(db, rec.phone, subject[:300])
            except Exception:
                logger.exception("could not deliver alert %s to %s", alert.id, rec.id)
        alert.notified_at = now
    db.commit()
    return {"sent": sent}


def recent_counts(db, tenant_ids=None) -> dict:
    q = db.query(PlatformAlert.tenant_id, PlatformAlert.severity).filter(PlatformAlert.status == "open")
    out = {}
    for tid, sev in q.all():
        if tenant_ids is not None and tid not in tenant_ids:
            continue
        d = out.setdefault(tid, {"critical": 0, "warning": 0, "info": 0})
        d[sev] = d.get(sev, 0) + 1
    return out


def weekly_summary(db) -> dict:
    """Monday morning: one email with last week's figures for every DCP the
    recipient follows (all DCPs when they follow all)."""
    from datetime import timedelta as _td
    from app.core.config import settings
    from app.services import mailer, platform_metrics as pm
    today = pm.today_kenya()
    start, end = today - _td(days=7), today - _td(days=1)
    tenants = (db.query(Tenant).filter(Tenant.active == True, Tenant.is_internal.is_(False))  # noqa: E712
               .order_by(Tenant.name).all())
    rows = {}
    for t in tenants:
        book = pm.loan_book(db, t.id, end)
        f = pm.flows(db, t.id, start, end)
        sms = pm.sms_usage(db, t.id, pm._utc_start(start), pm._utc_start(today))
        rows[t.id] = (t.name, book, f, sms)
    open_alerts = recent_counts(db)
    base = (settings.DARAJA_CALLBACK_BASE_URL or "").rstrip("/")
    sent = 0
    for rec in (db.query(PlatformAlertRecipient)
                .filter(PlatformAlertRecipient.active == True,              # noqa: E712
                        PlatformAlertRecipient.weekly_summary == True).all()):  # noqa: E712
        if not rec.email:
            continue
        ids = {int(x) for x in (rec.tenant_ids or [])} or set(rows)
        mine = [rows[i] for i in rows if i in ids]
        if not mine:
            continue
        lines = [f"Finyl weekly summary, {start:%d %b} to {end:%d %b %Y}", ""]
        tot = {"out": 0.0, "dis": 0.0, "col": 0.0, "sms": 0, "rev": 0.0}
        for name, book, f, sms in mine:
            tot["out"] += book["outstanding"]; tot["dis"] += f["disbursed"]; tot["col"] += f["collected"]
            tot["sms"] += sms["sent"]; tot["rev"] += sms["revenue"]
            lines.append(f"{name}: book KES {book['outstanding']:,.0f} (PAR30 {book['par30_pct']}%), "
                         f"disbursed KES {f['disbursed']:,.0f}, collected KES {f['collected']:,.0f}, "
                         f"{sms['sent']} SMS (KES {sms['revenue']:,.2f})")
        crit = sum(c.get("critical", 0) for tid, c in open_alerts.items() if tid in ids or tid is None)
        lines += ["", f"All: book KES {tot['out']:,.0f}, disbursed KES {tot['dis']:,.0f}, "
                      f"collected KES {tot['col']:,.0f}, {tot['sms']} SMS (KES {tot['rev']:,.2f}).",
                  f"Open critical alerts: {crit}.", "",
                  f"Command centre: {base}/platform" if base else "Open Finyl → Command centre.",
                  "You receive this because you are on Finyl's alert list with the weekly summary on."]
        try:
            if mailer.send_email(rec.email, f"[Finyl] Weekly summary {end:%d %b %Y}", "\n".join(lines)):
                sent += 1
        except Exception:
            logger.exception("weekly summary to %s failed", rec.id)
    return {"sent": sent}
