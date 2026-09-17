"""Client-edit maker-checker workflow (Phase 2).

When a loan is active the borrower record is edit-locked. Changes to locked
fields are proposed as tiered requests and applied only once approved. Primary
tier = high-sensitivity fields (phone, national_id, dob); secondary = the rest.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ClientEditRequest, Borrower, Loan

# Fields that require the higher (primary) approval tier when edited.
PRIMARY_FIELDS = {"phone", "national_id", "date_of_birth", "dob", "first_name", "last_name"}

# Borrower attributes that an approved edit request may write back.
_APPLYABLE = {
    "first_name", "last_name", "phone", "alt_phone", "national_id",
    "date_of_birth", "address", "occupation", "email", "gender",
}


def has_active_loan(db: Session, *, tenant_id: int, client_id: int) -> bool:
    return (db.query(Loan)
            .filter(Loan.tenant_id == tenant_id, Loan.borrower_id == client_id,
                    Loan.status.in_(["active", "disbursed", "arrears", "overdue"]))
            .first() is not None)


def infer_tier(field_changes: dict) -> str:
    fields = set(field_changes or {})
    return "primary" if fields & PRIMARY_FIELDS else "secondary"


def create_request(db: Session, *, tenant_id: int, client_id: int, requested_by: int,
                   field_changes: dict, edit_tier: str | None = None,
                   supporting_docs: list | None = None) -> ClientEditRequest:
    if not field_changes:
        raise ValueError("no_changes")
    tier = edit_tier or infer_tier(field_changes)
    req = ClientEditRequest(
        tenant_id=tenant_id, client_id=client_id, edit_tier=tier,
        requested_by=requested_by, field_changes=field_changes,
        supporting_docs=supporting_docs, status="pending",
    )
    db.add(req)
    db.commit()
    return req


def approve(db: Session, *, tenant_id: int, request_id: int, approved_by: int) -> ClientEditRequest:
    req = (db.query(ClientEditRequest)
           .filter(ClientEditRequest.id == request_id,
                   ClientEditRequest.tenant_id == tenant_id).first())
    if not req:
        raise ValueError("request_not_found")
    if req.status != "pending":
        raise ValueError("not_pending")
    borrower = db.query(Borrower).filter(Borrower.id == req.client_id,
                                         Borrower.tenant_id == tenant_id).first()
    if not borrower:
        raise ValueError("client_not_found")
    # Apply approved field changes.
    for field, change in (req.field_changes or {}).items():
        if field not in _APPLYABLE:
            continue
        new_val = change.get("new_value") if isinstance(change, dict) else change
        setattr(borrower, field, new_val)
    req.status = "approved"
    req.approved_by = approved_by
    req.approved_at = datetime.now(timezone.utc)
    db.commit()
    return req


def reject(db: Session, *, tenant_id: int, request_id: int, approved_by: int,
           reason: str) -> ClientEditRequest:
    req = (db.query(ClientEditRequest)
           .filter(ClientEditRequest.id == request_id,
                   ClientEditRequest.tenant_id == tenant_id).first())
    if not req:
        raise ValueError("request_not_found")
    if req.status != "pending":
        raise ValueError("not_pending")
    req.status = "rejected"
    req.approved_by = approved_by
    req.rejected_at = datetime.now(timezone.utc)
    req.rejection_reason = reason
    db.commit()
    return req
