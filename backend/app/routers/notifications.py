"""SMS notifications: manual send, dispatch log viewer and scheduled-job endpoints
(repayment reminders 3 days before due; overdue/penalty alerts)."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_module, require_role
from app.models import Loan, SmsLog, Tenant, User
from app.schemas import SendSmsRequest
from app.services import sms

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


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
                            db: Session = Depends(get_db)):
    """Scan active loans due within 3 days and dispatch reminder SMS.
    In production, schedule this endpoint via cron/systemd timer."""
    sent = _run_repayment_reminders(db, tenant_id)
    db.commit()
    return {"reminders_sent": sent}


@router.post("/jobs/run-overdue-alerts")
def run_overdue_alerts(tenant_id: int = Depends(require_module("payments")),
                       db: Session = Depends(get_db)):
    """Mark past-due active loans overdue and alert borrowers (penalty notice)."""
    result = _run_overdue_alerts(db, tenant_id)
    db.commit()
    return result


@router.post("/jobs/run-defaulting")
def run_defaulting(grace_days: int = 30,
                   tenant_id: int = Depends(require_module("payments")),
                   db: Session = Depends(get_db)):
    """Flip loans overdue beyond the grace window to defaulted and notify borrowers."""
    defaulted = _run_defaulting(db, tenant_id, grace_days)
    db.commit()
    return {"defaulted": defaulted, "grace_days": grace_days}


@router.post("/jobs/run-all")
def run_all_jobs(grace_days: int = 30,
                 user: User = Depends(require_role("super_admin")),
                 db: Session = Depends(get_db)):
    """Super-admin: run every scheduled SMS job across ALL tenants in one pass.

    Executes repayment reminders, overdue alerts and defaulting for each tenant,
    committing per tenant so one tenant's failure cannot roll back the others."""
    results = []
    for t in db.query(Tenant).order_by(Tenant.id).all():
        try:
            reminders = _run_repayment_reminders(db, t.id)
            overdue = _run_overdue_alerts(db, t.id)
            defaulted = _run_defaulting(db, t.id, grace_days)
            db.commit()
            results.append({
                "tenant_id": t.id, "tenant": t.name, "ok": True,
                "reminders_sent": reminders,
                "alerts_sent": overdue["alerts_sent"],
                "flipped_to_overdue": overdue["flipped_to_overdue"],
                "defaulted": defaulted,
            })
        except Exception as exc:  # noqa: BLE001 — isolate per-tenant failures
            db.rollback()
            results.append({"tenant_id": t.id, "tenant": t.name,
                            "ok": False, "error": str(exc)})
    return {"tenants_processed": len(results), "results": results}
