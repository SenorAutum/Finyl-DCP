"""Client-edit maker-checker workflow (Phase 2).

Edits to a borrower whose loan is active are gated through tiered approval:
- secondary tier: lower-sensitivity fields -> client_edits.approve_secondary
- primary tier:   high-sensitivity fields  -> client_edits.approve_primary
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (require_module, require_permission, get_current_user,
                           write_audit)
from app.models import ClientEditRequest, Borrower
from app.schemas import ClientEditRequestIn, ClientEditRejectIn
from app.services import client_edit as edit_service
from app.services.authz import effective_permissions

router = APIRouter(prefix="/api/v1/clients", tags=["client-edits"])


def _dict(r: ClientEditRequest) -> dict:
    return {"id": r.id, "client_id": r.client_id, "edit_tier": r.edit_tier,
            "requested_by": r.requested_by, "field_changes": r.field_changes,
            "supporting_docs": r.supporting_docs, "status": r.status,
            "approved_by": r.approved_by, "approved_at": r.approved_at,
            "rejected_at": r.rejected_at, "rejection_reason": r.rejection_reason,
            "requested_at": r.requested_at}


@router.get("/edit-requests")
def list_edit_requests(status: str = "pending", client_id: int = 0,
                       tenant_id: int = Depends(require_module("clients")),
                       user=Depends(require_permission(
                           "client_edits.approve_secondary",
                           "client_edits.approve_primary", mode="any")),
                       db: Session = Depends(get_db)):
    q = db.query(ClientEditRequest).filter(ClientEditRequest.tenant_id == tenant_id)
    if status:
        q = q.filter(ClientEditRequest.status == status)
    if client_id:
        q = q.filter(ClientEditRequest.client_id == client_id)
    rows = q.order_by(ClientEditRequest.requested_at.desc()).all()
    return {"items": [_dict(r) for r in rows]}


@router.post("/{client_id}/edit-request")
def create_edit_request(client_id: int, body: ClientEditRequestIn,
                        tenant_id: int = Depends(require_module("clients")),
                        user=Depends(require_permission("client_edits.request")),
                        db: Session = Depends(get_db), request: Request = None):
    if not db.query(Borrower).filter(Borrower.id == client_id,
                                     Borrower.tenant_id == tenant_id).first():
        raise HTTPException(404, "Client not found")
    try:
        req = edit_service.create_request(
            db, tenant_id=tenant_id, client_id=client_id, requested_by=user.id,
            field_changes=body.field_changes, edit_tier=body.edit_tier,
            supporting_docs=body.supporting_docs)
    except ValueError as e:
        raise HTTPException(400, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="client_edits.request",
                entity_type="client", entity_id=client_id,
                details={"request_id": req.id, "tier": req.edit_tier}, request=request)
    db.commit()
    return _dict(req)


def _assert_can_approve(db: Session, user, tier: str):
    if user.role == "super_admin":
        return
    perms = effective_permissions(db, user.tenant_id, user.role)
    needed = ("client_edits.approve_primary" if tier == "primary"
              else "client_edits.approve_secondary")
    if needed not in perms:
        raise HTTPException(403, f"Missing required permission: {needed}")


@router.post("/edit-requests/{request_id}/approve")
def approve_edit_request(request_id: int,
                         tenant_id: int = Depends(require_module("clients")),
                         user=Depends(get_current_user),
                         db: Session = Depends(get_db), request: Request = None):
    req = (db.query(ClientEditRequest)
           .filter(ClientEditRequest.id == request_id,
                   ClientEditRequest.tenant_id == tenant_id).first())
    if not req:
        raise HTTPException(404, "Edit request not found")
    _assert_can_approve(db, user, req.edit_tier)
    try:
        req = edit_service.approve(db, tenant_id=tenant_id, request_id=request_id,
                                   approved_by=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="client_edits.approve",
                entity_type="client", entity_id=req.client_id,
                details={"request_id": request_id, "tier": req.edit_tier}, request=request)
    db.commit()
    return _dict(req)


@router.post("/edit-requests/{request_id}/reject")
def reject_edit_request(request_id: int, body: ClientEditRejectIn,
                        tenant_id: int = Depends(require_module("clients")),
                        user=Depends(get_current_user),
                        db: Session = Depends(get_db), request: Request = None):
    req = (db.query(ClientEditRequest)
           .filter(ClientEditRequest.id == request_id,
                   ClientEditRequest.tenant_id == tenant_id).first())
    if not req:
        raise HTTPException(404, "Edit request not found")
    _assert_can_approve(db, user, req.edit_tier)
    try:
        req = edit_service.reject(db, tenant_id=tenant_id, request_id=request_id,
                                  approved_by=user.id, reason=body.rejection_reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="client_edits.reject",
                entity_type="client", entity_id=req.client_id,
                details={"request_id": request_id}, request=request)
    db.commit()
    return _dict(req)
