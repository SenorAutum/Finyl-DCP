"""
HQ Operations — central reporting & monitoring. Strictly read-only over the
loan book; the only writes are report-schedule / template / anomaly-flag records
(operational metadata, not business transactions).
"""
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import get_tenant_id, require_permission, write_audit
from app.models import (Loan, Borrower, Repayment, Staff, Branch, User,
                        ReportSchedule, ReportTemplate, AnomalyFlag)
from app.schemas import ReportScheduleCreate, ReportTemplateCreate, AnomalyFlagCreate

router = APIRouter(prefix="/api/v1/reporting", tags=["reporting"])

REPORT_TYPES = ["loan_book", "par", "disbursement", "collections", "productivity"]


# ---------------------------------------------------------------------------
# Report exports (CSV download)
# ---------------------------------------------------------------------------
def _csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in columns})
    return buf.getvalue()


@router.get("/export/{report_type}")
def export_report(report_type: str, request: Request,
                  tenant_id: int = Depends(get_tenant_id),
                  user: User = Depends(require_permission("reports.export")),
                  db: Session = Depends(get_db)):
    if report_type not in REPORT_TYPES:
        raise HTTPException(400, f"Unknown report type. One of {REPORT_TYPES}")

    loans = (db.query(Loan).options(joinedload(Loan.borrower), joinedload(Loan.staff))
             .filter(Loan.tenant_id == tenant_id).all())
    rows, columns = [], []

    if report_type == "loan_book":
        columns = ["account_number", "client", "principal", "status", "outstanding", "branch_id"]
        rows = [{"account_number": l.account_number,
                 "client": l.borrower.full_name if l.borrower else "",
                 "principal": float(l.principal), "status": l.status,
                 "outstanding": float(l.outstanding_balance or 0),
                 "branch_id": l.branch_id} for l in loans]
    elif report_type == "par":
        # Portfolio-at-risk by branch: outstanding on overdue/defaulted vs total.
        agg = {}
        for l in loans:
            a = agg.setdefault(l.branch_id, {"branch_id": l.branch_id, "outstanding": 0.0, "at_risk": 0.0})
            out = float(l.outstanding_balance or 0)
            a["outstanding"] += out
            if l.status in ("overdue", "defaulted"):
                a["at_risk"] += out
        columns = ["branch_id", "outstanding", "at_risk", "par_pct"]
        for a in agg.values():
            a["par_pct"] = round(100 * a["at_risk"] / a["outstanding"], 2) if a["outstanding"] else 0
            rows.append(a)
    elif report_type == "disbursement":
        columns = ["account_number", "client", "principal", "disbursement_date"]
        rows = [{"account_number": l.account_number,
                 "client": l.borrower.full_name if l.borrower else "",
                 "principal": float(l.principal),
                 "disbursement_date": l.disbursement_date.isoformat() if l.disbursement_date else ""}
                for l in loans if l.disbursement_date]
    elif report_type == "collections":
        reps = (db.query(Repayment).filter(Repayment.tenant_id == tenant_id)
                .order_by(Repayment.payment_date.desc()).limit(2000).all())
        columns = ["loan_id", "amount", "method", "payment_date", "mpesa_ref"]
        rows = [{"loan_id": r.loan_id, "amount": float(r.amount), "method": r.method,
                 "payment_date": r.payment_date.isoformat() if r.payment_date else "",
                 "mpesa_ref": r.mpesa_ref or ""} for r in reps]
    elif report_type == "productivity":
        agg = {}
        for l in loans:
            key = l.staff_id
            a = agg.setdefault(key, {"staff_id": key, "loans": 0, "principal": 0.0})
            a["loans"] += 1; a["principal"] += float(l.principal)
        for a in agg.values():
            s = db.get(Staff, a["staff_id"]) if a["staff_id"] else None
            a["officer"] = s.name if s else "Unassigned"
        columns = ["staff_id", "officer", "loans", "principal"]
        rows = list(agg.values())

    write_audit(db, tenant_id=tenant_id, user=user, action="report.export",
                entity_type="report", entity_id=report_type,
                details={"rows": len(rows)}, request=request)
    db.commit()
    body = _csv(rows, columns)
    return Response(content=body, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{report_type}.csv"'})


# ---------------------------------------------------------------------------
# Report schedules
# ---------------------------------------------------------------------------
@router.get("/schedules")
def list_schedules(tenant_id: int = Depends(get_tenant_id),
                   _: User = Depends(require_permission("reports.schedule")),
                   db: Session = Depends(get_db)):
    rows = db.query(ReportSchedule).filter(ReportSchedule.tenant_id == tenant_id).order_by(ReportSchedule.id.desc()).all()
    return [{"id": s.id, "name": s.name, "report_type": s.report_type, "frequency": s.frequency,
             "recipients": s.recipients, "active": s.active,
             "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None} for s in rows]


@router.post("/schedules")
def create_schedule(body: ReportScheduleCreate, request: Request,
                    tenant_id: int = Depends(get_tenant_id),
                    user: User = Depends(require_permission("reports.schedule")),
                    db: Session = Depends(get_db)):
    if body.report_type not in REPORT_TYPES:
        raise HTTPException(400, "Unknown report type")
    s = ReportSchedule(tenant_id=tenant_id, user_id=user.id, name=body.name,
                       report_type=body.report_type, frequency=body.frequency,
                       recipients=body.recipients)
    db.add(s); db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="report.schedule_create",
                entity_type="report_schedule", entity_id=s.id, details=body.model_dump(), request=request)
    db.commit()
    return {"id": s.id, "name": s.name, "report_type": s.report_type, "frequency": s.frequency}


@router.delete("/schedules/{sid}")
def delete_schedule(sid: int, tenant_id: int = Depends(get_tenant_id),
                    _: User = Depends(require_permission("reports.schedule")),
                    db: Session = Depends(get_db)):
    row = db.query(ReportSchedule).filter(ReportSchedule.id == sid, ReportSchedule.tenant_id == tenant_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row); db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Report templates
# ---------------------------------------------------------------------------
@router.get("/templates")
def list_templates(tenant_id: int = Depends(get_tenant_id),
                   _: User = Depends(require_permission("reports.template")),
                   db: Session = Depends(get_db)):
    rows = db.query(ReportTemplate).filter(ReportTemplate.tenant_id == tenant_id).order_by(ReportTemplate.id.desc()).all()
    return [{"id": t.id, "name": t.name, "definition": t.definition} for t in rows]


@router.post("/templates")
def create_template(body: ReportTemplateCreate, request: Request,
                    tenant_id: int = Depends(get_tenant_id),
                    user: User = Depends(require_permission("reports.template")),
                    db: Session = Depends(get_db)):
    t = ReportTemplate(tenant_id=tenant_id, user_id=user.id, name=body.name, definition=body.definition)
    db.add(t); db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="report.template_create",
                entity_type="report_template", entity_id=t.id, request=request)
    db.commit()
    return {"id": t.id, "name": t.name, "definition": t.definition}


# ---------------------------------------------------------------------------
# Anomaly flags (routed to Compliance)
# ---------------------------------------------------------------------------
@router.get("/anomalies")
def list_anomalies(tenant_id: int = Depends(get_tenant_id),
                   _: User = Depends(require_permission("reports.flag", "audit.view", mode="any")),
                   db: Session = Depends(get_db)):
    rows = db.query(AnomalyFlag).filter(AnomalyFlag.tenant_id == tenant_id).order_by(AnomalyFlag.id.desc()).all()
    out = []
    for a in rows:
        u = db.get(User, a.user_id) if a.user_id else None
        out.append({"id": a.id, "entity_type": a.entity_type, "entity_id": a.entity_id,
                    "note": a.note, "status": a.status,
                    "flagged_by": u.email if u else None,
                    "created_at": a.created_at.isoformat() if a.created_at else None})
    return out


@router.post("/anomalies")
def create_anomaly(body: AnomalyFlagCreate, request: Request,
                   tenant_id: int = Depends(get_tenant_id),
                   user: User = Depends(require_permission("reports.flag")),
                   db: Session = Depends(get_db)):
    a = AnomalyFlag(tenant_id=tenant_id, user_id=user.id, entity_type=body.entity_type,
                    entity_id=body.entity_id, note=body.note)
    db.add(a); db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="anomaly.flag",
                entity_type="anomaly", entity_id=a.id, details={"note": body.note}, request=request)
    db.commit()
    return {"id": a.id, "status": a.status}
