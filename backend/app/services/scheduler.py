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
        sched.start()
        _scheduler = sched
        logger.info("scheduler started: auto_reconcile every %d min "
                    "(resolve payouts stuck > %d min); webhook_retry every 2 min; "
                    "webhook_purge every 60 min (raw retention %dh)",
                    settings.SCHEDULER_INTERVAL_MINUTES,
                    settings.SCHEDULER_STUCK_MINUTES,
                    settings.WEBHOOK_RAW_RETENTION_HOURS)
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
