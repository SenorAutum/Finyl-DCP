"""In-process auto-reconcile worker (APScheduler, no Redis/Celery).

A single recurring job runs inside the FastAPI process and, for every tenant that
has the `payments` module enabled, resolves stuck B2C payouts (transactions still
in `processing`/`timed_out` whose async Daraja Result never arrived). This mirrors
the manual POST /api/v1/payments/reconcile-disbursements endpoint:

  * With real Daraja credentials configured for the tenant, the sweep is a
    read-only report (operators resolve via the Daraja Transaction Status API).
  * In the credential-gated MOCK (no creds), it drives each pending payout to a
    successful result via the same callback code path (mpesa.simulate_b2c_result
    -> apply_b2c_result), so the async state machine completes without live Daraja.

The scheduler starts/stops with the app lifespan. Each run opens its own DB
session, commits, and closes it; per-tenant errors are caught and logged so one
tenant's failure never aborts the sweep or crashes the scheduler thread.
"""
import logging
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.database import SessionLocal

logger = logging.getLogger("finyl.scheduler")

_scheduler = None  # module-level singleton so we only ever start one


def _sweep_tenant(db, tenant_id: int, stuck_minutes: int) -> dict:
    """Resolve stuck B2C payouts for one tenant. Returns a counts dict.
    Reuses the exact service functions the endpoint uses."""
    from app.models import PaymentTransaction
    from app.services import mpesa
    from app.services.disbursement import apply_b2c_result

    cutoff = datetime.utcnow() - timedelta(minutes=max(0, stuck_minutes))
    stuck = (db.query(PaymentTransaction)
             .filter(PaymentTransaction.tenant_id == tenant_id,
                     PaymentTransaction.type == "b2c",
                     PaymentTransaction.status.in_(("processing", "timed_out")),
                     PaymentTransaction.created_at <= cutoff)
             .order_by(PaymentTransaction.created_at.asc())
             .all())
    if not stuck:
        return {"stuck": 0, "resolved": 0, "mode": "none"}

    configured = mpesa.is_configured(mpesa.resolve_creds(db, tenant_id))
    if configured or not mpesa.simulation_allowed():
        # Real creds, or a live (production) platform: read-only report — leave
        # resolution to Daraja status polling / a person. WP8: never pretend a
        # payout succeeded on a production platform, even if a DCP has no keys.
        return {"stuck": len(stuck), "resolved": 0, "mode": "report"}

    resolved = 0
    for txn in stuck:
        conv = txn.mpesa_ref
        orig = (txn.raw_payload or {}).get("result", {}).get("OriginatorConversationID")
        result = mpesa.simulate_b2c_result(conv, orig, success=True,
                                           amount=float(txn.amount))
        r = result["Result"]
        receipt = r.get("TransactionID")
        apply_b2c_result(db, tenant_id, txn, r.get("ResultCode"), receipt, raw=result)
        resolved += 1
    db.commit()
    return {"stuck": len(stuck), "resolved": resolved, "mode": "simulate"}


def run_auto_reconcile():
    """One scheduler tick: sweep every payments-enabled tenant. Never raises."""
    from app.models import TenantModule

    db = SessionLocal()
    try:
        tenant_ids = [row.tenant_id for row in
                      db.query(TenantModule)
                      .filter(TenantModule.module_key == "payments",
                              TenantModule.enabled == True)  # noqa: E712
                      .all()]
        total_stuck = total_resolved = 0
        for tid in tenant_ids:
            try:
                counts = _sweep_tenant(db, tid, settings.SCHEDULER_STUCK_MINUTES)
                total_stuck += counts["stuck"]
                total_resolved += counts["resolved"]
            except Exception:
                db.rollback()
                logger.exception("auto_reconcile: tenant %s sweep failed", tid)
        if total_stuck:
            logger.info("auto_reconcile: tenants=%d stuck=%d resolved=%d",
                        len(tenant_ids), total_stuck, total_resolved)
    except Exception:
        logger.exception("auto_reconcile: sweep aborted")
    finally:
        db.close()


def run_webhook_retry():
    """One retry tick: reprocess durable Daraja webhook events whose retry is due.

    Picks `failed` mpesa_webhook_events with next_retry_at <= now, reprocesses each
    idempotently via the SAME processors the live callbacks use (so a retry can
    never double-credit), and escalates to `dead` + alert after WEBHOOK_MAX_ATTEMPTS
    (handled inside reprocess_event -> webhook_security.mark_failed). Never raises."""
    from app.models import MpesaWebhookEvent
    from app.routers.payments import reprocess_event

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        due = (db.query(MpesaWebhookEvent)
               .filter(MpesaWebhookEvent.processing_status == "failed",
                       MpesaWebhookEvent.next_retry_at != None,   # noqa: E711
                       MpesaWebhookEvent.next_retry_at <= now)
               .order_by(MpesaWebhookEvent.next_retry_at.asc())
               .limit(100).all())
        recovered = dead = 0
        for event in due:
            try:
                if reprocess_event(db, event):
                    recovered += 1
                elif event.processing_status == "dead":
                    dead += 1
            except Exception:
                db.rollback()
                logger.exception("webhook_retry: event %s reprocess failed", event.id)
        if due:
            logger.info("webhook_retry: due=%d recovered=%d dead=%d", len(due), recovered, dead)
    except Exception:
        logger.exception("webhook_retry: sweep aborted")
    finally:
        db.close()


def run_webhook_purge():
    """One purge tick: enforce short retention of raw webhook payloads (ODPC).

    NULLs raw_payload of successfully-`processed` events older than
    WEBHOOK_RAW_RETENTION_HOURS, keeping the non-PII event metadata for audit.
    Failed/dead events retain their body until resolved. Never raises."""
    from app.models import MpesaWebhookEvent

    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=max(1, settings.WEBHOOK_RAW_RETENTION_HOURS))
        stale = (db.query(MpesaWebhookEvent)
                 .filter(MpesaWebhookEvent.processing_status == "processed",
                         MpesaWebhookEvent.raw_payload != None,      # noqa: E711
                         MpesaWebhookEvent.received_at <= cutoff)
                 .limit(1000).all())
        purged = 0
        for event in stale:
            event.raw_payload = None
            purged += 1
        if purged:
            db.commit()
            logger.info("webhook_purge: anonymised raw_payload of %d processed events "
                        "older than %dh", purged, settings.WEBHOOK_RAW_RETENTION_HOURS)
    except Exception:
        db.rollback()
        logger.exception("webhook_purge: aborted")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Phase 2 scheduled jobs: PTP reminders, collection efficiency, GPS stipend,
# OTP expiry cleanup. Each opens its own session, catches per-tenant/per-row
# errors, and never raises out of the scheduler thread.
# ---------------------------------------------------------------------------

def run_ptp_reminders():
    """Daily (08:00): send D-1 reminders and D+1 follow-ups for promises-to-pay.

    For every still-``pending`` PTP due tomorrow we send a courtesy reminder; for
    one that fell due yesterday and is still unpaid we send a follow-up. Each leg
    is recorded in ``PromiseToPay.reminder_sent_at`` (JSONB) so it is sent at most
    once. Never raises."""
    from datetime import date, timedelta
    from app.models import PromiseToPay, Borrower
    from app.services import sms

    db = SessionLocal()
    try:
        today = date.today()
        d_minus_1 = today + timedelta(days=1)   # due tomorrow  -> reminder
        d_plus_1 = today - timedelta(days=1)     # due yesterday -> follow-up
        rows = (db.query(PromiseToPay)
                .filter(PromiseToPay.status == "pending",
                        PromiseToPay.ptp_date.in_((d_minus_1, d_plus_1)))
                .all())
        sent = 0
        for ptp in rows:
            try:
                leg = "d_minus_1" if ptp.ptp_date == d_minus_1 else "d_plus_1"
                log = dict(ptp.reminder_sent_at or {})
                if leg in log:
                    continue
                borrower = db.query(Borrower).filter(
                    Borrower.id == ptp.client_id,
                    Borrower.tenant_id == ptp.tenant_id).first()
                phone = getattr(borrower, "phone", None)
                if not phone:
                    continue
                if leg == "d_minus_1":
                    msg = (f"Reminder: your payment promise of KES {ptp.amount} is due "
                           f"tomorrow ({ptp.ptp_date}). Kindly pay on time. Thank you.")
                else:
                    msg = (f"Follow-up: your payment promise of KES {ptp.amount} was due "
                           f"on {ptp.ptp_date}. Please settle it as soon as possible.")
                sms.send_sms(db, ptp.tenant_id, phone, msg, trigger_type="ptp_reminder")
                log[leg] = datetime.utcnow().isoformat() + "Z"
                ptp.reminder_sent_at = log
                db.commit()
                sent += 1
            except Exception:
                db.rollback()
                logger.exception("ptp_reminders: PTP %s failed", ptp.id)
        if sent:
            logger.info("ptp_reminders: sent=%d of due=%d", sent, len(rows))
    except Exception:
        logger.exception("ptp_reminders: aborted")
    finally:
        db.close()


def run_collection_efficiency():
    """Monthly: recompute and upsert every officer's collection-efficiency row for
    the current period, per tenant. Never raises."""
    from datetime import date
    from app.models import TenantModule
    from app.services import collection_analytics

    db = SessionLocal()
    try:
        period = date.today().replace(day=1)
        tenant_ids = [row.tenant_id for row in
                      db.query(TenantModule)
                      .filter(TenantModule.module_key == "lending",
                              TenantModule.enabled == True)  # noqa: E712
                      .all()]
        total = 0
        for tid in tenant_ids:
            try:
                out = collection_analytics.compute_all_officers(
                    db, tenant_id=tid, period_month=period)
                total += len(out)
            except Exception:
                db.rollback()
                logger.exception("collection_efficiency: tenant %s failed", tid)
        if total:
            logger.info("collection_efficiency: officers computed=%d across %d tenants",
                        total, len(tenant_ids))
    except Exception:
        logger.exception("collection_efficiency: aborted")
    finally:
        db.close()


def run_gps_stipend():
    """Daily (22:00): compute today's distance + transport stipend for every
    officer who logged GPS pings, per tenant. Never raises."""
    from datetime import date
    from app.models import StaffGpsLog
    from app.services import gps_tracker

    db = SessionLocal()
    try:
        today = date.today()
        # Distinct (tenant_id, user_id) pairs that logged a ping today.
        pairs = (db.query(StaffGpsLog.tenant_id, StaffGpsLog.user_id)
                 .filter(StaffGpsLog.timestamp >= datetime(today.year, today.month, today.day))
                 .distinct().all())
        computed = 0
        for tenant_id, user_id in pairs:
            try:
                gps_tracker.compute_stipend(db, tenant_id=tenant_id,
                                            user_id=user_id, day=today)
                computed += 1
            except Exception:
                db.rollback()
                logger.exception("gps_stipend: tenant %s user %s failed", tenant_id, user_id)
        if computed:
            logger.info("gps_stipend: computed %d officer-days", computed)
    except Exception:
        logger.exception("gps_stipend: aborted")
    finally:
        db.close()


def run_otp_cleanup():
    """Periodic: mark expired unconsumed OTP tokens consumed so they cannot be
    reused. Never raises."""
    from app.services import otp

    db = SessionLocal()
    try:
        n = otp.cleanup_expired(db)
        if n:
            logger.info("otp_cleanup: expired %d stale OTP tokens", n)
    except Exception:
        db.rollback()
        logger.exception("otp_cleanup: aborted")
    finally:
        db.close()


def run_cbk_monthly_gdi_submit(today=None):
    """CBK GDI monthly submission. Fires daily but only acts within the regulatory
    filing window: the 5th–10th of the month on a weekday (Mon–Fri). For each
    tenant with `cbk_reporting` enabled that has not yet submitted last month's
    datasets, it runs the full 5-dataset submission. Never raises."""
    from datetime import date
    from app.models import TenantModule, CbkSubmissionLog
    from app.services import cbk_gdi

    today = today or date.today()
    # Only act during the 5th–10th on a weekday.
    if not (5 <= today.day <= 10 and today.weekday() < 5):
        return

    # Previous calendar month (first day).
    if today.month == 1:
        last_month = date(today.year - 1, 12, 1)
    else:
        last_month = date(today.year, today.month - 1, 1)

    db = SessionLocal()
    try:
        tenant_ids = [row.tenant_id for row in
                      db.query(TenantModule)
                      .filter(TenantModule.module_key == "cbk_reporting",
                              TenantModule.enabled == True)  # noqa: E712
                      .all()]
        ran = 0
        for tid in tenant_ids:
            try:
                # WP8: a month counts as filed only when every dataset has a
                # log that is not an error. Datasets that failed are retried on
                # the next day of the filing window; ones already sent are not
                # sent again.
                done = cbk_gdi.datasets_filed(db, tid, last_month)
                if len(done) >= len(cbk_gdi.DATASET_SEQUENCE):
                    continue
                cbk_gdi.run_monthly_submission(db, tid, last_month, None, skip=done)
                ran += 1
            except Exception:
                db.rollback()
                logger.exception("cbk_gdi_submit: tenant %s failed", tid)
        if ran:
            logger.info("cbk_gdi_submit: submitted %d tenants for %s",
                        ran, last_month.isoformat())
    except Exception:
        logger.exception("cbk_gdi_submit: aborted")
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# WP8 — one run per job per time slot, across every copy of the backend.
#   On AWS the backend runs as two or more copies. Each copy has its own
#   scheduler, so without this every job (SMS reminders, CBK filing...) would
#   run once per copy. Before running, a job claims (job_id, slot) in
#   scheduled_job_runs; the database lets only one copy claim it.
# --------------------------------------------------------------------------- #
def _slot(every_minutes: int | None = None) -> str:
    """The current time slot: for an interval job, the n-th block of
    ``every_minutes`` since 1970; for a fixed-time job, the local date and hour."""
    from zoneinfo import ZoneInfo
    if every_minutes:
        return f"every{every_minutes}m:{int(datetime.utcnow().timestamp() // (every_minutes * 60))}"
    tz = getattr(settings, "SCHEDULER_TIMEZONE", None) or "Africa/Nairobi"
    return datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%dT%H")


def claim_run(db, job_id: str, slot: str) -> int | None:
    """Claim this slot for this job. Returns the run id, or None if another copy
    of the backend already has it."""
    import socket
    from sqlalchemy import text
    row = db.execute(text(
        "INSERT INTO scheduled_job_runs (job_id, slot, host, started_at, status) "
        "VALUES (:j, :s, :h, now(), 'running') "
        "ON CONFLICT (job_id, slot) DO NOTHING RETURNING id"),
        {"j": job_id, "s": slot, "h": socket.gethostname()[:120]}).first()
    db.commit()
    return row[0] if row else None


def finish_run(db, run_id: int, status: str, detail: str | None = None) -> None:
    from sqlalchemy import text
    db.execute(text("UPDATE scheduled_job_runs SET finished_at = now(), status = :st, "
                    "detail = :d WHERE id = :i"),
               {"st": status, "d": (detail or "")[:2000] or None, "i": run_id})
    db.commit()


def guarded(job_id: str, fn, every_minutes: int | None = None):
    """Wrap a job so that it runs at most once per slot across all copies."""
    def _run():
        db = SessionLocal()
        try:
            try:
                run_id = claim_run(db, job_id, _slot(every_minutes))
            except Exception:
                db.rollback()
                logger.exception("scheduled_job_failed job=%s: could not claim a run slot", job_id)
                return
            if run_id is None:
                return                      # another copy is running / ran it
            status, detail = "ok", None
            try:
                out = fn()
                if isinstance(out, dict):
                    detail = ", ".join(f"{k}={v}" for k, v in out.items())[:2000]
            except Exception as exc:        # jobs normally catch their own errors
                status, detail = "failed", f"{type(exc).__name__}: {exc}"
                logger.exception("scheduled_job_failed job=%s", job_id)
            try:
                finish_run(db, run_id, status, detail)
            except Exception:
                db.rollback()
                logger.exception("%s: could not record the run result", job_id)
        finally:
            db.close()
    _run.__name__ = f"guarded_{job_id}"
    return _run


def run_mpesa_pull():
    """WP12: hourly, ask Safaricom for the last few hours of paybill and till
    payments and record any whose notification never arrived."""
    from app.services import mpesa_pull
    db = SessionLocal()
    try:
        res = mpesa_pull.run_pull_all(db)
        if res["recovered"]:
            logger.warning("mpesa_pull: recovered %d payment(s)", res["recovered"])
        return res
    finally:
        db.close()


def run_lifecycle_sms():
    """Hourly (WP8): repayment reminders, overdue alerts and defaulting for each
    DCP whose automation is on and whose chosen send hour (Kenyan time) is now.
    Before WP8 nothing called this on a schedule."""
    from app.routers.notifications import run_all_tenants
    db = SessionLocal()
    try:
        out = run_all_tenants(db)
        return {"hour": out["hour"], "dcps_processed": out["tenants_processed"]}
    finally:
        db.close()


SEND_HOUR = 7   # reports go out from 07:00 Kenyan time


def _latest_slot(freq: str, now_local):
    """The most recent moment (local time) a schedule of this frequency was due:
    07:00 today (daily), 07:00 last Monday (weekly), 07:00 on the 1st (monthly).
    None when the first slot has not yet arrived."""
    base = now_local.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
    if freq == "daily":
        slot = base
        if slot > now_local:
            slot -= timedelta(days=1)
        return slot
    if freq == "monthly":
        slot = base.replace(day=1)
        if slot > now_local:
            prev = (slot - timedelta(days=1)).replace(day=1)
            slot = prev
        return slot
    slot = base - timedelta(days=now_local.weekday())   # weekly: Monday
    if slot > now_local:
        slot -= timedelta(days=7)
    return slot


def _as_local(value, tz):
    if value is None:
        return None
    if value.tzinfo is None:
        from datetime import timezone
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz)


def _schedule_due(schedule, now_local) -> bool:
    """Is a report schedule due? It is due when its latest slot (07:00 Kenyan
    time daily / on Mondays / on the 1st) has passed and it has not been sent
    since. WP10: a slot missed while the servers were down is sent as soon as
    they are back (once, however many slots were missed), instead of waiting
    for the next slot. A new schedule waits for its first slot after creation."""
    freq = (schedule.frequency or "weekly").lower()
    slot = _latest_slot(freq, now_local)
    last_local = _as_local(schedule.last_run_at, now_local.tzinfo)
    if last_local is not None:
        return last_local < slot
    created = _as_local(getattr(schedule, "created_at", None), now_local.tzinfo)
    return created is None or created < slot


def _slot_label(schedule, now_local) -> str | None:
    """'Monday 9 Nov 07:00' when the report is going out late (catch-up)."""
    slot = _latest_slot((schedule.frequency or "weekly").lower(), now_local)
    if now_local - slot > timedelta(hours=2):
        return f"{slot:%A %d %b %H:%M}"
    return None


def run_report_schedules(now_local=None):
    """Hourly (WP8): e-mail each due report schedule as a CSV attachment.
    Before WP8 schedules could be created but nothing ever sent them."""
    from zoneinfo import ZoneInfo
    from app.models import ReportSchedule, Tenant
    from app.routers.reporting import report_csv
    from app.services import mailer

    tz = getattr(settings, "SCHEDULER_TIMEZONE", None) or "Africa/Nairobi"
    now_local = now_local or datetime.now(ZoneInfo(tz))
    from datetime import timezone as _tz
    stamp = now_local.astimezone(_tz.utc).replace(tzinfo=None)   # stored as UTC
    db = SessionLocal()
    sent = failed = 0
    try:
        rows = (db.query(ReportSchedule).join(Tenant, Tenant.id == ReportSchedule.tenant_id)
                .filter(ReportSchedule.active == True, Tenant.active == True)  # noqa: E712
                .order_by(ReportSchedule.id).all())
        for sch in rows:
            if not _schedule_due(sch, now_local):
                continue
            try:
                recipients = ",".join(a.strip() for a in (sch.recipients or "").split(",")
                                      if "@" in a)
                if not recipients:
                    sch.last_status, sch.last_error = "failed", "No valid e-mail recipients."
                elif not mailer.is_configured():
                    sch.last_status, sch.last_error = "failed", "E-mail (SMTP) is not set up on the platform."
                else:
                    body, count = report_csv(db, sch.tenant_id, sch.report_type)
                    late = _slot_label(sch, now_local)
                    note = (f"\n\nThis report was due on {late} and is sent late because "
                            f"the system was unavailable then. The figures are as of now."
                            if late else "")
                    ok = mailer.send_email(
                        recipients, f"{sch.name} - {now_local:%d %b %Y}",
                        f"Attached: {sch.name} ({sch.report_type.replace('_', ' ')}), "
                        f"{count} rows, as of {now_local:%d %b %Y %H:%M}.{note}\n\nSent by Finyl.",
                        attachments=[(f"{sch.report_type}-{now_local:%Y%m%d}.csv",
                                      body.encode("utf-8"), "text/csv")])
                    sch.last_status = "sent" if ok else "failed"
                    sch.last_error = None if ok else "The e-mail server did not accept the message."
                # Mark the slot as used either way, so a broken address is not
                # retried every hour; the screen shows the failure.
                sch.last_run_at = stamp
                db.commit()
                if sch.last_status == "sent":
                    sent += 1
                else:
                    failed += 1
            except Exception as exc:
                db.rollback()
                failed += 1
                logger.exception("report_schedules: schedule %s failed", sch.id)
                try:
                    sch.last_status, sch.last_error = "failed", str(exc)[:500]
                    sch.last_run_at = stamp
                    db.commit()
                except Exception:
                    db.rollback()
        return {"sent": sent, "failed": failed}
    finally:
        db.close()


def purge_old_job_runs(days: int = 30):
    from sqlalchemy import text
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM scheduled_job_runs WHERE started_at < now() - make_interval(days => :d)"),
                   {"d": days})
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("purge_old_job_runs failed")
    finally:
        db.close()


def start_scheduler():
    """Start the background scheduler once. Safe to call on app startup; logs a
    warning and no-ops if APScheduler is unavailable or disabled by config."""
    global _scheduler
    if not settings.SCHEDULER_ENABLED:
        logger.info("scheduler disabled via SCHEDULER_ENABLED=false")
        return
    if _scheduler is not None:
        return  # already started (guard against double-start)
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as exc:  # pragma: no cover
        logger.warning("APScheduler unavailable — auto-reconcile disabled: %s", exc)
        return
    try:
        sched = BackgroundScheduler(daemon=True)
        sched.add_job(guarded("auto_reconcile", run_auto_reconcile,
                              settings.SCHEDULER_INTERVAL_MINUTES), "interval",
                      minutes=settings.SCHEDULER_INTERVAL_MINUTES,
                      id="auto_reconcile", max_instances=1,
                      coalesce=True, replace_existing=True)
        # Durable-webhook workers: retry failed Daraja events on exponential
        # backoff (idempotent reprocessing) and purge raw payloads past retention.
        sched.add_job(guarded("webhook_retry", run_webhook_retry, 2), "interval",
                      minutes=2,
                      id="webhook_retry", max_instances=1,
                      coalesce=True, replace_existing=True)
        sched.add_job(guarded("webhook_purge", run_webhook_purge, 60), "interval",
                      minutes=60,
                      id="webhook_purge", max_instances=1,
                      coalesce=True, replace_existing=True)
        # Phase 2 field/collections jobs. `cron` triggers so they fire at fixed
        # local times rather than on an interval from boot.
        tz = getattr(settings, "SCHEDULER_TIMEZONE", None) or "Africa/Nairobi"
        sched.add_job(guarded("ptp_reminders", run_ptp_reminders), "cron", hour=8, minute=0,
                      id="ptp_reminders", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        sched.add_job(guarded("gps_stipend", run_gps_stipend), "cron", hour=22, minute=0,
                      id="gps_stipend", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        # Collection efficiency: 1st of every month at 02:00.
        sched.add_job(guarded("collection_efficiency", run_collection_efficiency), "cron", day=1, hour=2, minute=0,
                      id="collection_efficiency", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        # OTP cleanup: hourly is plenty (tokens expire in 5 min).
        sched.add_job(guarded("otp_cleanup", run_otp_cleanup, 30), "interval", minutes=30,
                      id="otp_cleanup", max_instances=1, coalesce=True,
                      replace_existing=True)
        # CBK GDI monthly submission: check daily at 03:00; the job itself only
        # acts during the 5th–10th weekday filing window for the prior month.
        sched.add_job(guarded("cbk_gdi_submit", run_cbk_monthly_gdi_submit), "cron", hour=3, minute=0,
                      id="cbk_gdi_submit", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        # WP8: lifecycle SMS (each DCP's chosen hour) and scheduled report
        # e-mails, both checked a few minutes past every hour; old run records
        # are deleted daily.
        sched.add_job(guarded("lifecycle_sms", run_lifecycle_sms), "cron", minute=5,
                      id="lifecycle_sms", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        sched.add_job(guarded("report_schedules", run_report_schedules), "cron", minute=10,
                      id="report_schedules", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        sched.add_job(guarded("mpesa_pull", run_mpesa_pull), "cron", minute=20,
                      id="mpesa_pull", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        sched.add_job(guarded("purge_job_runs", purge_old_job_runs), "cron", hour=4, minute=30,
                      id="purge_job_runs", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        sched.start()
        _scheduler = sched
        logger.info("scheduler started: auto_reconcile every %d min "
                    "(resolve payouts stuck > %d min); webhook_retry every 2 min; "
                    "webhook_purge every 60 min (raw retention %dh); "
                    "ptp_reminders 08:00, gps_stipend 22:00, "
                    "collection_efficiency monthly, otp_cleanup every 30 min, "
                    "cbk_gdi_submit daily 03:00 (5th–10th weekday window), lifecycle_sms and "
                    "report_schedules hourly; one run per slot across copies (tz=%s)",
                    settings.SCHEDULER_INTERVAL_MINUTES,
                    settings.SCHEDULER_STUCK_MINUTES,
                    settings.WEBHOOK_RAW_RETENTION_HOURS, tz)
    except Exception:
        logger.exception("scheduler failed to start — app continues without it")


def shutdown_scheduler():
    """Stop the scheduler cleanly on app shutdown."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("scheduler stopped")
        except Exception:
            logger.exception("scheduler shutdown error")
        finally:
            _scheduler = None
