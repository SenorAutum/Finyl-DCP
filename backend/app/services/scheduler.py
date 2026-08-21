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
        sched.start()
        _scheduler = sched
        logger.info("scheduler started: auto_reconcile every %d min "
                    "(resolve payouts stuck > %d min)",
                    settings.SCHEDULER_INTERVAL_MINUTES,
                    settings.SCHEDULER_STUCK_MINUTES)
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
