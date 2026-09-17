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
    if configured:
        # Real creds: read-only report — leave resolution to Daraja status polling.
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
                sms.send_sms(db, ptp.tenant_id, phone, msg, category="ptp_reminder")
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
        sched.add_job(run_auto_reconcile, "interval",
                      minutes=settings.SCHEDULER_INTERVAL_MINUTES,
                      id="auto_reconcile", max_instances=1,
                      coalesce=True, replace_existing=True)
        # Durable-webhook workers: retry failed Daraja events on exponential
        # backoff (idempotent reprocessing) and purge raw payloads past retention.
        sched.add_job(run_webhook_retry, "interval",
                      minutes=2,
                      id="webhook_retry", max_instances=1,
                      coalesce=True, replace_existing=True)
        sched.add_job(run_webhook_purge, "interval",
                      minutes=60,
                      id="webhook_purge", max_instances=1,
                      coalesce=True, replace_existing=True)
        # Phase 2 field/collections jobs. `cron` triggers so they fire at fixed
        # local times rather than on an interval from boot.
        tz = getattr(settings, "SCHEDULER_TIMEZONE", None) or "Africa/Nairobi"
        sched.add_job(run_ptp_reminders, "cron", hour=8, minute=0,
                      id="ptp_reminders", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        sched.add_job(run_gps_stipend, "cron", hour=22, minute=0,
                      id="gps_stipend", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        # Collection efficiency: 1st of every month at 02:00.
        sched.add_job(run_collection_efficiency, "cron", day=1, hour=2, minute=0,
                      id="collection_efficiency", max_instances=1, coalesce=True,
                      replace_existing=True, timezone=tz)
        # OTP cleanup: hourly is plenty (tokens expire in 5 min).
        sched.add_job(run_otp_cleanup, "interval", minutes=30,
                      id="otp_cleanup", max_instances=1, coalesce=True,
                      replace_existing=True)
        sched.start()
        _scheduler = sched
        logger.info("scheduler started: auto_reconcile every %d min "
                    "(resolve payouts stuck > %d min); webhook_retry every 2 min; "
                    "webhook_purge every 60 min (raw retention %dh); "
                    "ptp_reminders 08:00, gps_stipend 22:00, "
                    "collection_efficiency monthly, otp_cleanup every 30 min (tz=%s)",
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
