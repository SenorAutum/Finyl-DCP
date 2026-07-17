"""Consumer Protection & Complaints Registry with 14-day SLA tracking."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_module
from app.models import Complaint, COMPLAINT_CATEGORIES, COMPLAINT_STATUSES
from app.schemas import ComplaintCreate, ComplaintUpdate
from app.services import analytics, sms

router = APIRouter(prefix="/api/v1/complaints", tags=["complaints"])


def _dict(c: Complaint) -> dict:
    return {
        "id": c.id, "ticket_id": c.ticket_id, "borrower_id": c.borrower_id,
        "borrower_name": c.borrower.full_name if c.borrower else None,
        "borrower_phone": c.borrower.phone if c.borrower else None,
        "category": c.category, "description": c.description, "status": c.status,
        "created_at": c.created_at, "sla_deadline": c.sla_deadline,
        "resolved_at": c.resolved_at, "assigned_staff_id": c.assigned_staff_id,
        "assigned_staff_name": c.assigned_staff.name if c.assigned_staff else None,
        "remedial_action": c.remedial_action,
    }


@router.get("/meta")
def meta(tenant_id: int = Depends(require_module("complaints"))):
    return {"categories": COMPLAINT_CATEGORIES, "statuses": COMPLAINT_STATUSES}


@router.get("/stats")
def stats(tenant_id: int = Depends(require_module("complaints")), db: Session = Depends(get_db)):
    return analytics.complaint_sla_stats(db, tenant_id)


@router.get("")
def list_complaints(tenant_id: int = Depends(require_module("complaints")),
                    db: Session = Depends(get_db),
                    status: str = "", category: str = "", page: int = 1, page_size: int = 20):
    q = (db.query(Complaint).options(joinedload(Complaint.borrower), joinedload(Complaint.assigned_staff))
         .filter(Complaint.tenant_id == tenant_id))
    if status:
        q = q.filter(Complaint.status == status)
    if category:
        q = q.filter(Complaint.category == category)
    total = q.count()
    rows = q.order_by(Complaint.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "items": [_dict(c) for c in rows]}


@router.post("")
def create_complaint(body: ComplaintCreate, tenant_id: int = Depends(require_module("complaints")),
                     db: Session = Depends(get_db)):
    now = datetime.utcnow()
    seq = db.query(Complaint).filter(Complaint.tenant_id == tenant_id).count() + 1
    c = Complaint(
        tenant_id=tenant_id,
        ticket_id=f"{body.category.upper()}/{tenant_id}/{seq}",
        borrower_id=body.borrower_id, category=body.category,
        description=body.description, assigned_staff_id=body.assigned_staff_id,
        status="open", created_at=now, sla_deadline=Complaint.compute_sla(now),
    )
    db.add(c)
    db.commit()
    return _dict(c)


@router.patch("/{complaint_id}")
def update_complaint(complaint_id: int, body: ComplaintUpdate,
                     tenant_id: int = Depends(require_module("complaints")),
                     db: Session = Depends(get_db)):
    c = (db.query(Complaint).options(joinedload(Complaint.borrower))
         .filter(Complaint.id == complaint_id, Complaint.tenant_id == tenant_id).first())
    if not c:
        raise HTTPException(404, "Complaint not found")
    if body.status and body.status not in COMPLAINT_STATUSES:
        raise HTTPException(400, "Invalid status")
    if body.assigned_staff_id is not None:
        c.assigned_staff_id = body.assigned_staff_id
    if body.remedial_action is not None:
        c.remedial_action = body.remedial_action
    if body.status:
        was_resolved = c.status in ("resolved", "closed")
        c.status = body.status
        if body.status in ("resolved", "closed") and not was_resolved:
            c.resolved_at = datetime.utcnow()
            # Ticket resolution triggers a confirmation SMS to the borrower
            if c.borrower and c.borrower.phone:
                sms.sms_ticket_resolution(db, tenant_id, c.borrower.phone, c.ticket_id)
    db.commit()
    return _dict(c)
