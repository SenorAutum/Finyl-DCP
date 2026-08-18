"""SMS notifications: manual send, dispatch log viewer and scheduled-job endpoints
(repayment reminders 3 days before due; overdue/penalty alerts)."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_module, require_role
from app.models import (Loan, SmsLog, Tenant, User, SmsAutomationSetting,
                        SMS_AUTOMATION_DEFAULT_ENABLED, SMS_AUTOMATION_DEFAULT_HOUR)
from app.schemas import SendSmsRequest
from app.services import sms

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


def get_automation_config(db: Session, tenant_id: int) -> dict:
    """Resolve a tenant's SMS automation config, falling back to defaults when
    the tenant has no stored row yet."""
    row = (db.query(SmsAutomationSetting)
           .filter(SmsAutomationSetting.tenant_id == tenant_id).first())
    if row is None:
        return {"tenant_id": tenant_id,
                "automation_enabled": SMS_AUTOMATION_DEFAULT_ENABLED,
                "send_hour": SMS_AUTOMATION_DEFAULT_HOUR,
                "source": "default", "updated_at": None}
    return {"tenant_id": tenant_id,
            "automation_enabled": bool(row.automation_enabled),
            "send_hour": int(row.send_hour),
            "source": "custom",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


# ---------------------------------------------------------------------------
# Per-tenant job helpers (shared by the tenant-scoped endpoints and the
# super-admin all-tenants runner). Each mutates/queues SMS but does NOT commit —
# callers commit so a batch can be atomic per tenant.
# ---------------------------------------------------------------------------
def _run_repayment_reminders(db: Session, tenant_id: int) -> int:
    horizon = date.today() + timedelta(days=3)
    loans = (db.query(Loan).options(joinedload(Loan.borrower))
             .filter(Loan.tenant_id == tenant_id, Loan.status == "active",
                     Loan.due_date != None, Loan.due_date <= horizon,
                     Loan.due_date >= date.today(), Loan.outstanding_balance > 0).all())
    for l in loans:
        days_left = (l.due_date - date.today()).days
        sms.sms_repayment_reminder(db, tenant_id, l.borrower, l, days_left)
    return len(loans)


def _run_overdue_alerts(db: Session, tenant_id: int) -> dict:
    today = date.today()
    loans = (db.query(Loan).options(joinedload(Loan.borrower))
             .filter(Loan.tenant_id == tenant_id, Loan.status.in_(["active", "overdue"]),
                     Loan.due_date != None, Loan.due_date < today,
                     Loan.outstanding_balance > 0).all())
    flipped = 0
    for l in loans:
        if l.status == "active":
            l.status = "overdue"
            flipped += 1
        sms.sms_overdue_alert(db, tenant_id, l.borrower, l)
    return {"alerts_sent": len(loans), "flipped_to_overdue": flipped}


def _run_defaulting(db: Session, tenant_id: int, grace_days: int = 30) -> int:
    """Flip long-overdue loans to defaulted (due_date older than grace window)
    and notify borrowers. Only loans with an outstanding balance are affected."""
    cutoff = date.today() - timedelta(days=grace_days)
    loans = (db.query(Loan).options(joinedload(Loan.borrower))
             .filter(Loan.tenant_id == tenant_id, Loan.status.in_(["active", "overdue"]),
                     Loan.due_date != None, Loan.due_date < cutoff,
                     Loan.outstanding_balance > 0).all())
    for l in loans:
        l.status = "defaulted"
        sms.sms_defaulted(db, tenant_id, l.borrower, l)
    return len(loans)


def run_tenant_jobs(db: Session, tenant_id: int, grace_days: int = 30) -> dict:
    """Run every lifecycle-SMS job for one tenant (does NOT commit — caller does).
    Shared by the super-admin all-tenants runner and the per-DCP 'Send now'."""
    reminders = _run_repayment_reminders(db, tenant_id)
    overdue = _run_overdue_alerts(db, tenant_id)
    defaulted = _run_defaulting(db, tenant_id, grace_days)
    return {
        "reminders_sent": reminders,
        "alerts_sent": overdue["alerts_sent"],
        "flipped_to_overdue": overdue["flipped_to_overdue"],
        "defaulted": defaulted,
    }


@router.post("/send-sms")
def send_sms_endpoint(body: SendSmsRequest, tenant_id: int = Depends(require_module("payments")),
                      db: Session = Depends(get_db)):
    log = sms.send_sms(db, tenant_id, body.phone, body.message, body.trigger_type)
    db.commit()
    return {"id": log.id, "status": log.status}


@router.get("/sms-logs")
def sms_logs(tenant_id: int = Depends(require_module("payments")), db: Session = Depends(get_db),
             trigger_type: str = "", page: int = 1, page_size: int = 20):
    q = db.query(SmsLog).filter(SmsLog.tenant_id == tenant_id)
    if trigger_type:
        q = q.filter(SmsLog.trigger_type == trigger_type)
    total = q.count()
    rows = q.order_by(SmsLog.sent_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "items": [{
        "id": s.id, "recipient_phone": s.recipient_phone, "message": s.message,
        "trigger_type": s.trigger_type, "status": s.status, "sent_at": s.sent_at,
    } for s in rows]}


@router.post("/jobs/run-repayment-reminders")
def run_repayment_reminders(tenant_id: int = Depends(require_module("payments")),
                            _: User = Depends(require_role("super_admin")),
                            db: Session = Depends(get_db)):
    """Scan active loans due within 3 days and dispatch reminder SMS.
    In production, schedule this endpoint via cron/systemd timer."""
    sent = _run_repayment_reminders(db, tenant_id)
    db.commit()
    return {"reminders_sent": sent}


@router.post("/jobs/run-overdue-alerts")
def run_overdue_alerts(tenant_id: int = Depends(require_module("payments")),
                       _: User = Depends(require_role("super_admin")),
                       db: Session = Depends(get_db)):
    """Mark past-due active loans overdue and alert borrowers (penalty notice)."""
    result = _run_overdue_alerts(db, tenant_id)
    db.commit()
    return result


@router.post("/jobs/run-defaulting")
def run_defaulting(grace_days: int = 30,
                   tenant_id: int = Depends(require_module("payments")),
                   _: User = Depends(require_role("super_admin")),
                   db: Session = Depends(get_db)):
    """Flip loans overdue beyond the grace window to defaulted and notify borrowers."""
    defaulted = _run_defaulting(db, tenant_id, grace_days)
    db.commit()
    return {"defaulted": defaulted, "grace_days": grace_days}


@router.post("/jobs/run-all")
def run_all_jobs(hour: int | None = None, grace_days: int = 30,
                 user: User = Depends(require_role("super_admin")),
                 db: Session = Depends(get_db)):
    """Super-admin: run scheduled SMS jobs across tenants, RESPECTING per-DCP config.

    Intended to be invoked hourly by a scheduled task. For each tenant it:
      * skips tenants with automation disabled (they use 'Send now' manually);
      * skips tenants whose configured send_hour != the current server hour;
      * otherwise runs reminders + overdue alerts + defaulting, committing per
        tenant so one tenant's failure cannot roll back the others.

    `hour` overrides the current server hour (0-23) for testing.
    """
    current_hour = hour if hour is not None else datetime.now().hour
    if not 0 <= current_hour <= 23:
        current_hour = current_hour % 24
    results = []
    processed = 0
    for t in db.query(Tenant).order_by(Tenant.id).all():
        cfg = get_automation_config(db, t.id)
        if not cfg["automation_enabled"]:
            results.append({"tenant_id": t.id, "tenant": t.name,
                            "processed": False, "reason": "disabled"})
            continue
        if cfg["send_hour"] != current_hour:
            results.append({"tenant_id": t.id, "tenant": t.name,
                            "processed": False, "reason": "not-their-hour",
                            "send_hour": cfg["send_hour"]})
            continue
        try:
            counts = run_tenant_jobs(db, t.id, grace_days)
            db.commit()
            processed += 1
            results.append({"tenant_id": t.id, "tenant": t.name,
                            "processed": True, **counts})
        except Exception as exc:  # noqa: BLE001 — isolate per-tenant failures
            db.rollback()
            results.append({"tenant_id": t.id, "tenant": t.name,
                            "processed": False, "reason": "error", "error": str(exc)})
    return {"hour": current_hour, "tenants_seen": len(results),
            "tenants_processed": processed, "results": results}
