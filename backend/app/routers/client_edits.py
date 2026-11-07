"""Client-edit maker-checker workflow (Phase 2).

Edits to a borrower whose loan is open are gated through tiered approval:
- secondary tier: lower-sensitivity fields -> one approval
  (client_edits.approve_secondary or client_edits.approve_primary)
- primary tier: identity fields -> two approvals by two different people:
  a first approval (either permission), then a final approval
  (client_edits.approve_primary).
The person who requested an edit can never approve or reject it (WP6, F13).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (require_module, require_permission, get_current_user,
                           get_scope, UserScope, write_audit)
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
            "requested_at": r.requested_at,
            "first_approved_by": r.first_approved_by,
            "first_approved_at": r.first_approved_at,
            # Which approval is needed next: "first" or "final" (None once decided).
            "next_stage": edit_service.next_stage(r) if r.status == "pending" else None,
            "approvals_required": edit_service.approvals_needed(r)}


@router.get("/edit-requests")
def list_edit_requests(status: str = "pending", client_id: int = 0,
                       tenant_id: int = Depends(require_module("lending")),
                       user=Depends(require_permission(
                           "client_edits.approve_secondary",
                           "client_edits.approve_primary", mode="any")),
                       scope: UserScope = Depends(get_scope),
                       db: Session = Depends(get_db)):
    q = (db.query(ClientEditRequest)
         .join(Borrower, Borrower.id == ClientEditRequest.client_id)
         .filter(ClientEditRequest.tenant_id == tenant_id))
    q = scope.apply_client(q, Borrower)
    if status:
        q = q.filter(ClientEditRequest.status == status)
    if client_id:
        q = q.filter(ClientEditRequest.client_id == client_id)
    rows = q.order_by(ClientEditRequest.requested_at.desc()).all()
    return {"items": [_dict(r) for r in rows]}


@router.post("/{client_id}/edit-request")
def create_edit_request(client_id: int, body: ClientEditRequestIn,
                        tenant_id: int = Depends(require_module("lending")),
                        user=Depends(require_permission("client_edits.request")),
                        scope: UserScope = Depends(get_scope),
                        db: Session = Depends(get_db), request: Request = None):
    client = db.query(Borrower).filter(Borrower.id == client_id,
                                       Borrower.tenant_id == tenant_id).first()
    if not client:
        raise HTTPException(404, "Client not found")
    if not scope.can_see_client(client):
        raise HTTPException(403, "Client is outside your data scope")
    try:
        req = edit_service.create_request(
            db, tenant_id=tenant_id, client_id=client_id, requested_by=user.id,
            field_changes=body.field_changes, edit_tier=body.edit_tier,
            supporting_docs=body.supporting_docs)
    except ValueError as e:
        raise HTTPException(422, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="client_edits.request",
                entity_type="client", entity_id=client_id,
                details={"request_id": req.id, "tier": req.edit_tier}, request=request)
    db.commit()
    return _dict(req)


def _assert_can_approve(db: Session, user, req: ClientEditRequest, scope: UserScope):
    """Stage-aware permission check. The final approval of a primary-tier
    (identity) edit needs client_edits.approve_primary; every other decision
    accepts either approval permission."""
    client = db.get(Borrower, req.client_id)
    if client is None or not scope.can_see_client(client):
        raise HTTPException(403, "Client is outside your data scope")
    if user.role == "super_admin":
        return
    perms = effective_permissions(db, user.tenant_id, user.role)
    if req.edit_tier == "primary" and edit_service.next_stage(req) == "final":
        # the approval that applies an identity change always needs the
        # higher permission, whether the DCP asks for one approver or two
        needed = ("client_edits.approve_primary",)
    else:
        needed = ("client_edits.approve_secondary", "client_edits.approve_primary")
    if not any(p in perms for p in needed):
        raise HTTPException(403, f"Missing required permission: {' or '.join(needed)}")


@router.post("/edit-requests/{request_id}/approve")
def approve_edit_request(request_id: int,
                         tenant_id: int = Depends(require_module("lending")),
                         user=Depends(get_current_user),
                         scope: UserScope = Depends(get_scope),
                         db: Session = Depends(get_db), request: Request = None):
    req = (db.query(ClientEditRequest)
           .filter(ClientEditRequest.id == request_id,
                   ClientEditRequest.tenant_id == tenant_id).first())
    if not req:
        raise HTTPException(404, "Edit request not found")
    _assert_can_approve(db, user, req, scope)
    stage = edit_service.next_stage(req)
    try:
        req = edit_service.approve(db, tenant_id=tenant_id, request_id=request_id,
                                   approved_by=user.id)
    except edit_service.EditRefused as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409 if str(e) == "not_pending" else 422, str(e))
    write_audit(db, tenant_id=tenant_id, user=user,
                action=("client_edits.approve" if stage == "final"
                        else "client_edits.first_approval"),
                entity_type="client", entity_id=req.client_id,
                details={"request_id": request_id, "tier": req.edit_tier,
                         "fields": sorted(req.field_changes or {})}, request=request)
    db.commit()
    return _dict(req)


@router.post("/edit-requests/{request_id}/reject")
def reject_edit_request(request_id: int, body: ClientEditRejectIn,
                        tenant_id: int = Depends(require_module("lending")),
                        user=Depends(get_current_user),
                        scope: UserScope = Depends(get_scope),
                        db: Session = Depends(get_db), request: Request = None):
    req = (db.query(ClientEditRequest)
           .filter(ClientEditRequest.id == request_id,
                   ClientEditRequest.tenant_id == tenant_id).first())
    if not req:
        raise HTTPException(404, "Edit request not found")
    _assert_can_approve(db, user, req, scope)
    if not (body.rejection_reason or "").strip():
        raise HTTPException(422, "A rejection reason is required")
    try:
        req = edit_service.reject(db, tenant_id=tenant_id, request_id=request_id,
                                  approved_by=user.id, reason=body.rejection_reason)
    except edit_service.EditRefused as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409 if str(e) == "not_pending" else 422, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="client_edits.reject",
                entity_type="client", entity_id=req.client_id,
                details={"request_id": request_id}, request=request)
    db.commit()
    return _dict(req)
