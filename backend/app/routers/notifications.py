"""SMS notifications: manual send, dispatch log viewer and scheduled-job endpoints
(repayment reminders 3 days before due; overdue/penalty alerts)."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_module
from app.models import Loan, SmsLog
from app.schemas import SendSmsRequest
from app.services import sms

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


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
    horizon = date.today() + timedelta(days=3)
    loans = (db.query(Loan).options(joinedload(Loan.borrower))
             .filter(Loan.tenant_id == tenant_id, Loan.status == "active",
                     Loan.due_date != None, Loan.due_date <= horizon,
                     Loan.due_date >= date.today(), Loan.outstanding_balance > 0).all())
    for l in loans:
        days_left = (l.due_date - date.today()).days
        sms.sms_repayment_reminder(db, tenant_id, l.borrower, l, days_left)
    db.commit()
    return {"reminders_sent": len(loans)}


@router.post("/jobs/run-overdue-alerts")
def run_overdue_alerts(tenant_id: int = Depends(require_module("payments")),
                       db: Session = Depends(get_db)):
    """Mark past-due active loans overdue and alert borrowers (penalty notice)."""
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
    db.commit()
    return {"alerts_sent": len(loans), "flipped_to_overdue": flipped}
