"""Super Admin: tenant management + module feature-flag matrix."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_role
from app.models import (MODULE_KEYS, Borrower, Loan, MpesaWebhookEvent, SmsLog,
                        Staff, StaffDailyTask, Tenant, TenantModule, User)
from app.schemas import ModuleToggle, TenantCreate
from app.services import analytics

router = APIRouter(prefix="/api/v1/admin", tags=["admin"],
                   dependencies=[Depends(require_role("super_admin"))])


@router.get("/module-matrix")
def module_matrix(db: Session = Depends(get_db)):
    """Grid of tenants × modules with enabled flags."""
    tenants = db.query(Tenant).order_by(Tenant.id).all()
    flags = db.query(TenantModule).all()
    fmap = {(f.tenant_id, f.module_key): f.enabled for f in flags}
    return {
        "module_keys": MODULE_KEYS,
        "tenants": [{
            "id": t.id, "name": t.name, "code": t.code,
            "logo_color": t.logo_color, "active": t.active,
            "modules": {k: fmap.get((t.id, k), False) for k in MODULE_KEYS},
        } for t in tenants],
    }


@router.post("/tenants")
def create_tenant(body: TenantCreate, db: Session = Depends(get_db)):
    tenant = Tenant(**body.model_dump())
    db.add(tenant)
    db.flush()
    for key in MODULE_KEYS:  # new tenants start with all modules on
        db.add(TenantModule(tenant_id=tenant.id, module_key=key, enabled=True))
    db.commit()
    return {"id": tenant.id, "name": tenant.name}


@router.patch("/tenants/{tenant_id}")
def update_tenant(tenant_id: int, body: TenantCreate, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    for k, v in body.model_dump().items():
        setattr(tenant, k, v)
    db.commit()
    return {"ok": True}


@router.post("/modules/toggle")
def toggle_module(body: ModuleToggle, db: Session = Depends(get_db)):
    if body.module_key not in MODULE_KEYS:
        raise HTTPException(400, f"Unknown module '{body.module_key}'")
    row = (db.query(TenantModule)
           .filter(TenantModule.tenant_id == body.tenant_id,
                   TenantModule.module_key == body.module_key).first())
    if not row:
        row = TenantModule(tenant_id=body.tenant_id, module_key=body.module_key)
        db.add(row)
    row.enabled = body.enabled
    db.commit()
    return {"tenant_id": body.tenant_id, "module_key": body.module_key, "enabled": row.enabled}


@router.get("/webhook-health")
def webhook_health(db: Session = Depends(get_db)):
    """Dead-letter / durability dashboard for the Daraja webhook pipeline.

    Platform-wide (super_admin): counts every mpesa_webhook_events row by
    processing_status and lists the most recent dead-lettered events (payload
    excluded — only non-PII metadata) so operators can alert/act on them.
    """
    rows = (db.query(MpesaWebhookEvent.processing_status,
                     func.count(MpesaWebhookEvent.id))
            .group_by(MpesaWebhookEvent.processing_status).all())
    counts = {status: count for status, count in rows}
    for status in ("received", "processed", "failed", "dead"):
        counts.setdefault(status, 0)

    dead = (db.query(MpesaWebhookEvent)
            .filter(MpesaWebhookEvent.processing_status == "dead")
            .order_by(MpesaWebhookEvent.received_at.desc())
            .limit(50).all())
    return {
        "counts": counts,
        "dead_total": counts["dead"],
        "dead_events": [{
            "id": e.id,
            "endpoint": e.endpoint,
            "tenant_id": e.tenant_id,
            "shortcode": e.shortcode,
            "attempts": e.attempts,
            "last_error": e.last_error,
            "received_at": e.received_at.isoformat() if e.received_at else None,
        } for e in dead],
    }


# --------------------------- Super-admin tenants dashboard ------------------------

def _loan_bucket(status: str) -> str:
    s = (status or "").lower()
    if s in ("pending", "underwriting", "processing"):
        return "pending"
    if s == "approved":
        return "approved"
    if s == "rejected":
        return "rejected"
    if s in ("active",):
        return "performing"
    if s in ("overdue", "defaulted"):
        return "non_performing"
    if s == "paid":
        return "closed"
    return "other"


@router.get("/tenants-dashboard")
def tenants_dashboard(days: int = 30, db: Session = Depends(get_db)):
    """Platform command-centre for the super-admin: every tenant on one screen.

    For each tenant we surface the aspects the super-admin reviews:
      * ``portfolio``     — headline lending KPIs (PAR, outstanding, disbursement)
      * ``loan_status``   — loan book bucketed approved/rejected/pending/
                            performing/non_performing/closed
      * ``subscription``  — billing proxy = SMS revenue (billable sell price) over
                            the window, with reminder-SMS volume broken out
      * ``sla``           — complaint SLA performance (within-SLA %, breaches)
      * ``operations``    — staff headcount, client count, pending field tasks

    ``days`` (default 30) controls the SMS-revenue / reminder window.
    A platform ``totals`` roll-up is included for the headline strip.
    """
    since = datetime.utcnow() - timedelta(days=max(days, 1))
    tenants = db.query(Tenant).order_by(Tenant.id).all()

    # ---- Pre-aggregate SMS revenue + reminders per tenant (one query each) ----
    sms_rev_rows = (db.query(SmsLog.tenant_id,
                             func.coalesce(func.sum(SmsLog.sell_price_kes), 0),
                             func.count(SmsLog.id))
                    .filter(SmsLog.billable == True,  # noqa: E712
                            SmsLog.sent_at >= since)
                    .group_by(SmsLog.tenant_id).all())
    sms_rev = {tid: {"revenue": float(rev or 0), "billable_msgs": int(cnt or 0)}
               for tid, rev, cnt in sms_rev_rows}

    reminder_rows = (db.query(SmsLog.tenant_id, func.count(SmsLog.id))
                     .filter(SmsLog.sent_at >= since,
                             SmsLog.trigger_type.in_(["repayment_reminder", "overdue_alert"]))
                     .group_by(SmsLog.tenant_id).all())
    reminders = {tid: int(cnt or 0) for tid, cnt in reminder_rows}

    staff_rows = (db.query(Staff.tenant_id, func.count(Staff.id))
                  .filter(Staff.active == True)  # noqa: E712
                  .group_by(Staff.tenant_id).all())
    staff_counts = {tid: int(cnt or 0) for tid, cnt in staff_rows}

    client_rows = (db.query(Borrower.tenant_id, func.count(Borrower.id))
                   .group_by(Borrower.tenant_id).all())
    client_counts = {tid: int(cnt or 0) for tid, cnt in client_rows}

    task_rows = (db.query(StaffDailyTask.tenant_id, func.count(StaffDailyTask.id))
                 .filter(StaffDailyTask.status.in_(["planned", "active"]))
                 .group_by(StaffDailyTask.tenant_id).all())
    pending_tasks = {tid: int(cnt or 0) for tid, cnt in task_rows}

    out = []
    totals = {"revenue": 0.0, "clients": 0, "staff": 0, "outstanding": 0.0,
              "loans": 0, "reminders": 0, "active_tenants": 0}

    for t in tenants:
        # Loan status buckets (raw query — avoids loading the whole df twice).
        lstatus = {"approved": 0, "rejected": 0, "pending": 0,
                   "performing": 0, "non_performing": 0, "closed": 0, "other": 0}
        for status, cnt in (db.query(Loan.status, func.count(Loan.id))
                            .filter(Loan.tenant_id == t.id)
                            .group_by(Loan.status).all()):
            lstatus[_loan_bucket(status)] += int(cnt or 0)
        loans_total = sum(lstatus.values())

        # Portfolio KPIs via analytics (dataframe-based, tenant-scoped).
        try:
            loans_df = analytics.load_loans_df(db, t.id)
            reps_df = analytics.load_repayments_df(db, t.id)
            pk = analytics.portfolio_kpis(loans_df, reps_df)
        except Exception:  # analytics must never break the whole dashboard
            pk = {"par_30": 0, "total_outstanding": 0, "disbursement_volume": 0,
                  "repayment_rate": 0, "active_loans": 0, "overdue_loans": 0}

        try:
            sla = analytics.complaint_sla_stats(db, t.id)
        except Exception:
            sla = {"total": 0, "open": 0, "breached": 0, "within_sla_pct": 0}

        rev = sms_rev.get(t.id, {"revenue": 0.0, "billable_msgs": 0})
        row = {
            "id": t.id, "name": t.name, "code": t.code,
            "logo_color": t.logo_color, "active": t.active,
            "portfolio": {
                "par_30": pk.get("par_30", 0),
                "total_outstanding": pk.get("total_outstanding", 0),
                "disbursement_volume": pk.get("disbursement_volume", 0),
                "repayment_rate": pk.get("repayment_rate", 0),
                "active_loans": pk.get("active_loans", 0),
                "overdue_loans": pk.get("overdue_loans", 0),
            },
            "loan_status": lstatus,
            "loans_total": loans_total,
            "subscription": {
                "sms_revenue_kes": round(rev["revenue"], 2),
                "billable_msgs": rev["billable_msgs"],
                "reminders_sent": reminders.get(t.id, 0),
                "window_days": max(days, 1),
            },
            "sla": {
                "total": sla.get("total", 0),
                "open": sla.get("open", 0),
                "breached": sla.get("breached", 0),
                "within_sla_pct": sla.get("within_sla_pct", 0),
            },
            "operations": {
                "staff": staff_counts.get(t.id, 0),
                "clients": client_counts.get(t.id, 0),
                "pending_tasks": pending_tasks.get(t.id, 0),
            },
        }
        out.append(row)

        totals["revenue"] += rev["revenue"]
        totals["clients"] += client_counts.get(t.id, 0)
        totals["staff"] += staff_counts.get(t.id, 0)
        totals["outstanding"] += float(pk.get("total_outstanding", 0) or 0)
        totals["loans"] += loans_total
        totals["reminders"] += reminders.get(t.id, 0)
        if t.active:
            totals["active_tenants"] += 1

    totals["revenue"] = round(totals["revenue"], 2)
    totals["outstanding"] = round(totals["outstanding"], 2)
    totals["tenant_count"] = len(tenants)

    return {"window_days": max(days, 1), "totals": totals, "tenants": out}
