"""CBK Compliance & Reporting: AML monitor + simulated regulatory exports."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_module
from app.models import AmlFlag
from app.services import aml, cbk_exports

router = APIRouter(prefix="/api/v1/cbk", tags=["cbk_reporting"])


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
