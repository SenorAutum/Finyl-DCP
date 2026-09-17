"""KYC mismatch escalations (Phase 2)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_module, require_permission, write_audit
from app.models import (KycMismatchEscalation, KYC_ESCALATION_STATUSES,
                        MISMATCH_TYPES, Borrower)
from app.schemas import KycEscalationCreate, KycEscalationUpdate

router = APIRouter(prefix="/api/v1/kyc-escalations", tags=["kyc-escalations"])


def _dict(e: KycMismatchEscalation) -> dict:
    return {"id": e.id, "client_id": e.client_id, "mismatch_type": e.mismatch_type,
            "mismatch_detail": e.mismatch_detail, "logged_by": e.logged_by,
            "escalated_to": e.escalated_to, "status": e.status,
            "resolution_note": e.resolution_note, "created_at": e.created_at,
            "resolved_at": e.resolved_at}


@router.get("/meta")
def meta(tenant_id: int = Depends(require_module("lending"))):
    return {"statuses": KYC_ESCALATION_STATUSES, "mismatch_types": MISMATCH_TYPES}


@router.post("")
def create_escalation(body: KycEscalationCreate,
                      tenant_id: int = Depends(require_module("lending")),
                      user=Depends(require_permission("kyc.escalate")),
                      db: Session = Depends(get_db), request: Request = None):
    if body.mismatch_type not in MISMATCH_TYPES:
        raise HTTPException(400, "Invalid mismatch_type")
    if not db.query(Borrower).filter(Borrower.id == body.client_id,
                                     Borrower.tenant_id == tenant_id).first():
        raise HTTPException(404, "Client not found")
    e = KycMismatchEscalation(
        tenant_id=tenant_id, client_id=body.client_id, mismatch_type=body.mismatch_type,
        mismatch_detail=body.mismatch_detail, logged_by=user.id,
        escalated_to=body.escalated_to, status="open",
    )
    db.add(e)
    write_audit(db, tenant_id=tenant_id, user=user, action="kyc.escalate",
                entity_type="client", entity_id=body.client_id,
                details={"mismatch_type": body.mismatch_type}, request=request)
    db.commit()
    return _dict(e)


@router.get("")
def list_escalations(status: str = "", client_id: int = 0,
                     tenant_id: int = Depends(require_module("lending")),
                     user=Depends(require_permission("kyc.resolve")),
                     db: Session = Depends(get_db)):
    q = db.query(KycMismatchEscalation).filter(KycMismatchEscalation.tenant_id == tenant_id)
    if status:
        q = q.filter(KycMismatchEscalation.status == status)
    if client_id:
        q = q.filter(KycMismatchEscalation.client_id == client_id)
    rows = q.order_by(KycMismatchEscalation.created_at.desc()).all()
    return {"items": [_dict(e) for e in rows]}


@router.get("/{escalation_id}")
def get_escalation(escalation_id: int, tenant_id: int = Depends(require_module("lending")),
                   user=Depends(require_permission("kyc.resolve")),
                   db: Session = Depends(get_db)):
    e = (db.query(KycMismatchEscalation)
         .filter(KycMismatchEscalation.id == escalation_id,
                 KycMismatchEscalation.tenant_id == tenant_id).first())
    if not e:
        raise HTTPException(404, "Escalation not found")
    return _dict(e)


@router.patch("/{escalation_id}")
def update_escalation(escalation_id: int, body: KycEscalationUpdate,
                      tenant_id: int = Depends(require_module("lending")),
                      user=Depends(require_permission("kyc.resolve")),
                      db: Session = Depends(get_db), request: Request = None):
    e = (db.query(KycMismatchEscalation)
         .filter(KycMismatchEscalation.id == escalation_id,
                 KycMismatchEscalation.tenant_id == tenant_id).first())
    if not e:
        raise HTTPException(404, "Escalation not found")
    if body.status and body.status not in KYC_ESCALATION_STATUSES:
        raise HTTPException(400, "Invalid status")
    if body.escalated_to is not None:
        e.escalated_to = body.escalated_to
    if body.resolution_note is not None:
        e.resolution_note = body.resolution_note
    if body.status:
        e.status = body.status
        if body.status in ("resolved", "overridden"):
            e.resolved_at = datetime.now(timezone.utc)
    write_audit(db, tenant_id=tenant_id, user=user, action="kyc.resolve",
                entity_type="client", entity_id=e.client_id,
                details={"status": e.status}, request=request)
    db.commit()
    return _dict(e)
