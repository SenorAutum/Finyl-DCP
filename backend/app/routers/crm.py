"""CRM & Field Sales Tracker: Kanban pipeline + geo-tagged site visits."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_module
from app.models import CrmLead, CRM_STAGES, SiteVisit
from app.schemas import LeadCreate, LeadStageUpdate, SiteVisitCreate

router = APIRouter(prefix="/api/v1/crm", tags=["crm"])


def _lead_dict(l: CrmLead) -> dict:
    return {
        "id": l.id, "name": l.name, "phone": l.phone, "sector": l.sector,
        "region_id": l.region_id, "stage": l.stage,
        "assigned_staff_id": l.assigned_staff_id,
        "assigned_staff_name": l.assigned_staff.name if l.assigned_staff else None,
        "estimated_loan_amount": float(l.estimated_loan_amount or 0),
        "notes": l.notes, "created_at": l.created_at,
        "visit_count": len(l.visits),
    }


@router.get("/board")
def kanban_board(tenant_id: int = Depends(require_module("crm")), db: Session = Depends(get_db)):
    leads = (db.query(CrmLead).options(joinedload(CrmLead.assigned_staff), joinedload(CrmLead.visits))
             .filter(CrmLead.tenant_id == tenant_id).order_by(CrmLead.created_at.desc()).all())
    columns = {stage: [] for stage in CRM_STAGES}
    for l in leads:
        columns.setdefault(l.stage, []).append(_lead_dict(l))
    return {"stages": CRM_STAGES, "columns": columns}


@router.post("/leads")
def create_lead(body: LeadCreate, tenant_id: int = Depends(require_module("crm")),
                db: Session = Depends(get_db)):
    lead = CrmLead(tenant_id=tenant_id, **body.model_dump())
    db.add(lead)
    db.commit()
    return _lead_dict(lead)


@router.patch("/leads/{lead_id}/stage")
def move_lead(lead_id: int, body: LeadStageUpdate,
              tenant_id: int = Depends(require_module("crm")), db: Session = Depends(get_db)):
    if body.stage not in CRM_STAGES:
        raise HTTPException(400, f"Invalid stage. Valid: {CRM_STAGES}")
    lead = db.query(CrmLead).filter(CrmLead.id == lead_id, CrmLead.tenant_id == tenant_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    lead.stage = body.stage
    db.commit()
    return _lead_dict(lead)


@router.get("/leads/{lead_id}/visits")
def lead_visits(lead_id: int, tenant_id: int = Depends(require_module("crm")),
                db: Session = Depends(get_db)):
    visits = (db.query(SiteVisit).options(joinedload(SiteVisit.staff))
              .filter(SiteVisit.lead_id == lead_id, SiteVisit.tenant_id == tenant_id)
              .order_by(SiteVisit.visit_date.desc()).all())
    return [{
        "id": v.id, "lead_id": v.lead_id, "staff_id": v.staff_id,
        "staff_name": v.staff.name if v.staff else None,
        "visit_date": v.visit_date, "latitude": v.latitude, "longitude": v.longitude,
        "outcome": v.outcome, "notes": v.notes,
    } for v in visits]


@router.post("/visits")
def create_visit(body: SiteVisitCreate, tenant_id: int = Depends(require_module("crm")),
                 db: Session = Depends(get_db)):
    lead = db.query(CrmLead).filter(CrmLead.id == body.lead_id, CrmLead.tenant_id == tenant_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    v = SiteVisit(tenant_id=tenant_id, **body.model_dump())
    db.add(v)
    db.commit()
    return {"id": v.id}
