"""CRM & Field Sales Tracker: Kanban pipeline + geo-tagged site visits."""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import get_current_user, require_module, require_permission, write_audit
from app.core import obs
from app.models import Borrower, CrmLead, CRM_STAGES, SiteVisit, User
from app.schemas import LeadCreate, LeadStageUpdate, SiteVisitCreate
from app.services import kyc_decisions

router = APIRouter(prefix="/api/v1/crm", tags=["crm"])

# Lead "temperature" — an intra-stage qualifier orthogonal to the pipeline stage.
LEAD_TEMPERATURES = ["cold", "warm", "hot"]


def _lead_dict(l: CrmLead) -> dict:
    return {
        "id": l.id, "name": l.name, "phone": l.phone, "sector": l.sector,
        "region_id": l.region_id, "stage": l.stage,
        # Phase 2 — Cold/Warm/Hot temperature + BM approval + conversion state.
        "stage_detail": l.stage_detail,
        "bm_approval_status": l.bm_approval_status,
        "bm_approved_by": l.bm_approved_by,
        "bm_approved_at": l.bm_approved_at,
        "bm_rejection_reason": l.bm_rejection_reason,
        "converted_to_client_id": l.converted_to_client_id,
        "conversion_triggered_at": l.conversion_triggered_at,
        "conversion_validations": l.conversion_validations,
        "assigned_staff_id": l.assigned_staff_id,
        "assigned_staff_name": l.assigned_staff.name if l.assigned_staff else None,
        "estimated_loan_amount": float(l.estimated_loan_amount or 0),
        "notes": l.notes, "created_at": l.created_at,
        "visit_count": len(l.visits),
    }


def _get_lead(db: Session, tenant_id: int, lead_id: int) -> CrmLead:
    lead = db.query(CrmLead).filter(CrmLead.id == lead_id,
                                    CrmLead.tenant_id == tenant_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


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


# ---------------------------------------------------------------------------
# Phase 2 — Cold/Warm/Hot temperature, BM approval, convert-to-onboarding
# ---------------------------------------------------------------------------


@router.patch("/leads/{lead_id}/temperature")
def set_lead_temperature(lead_id: int, temperature: str = Body(..., embed=True),
                         tenant_id: int = Depends(require_module("crm")),
                         db: Session = Depends(get_db)):
    """Set a lead's Cold/Warm/Hot qualifier (independent of the pipeline stage)."""
    if temperature not in LEAD_TEMPERATURES:
        raise HTTPException(400, f"Invalid temperature. Valid: {LEAD_TEMPERATURES}")
    lead = _get_lead(db, tenant_id, lead_id)
    lead.stage_detail = temperature
    db.commit()
    return _lead_dict(lead)


@router.post("/leads/{lead_id}/bm-approve")
def bm_approve_lead(lead_id: int, request: Request,
                    user: User = Depends(get_current_user),
                    tenant_id: int = Depends(require_module("crm")),
                    _perm=Depends(require_permission("clients.approve")),
                    db: Session = Depends(get_db)):
    """Branch-Manager gate: approve a qualified lead for conversion."""
    lead = _get_lead(db, tenant_id, lead_id)
    lead.bm_approval_status = "approved"
    lead.bm_approved_by = user.id
    lead.bm_approved_at = datetime.now(timezone.utc)
    lead.bm_rejection_reason = None
    db.commit()
    write_audit(db, tenant_id=tenant_id, user=user, action="crm.lead.bm_approve",
                entity_type="crm_lead", entity_id=lead.id,
                details={"stage": lead.stage, "temperature": lead.stage_detail},
                request=request)
    return _lead_dict(lead)


@router.post("/leads/{lead_id}/bm-reject")
def bm_reject_lead(lead_id: int, request: Request, reason: str = Body(..., embed=True),
                   user: User = Depends(get_current_user),
                   tenant_id: int = Depends(require_module("crm")),
                   _perm=Depends(require_permission("clients.approve")),
                   db: Session = Depends(get_db)):
    """Branch-Manager gate: reject a lead with a documented reason."""
    lead = _get_lead(db, tenant_id, lead_id)
    lead.bm_approval_status = "rejected"
    lead.bm_approved_by = user.id
    lead.bm_approved_at = datetime.now(timezone.utc)
    lead.bm_rejection_reason = reason
    db.commit()
    write_audit(db, tenant_id=tenant_id, user=user, action="crm.lead.bm_reject",
                entity_type="crm_lead", entity_id=lead.id,
                details={"reason": reason}, request=request)
    return _lead_dict(lead)


@router.post("/leads/{lead_id}/convert-to-onboarding")
def convert_to_onboarding(lead_id: int, request: Request,
                          national_id: str = Body(..., embed=True),
                          date_of_birth: str | None = Body(None, embed=True),
                          user: User = Depends(get_current_user),
                          tenant_id: int = Depends(require_module("crm")),
                          _perm=Depends(require_permission("clients.create")),
                          db: Session = Depends(get_db)):
    """Convert a BM-approved lead into a draft client and run the KYC cascade.

    Creates a draft `Borrower` from the lead, links it back onto the lead, then
    runs the authoritative `kyc_decisions` engine and records the decision on the
    lead's `conversion_validations`. Requires prior Branch-Manager approval.
    """
    lead = _get_lead(db, tenant_id, lead_id)
    if lead.bm_approval_status != "approved":
        raise HTTPException(409, "Lead must be BM-approved before conversion")
    if lead.converted_to_client_id:
        raise HTTPException(409, "Lead already converted")

    # Split the lead's single name field into first/last for the Borrower record.
    parts = (lead.name or "").strip().split()
    first_name = parts[0] if parts else "Unknown"
    last_name = " ".join(parts[1:]) if len(parts) > 1 else first_name

    dob = None
    if date_of_birth:
        try:
            dob = datetime.fromisoformat(date_of_birth).date()
        except ValueError:
            raise HTTPException(400, "date_of_birth must be ISO format (YYYY-MM-DD)")

    borrower = Borrower(
        tenant_id=tenant_id, first_name=first_name, last_name=last_name,
        national_id=national_id, phone=lead.phone or "",
        date_of_birth=dob, region_id=lead.region_id,
        business_sector=lead.sector, kyc_status="draft",
        profile_status="draft", onboarded_by=user.email,
    )
    db.add(borrower)
    db.flush()  # assign borrower.id without ending the transaction

    lead.converted_to_client_id = borrower.id
    lead.conversion_triggered_at = datetime.now(timezone.utc)
    if "disbursed" not in (lead.stage or ""):
        lead.stage = "app_setup"
    db.commit()

    decision = kyc_decisions.evaluate(db, tenant_id=tenant_id, client_id=borrower.id)
    lead.conversion_validations = decision.as_dict()
    db.commit()

    obs.log_cbk_event(obs.CBK_CLIENT_CONVERTED, tenant_id=tenant_id, user_id=user.id,
                      entity_type="crm_lead", entity_id=lead.id,
                      outcome=decision.decision)
    write_audit(db, tenant_id=tenant_id, user=user, action="crm.lead.convert",
                entity_type="crm_lead", entity_id=lead.id,
                details={"client_id": borrower.id, "kyc_decision": decision.decision,
                         "reasons": decision.reasons}, request=request)
    return {"lead": _lead_dict(lead), "client_id": borrower.id,
            "kyc_decision": decision.as_dict()}
