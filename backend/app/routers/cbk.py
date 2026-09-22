"""CBK Compliance & Reporting: AML monitor + simulated regulatory exports + GDI submission."""
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.core.database import SessionLocal, get_db
from app.core.deps import get_current_user, require_module
from app.models import AmlFlag, CbkSubmissionLog, User
from app.services import aml, cbk_exports, cbk_gdi

router = APIRouter(prefix="/api/v1/cbk", tags=["cbk_reporting"])


# ---------- GDI submission schemas -------------------------------------------
class GdiSubmitIn(BaseModel):
    reporting_month: str  # "YYYY-MM"


class GdiStatusIn(BaseModel):
    reporting_month: str  # "YYYY-MM"


class GdiNilIn(BaseModel):
    reporting_month: str  # "YYYY-MM"
    dataset_name: str     # customer | loan | repayment | overdue | complaint


def _parse_month(value: str) -> date:
    """Parse a 'YYYY-MM' string to the first day of that month."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m").date().replace(day=1)
    except (ValueError, AttributeError):
        raise HTTPException(400, "reporting_month must be in YYYY-MM format")


@router.post("/aml/scan")
def aml_scan(tenant_id: int = Depends(require_module("cbk_reporting")), db: Session = Depends(get_db)):
    created = aml.run_aml_scan(db, tenant_id)
    return {"new_flags": len(created)}


@router.get("/aml/flags")
def aml_flags(tenant_id: int = Depends(require_module("cbk_reporting")), db: Session = Depends(get_db),
              reviewed: str = ""):
    q = (db.query(AmlFlag).options(joinedload(AmlFlag.borrower))
         .filter(AmlFlag.tenant_id == tenant_id))
    if reviewed == "true":
        q = q.filter(AmlFlag.reviewed)
    elif reviewed == "false":
        q = q.filter(~AmlFlag.reviewed)
    rows = q.order_by(AmlFlag.flagged_at.desc()).all()
    return [{
        "id": f.id, "loan_id": f.loan_id, "borrower_id": f.borrower_id,
        "borrower_name": f.borrower.full_name if f.borrower else None,
        "flag_type": f.flag_type, "severity": f.severity, "details": f.details,
        "flagged_at": f.flagged_at, "reviewed": f.reviewed,
    } for f in rows]


@router.post("/aml/flags/{flag_id}/review")
def review_flag(flag_id: int, tenant_id: int = Depends(require_module("cbk_reporting")),
                db: Session = Depends(get_db)):
    f = db.query(AmlFlag).filter(AmlFlag.id == flag_id, AmlFlag.tenant_id == tenant_id).first()
    if not f:
        raise HTTPException(404, "Flag not found")
    f.reviewed = True
    db.commit()
    return {"ok": True}


# ---------- Regulatory exports (simulated templates from live ledger data) --------

@router.get("/exports/asset-quality", response_class=PlainTextResponse)
def export_asset_quality(tenant_id: int = Depends(require_module("cbk_reporting")),
                         db: Session = Depends(get_db)):
    content = cbk_exports.asset_quality_csv(db, tenant_id)
    return PlainTextResponse(content, media_type="text/csv", headers={
        "Content-Disposition": f"attachment; filename=asset_quality_{date.today()}.csv"})


@router.get("/exports/capital-adequacy", response_class=PlainTextResponse)
def export_capital_adequacy(tenant_id: int = Depends(require_module("cbk_reporting")),
                            db: Session = Depends(get_db)):
    content = cbk_exports.capital_adequacy_csv(db, tenant_id)
    return PlainTextResponse(content, media_type="text/csv", headers={
        "Content-Disposition": f"attachment; filename=capital_adequacy_{date.today()}.csv"})


@router.get("/exports/crb-daily", response_class=PlainTextResponse)
def export_crb_daily(tenant_id: int = Depends(require_module("cbk_reporting")),
                     db: Session = Depends(get_db)):
    content = cbk_exports.crb_daily_txt(db, tenant_id)
    return PlainTextResponse(content, media_type="text/plain", headers={
        "Content-Disposition": f"attachment; filename=crb_daily_{date.today()}.txt"})


# ---------- CBK GDI monthly submission ---------------------------------------

def _run_submission_bg(tenant_id: int, reporting_month: date, user_id: int):
    """Background worker: runs the monthly submission with its own DB session."""
    db = SessionLocal()
    try:
        cbk_gdi.run_monthly_submission(db, tenant_id, reporting_month, user_id)
    except Exception:  # pragma: no cover - defensive; errors are logged per-dataset
        import logging
        logging.getLogger("cbk_gdi").exception(
            "Background GDI submission failed tenant=%s month=%s", tenant_id, reporting_month)
    finally:
        db.close()


@router.post("/gdi/submit")
def gdi_submit(body: GdiSubmitIn, background: BackgroundTasks,
               tenant_id: int = Depends(require_module("cbk_reporting")),
               user: User = Depends(get_current_user)):
    reporting_month = _parse_month(body.reporting_month)
    background.add_task(_run_submission_bg, tenant_id, reporting_month, user.id)
    return {
        "message": "Submission started",
        "reporting_month": reporting_month.isoformat(),
        "datasets": [cbk_gdi.DATASET_LABELS[k] for k in cbk_gdi.DATASET_SEQUENCE],
    }


@router.get("/gdi/submissions")
def gdi_submissions(tenant_id: int = Depends(require_module("cbk_reporting")),
                    db: Session = Depends(get_db)):
    rows = (db.query(CbkSubmissionLog)
            .filter(CbkSubmissionLog.tenant_id == tenant_id)
            .order_by(CbkSubmissionLog.submitted_at.desc())
            .limit(200).all())
    return [{
        "id": r.id,
        "reporting_month": r.reporting_month.isoformat() if r.reporting_month else None,
        "dataset_name": r.dataset_name,
        "request_id": str(r.request_id) if r.request_id else None,
        "cbk_request_id": r.cbk_request_id,
        "status": r.status,
        "rows_submitted": r.rows_submitted,
        "error_detail": r.error_detail,
        "submitted_at": r.submitted_at,
        "status_checked_at": r.status_checked_at,
        "accepted_at": r.accepted_at,
    } for r in rows]


@router.get("/gdi/submissions/{log_id}")
def gdi_submission_detail(log_id: int,
                          tenant_id: int = Depends(require_module("cbk_reporting")),
                          db: Session = Depends(get_db)):
    r = (db.query(CbkSubmissionLog)
         .filter(CbkSubmissionLog.id == log_id,
                 CbkSubmissionLog.tenant_id == tenant_id).first())
    if not r:
        raise HTTPException(404, "Submission not found")
    return {
        "id": r.id,
        "reporting_month": r.reporting_month.isoformat() if r.reporting_month else None,
        "dataset_name": r.dataset_name,
        "request_id": str(r.request_id) if r.request_id else None,
        "cbk_request_id": r.cbk_request_id,
        "status": r.status,
        "rows_submitted": r.rows_submitted,
        "error_detail": r.error_detail,
        "response_body": r.response_body,
        "submitted_at": r.submitted_at,
        "status_checked_at": r.status_checked_at,
        "accepted_at": r.accepted_at,
    }


@router.post("/gdi/check-status")
def gdi_check_status(body: GdiStatusIn,
                     tenant_id: int = Depends(require_module("cbk_reporting")),
                     db: Session = Depends(get_db)):
    reporting_month = _parse_month(body.reporting_month)
    try:
        result = cbk_gdi.check_submission_status(reporting_month)
    except cbk_gdi.ConfigurationError as exc:
        raise HTTPException(400, str(exc))
    except cbk_gdi.SubmissionError as exc:
        raise HTTPException(502, str(exc))
    # Stamp status_checked_at on this month's logs for the tenant.
    db.query(CbkSubmissionLog).filter(
        CbkSubmissionLog.tenant_id == tenant_id,
        CbkSubmissionLog.reporting_month == reporting_month,
    ).update({"status_checked_at": datetime.utcnow()})
    db.commit()
    return result


@router.post("/gdi/nil-submission")
def gdi_nil_submission(body: GdiNilIn,
                       tenant_id: int = Depends(require_module("cbk_reporting")),
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    reporting_month = _parse_month(body.reporting_month)
    key = body.dataset_name.strip().lower()
    if key not in cbk_gdi.ENVELOPE_KEYS:
        raise HTTPException(400, f"Unknown dataset_name: {body.dataset_name}")
    label = cbk_gdi.DATASET_LABELS[key]
    import uuid as _uuid
    request_id = str(_uuid.uuid4())
    log = CbkSubmissionLog(
        tenant_id=tenant_id, reporting_month=reporting_month,
        dataset_name=f"{label} (Nil)", request_id=request_id, status="pending",
        rows_submitted=0, submitted_by=user.id,
    )
    db.add(log)
    db.commit()
    try:
        result = cbk_gdi.submit_dataset(key, [], reporting_month, request_id)
        log.status = "submitted" if result["ok"] else "error"
        log.response_body = result["response"]
        if not result["ok"]:
            log.error_detail = f"HTTP {result['status_code']}"
        db.commit()
    except cbk_gdi.ConfigurationError as exc:
        log.status = "error"
        log.error_detail = str(exc)
        db.commit()
        raise HTTPException(400, str(exc))
    except cbk_gdi.SubmissionError as exc:
        log.status = "error"
        log.error_detail = str(exc)
        db.commit()
        raise HTTPException(502, str(exc))
    return {"ok": result["ok"], "request_id": request_id, "status": log.status}
