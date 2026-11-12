"""Client-edit maker-checker workflow (Phase 2).

When a loan is open the borrower record is edit-locked. Changes to locked
fields are proposed as tiered requests and applied only once approved. Primary
tier = identity fields (phone, national ID, date of birth, names) and needs two
different approvers (each DCP can lower this to one); secondary = the rest, one
approver. The requester can
never approve their own request (WP6, F13).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models import ClientEditRequest, Borrower, Loan

# Fields that require the higher (primary) approval tier when edited.
PRIMARY_FIELDS = {"phone", "national_id", "date_of_birth", "first_name", "middle_name",
                  "last_name"}
# Identity fields: approving a change to any of them makes earlier identity
# checks (eKYC, OCR match) stale for the new values.
IDENTITY_FIELDS = {"national_id", "date_of_birth", "first_name", "middle_name", "last_name"}

# Borrower attributes that an approved edit request may write back.
# (Only real Borrower columns: the old list included address / occupation /
# email, which do not exist on the record, so approving them silently did nothing.)
_APPLYABLE = {
    "first_name", "middle_name", "last_name", "phone", "alt_phone", "national_id",
    "date_of_birth", "gender", "business_sector", "district", "division",
    "location", "sub_location",
}
_ALIASES = {"dob": "date_of_birth"}

# A client is record-locked while any of their loans is in one of these states:
# approved (a payout is due to their number), on the books, or in default.
LOCKING_LOAN_STATUSES = ("approved", "active", "overdue", "defaulted",
                         "disbursed", "arrears", "written_off")


class EditRefused(ValueError):
    """A request or approval that breaks the maker-checker rules (HTTP 403)."""


def has_active_loan(db: Session, *, tenant_id: int, client_id: int) -> bool:
    return (db.query(Loan)
            .filter(Loan.tenant_id == tenant_id, Loan.borrower_id == client_id,
                    Loan.status.in_(LOCKING_LOAN_STATUSES))
            .first() is not None)


def infer_tier(field_changes: dict) -> str:
    fields = {_ALIASES.get(f, f) for f in (field_changes or {})}
    return "primary" if fields & PRIMARY_FIELDS else "secondary"


def _age(d: date) -> int:
    t = date.today()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def _clean_value(field: str, value):
    """Validate one proposed value; return it normalised or raise ValueError."""
    if value is not None and not isinstance(value, (str, int, float)):
        raise ValueError(f"invalid_value:{field}")
    v = None if value is None else str(value).strip()
    if field in ("first_name", "last_name"):
        if not v or len(v) > 60:
            raise ValueError(f"invalid_value:{field}")
        return v
    if field == "middle_name":
        if v and len(v) > 60:
            raise ValueError(f"invalid_value:{field}")
        return v or None
    if field in ("phone", "alt_phone"):
        if not v and field == "alt_phone":
            return None
        digits = re.sub(r"\D", "", v or "")
        if digits.startswith("254"):
            digits = "0" + digits[3:]
        if not re.fullmatch(r"0(7|1)\d{8}", digits):
            raise ValueError(f"invalid_value:{field}")
        return digits
    if field == "national_id":
        if not v or not re.fullmatch(r"\d{6,10}", v):
            raise ValueError("invalid_value:national_id")
        return v
    if field == "date_of_birth":
        try:
            d = date.fromisoformat(v or "")
        except ValueError:
            raise ValueError("invalid_value:date_of_birth")
        if _age(d) < 18:
            raise ValueError("invalid_value:date_of_birth (client must be 18 or older)")
        return d.isoformat()
    if v and len(v) > 60:
        raise ValueError(f"invalid_value:{field}")
    if field == "gender":
        if v and v.lower() not in ("male", "female", "other"):
            raise ValueError("invalid_value:gender")
        return v.lower() if v else None
    return v or None


def _current(borrower: Borrower, field: str):
    cur = getattr(borrower, field, None)
    return cur.isoformat() if isinstance(cur, (date, datetime)) else cur


def validate_changes(borrower: Borrower, field_changes) -> dict:
    """Server-side validation of a proposed edit (F13). Unknown fields are
    refused, values are checked, ``old_value`` is taken from the record (never
    from the caller) and fields that would not actually change are dropped."""
    if not isinstance(field_changes, dict) or not field_changes:
        raise ValueError("no_changes")
    out = {}
    for raw_field, change in field_changes.items():
        field = _ALIASES.get(raw_field, raw_field)
        if field not in _APPLYABLE:
            raise ValueError(f"unknown_field:{raw_field}")
        new_val = change.get("new_value") if isinstance(change, dict) else change
        new_val = _clean_value(field, new_val)
        old_val = _current(borrower, field)
        if str(new_val or "") == str(old_val or ""):
            continue
        out[field] = {"old_value": old_val, "new_value": new_val}
    if not out:
        raise ValueError("no_changes")
    return out


def create_request(db: Session, *, tenant_id: int, client_id: int, requested_by: int,
                   field_changes: dict, edit_tier: str | None = None,
                   supporting_docs: list | None = None) -> ClientEditRequest:
    borrower = (db.query(Borrower)
                .filter(Borrower.id == client_id, Borrower.tenant_id == tenant_id).first())
    if not borrower:
        raise ValueError("client_not_found")
    changes = validate_changes(borrower, field_changes)
    if edit_tier not in (None, "", "primary", "secondary"):
        raise ValueError("invalid_tier")
    # F13: the tier is worked out from the fields. A caller may ask for MORE
    # scrutiny ("primary") but can never lower it.
    tier = infer_tier(changes)
    if edit_tier == "primary":
        tier = "primary"
    clash = (db.query(ClientEditRequest)
             .filter(ClientEditRequest.tenant_id == tenant_id,
                     ClientEditRequest.client_id == client_id,
                     ClientEditRequest.status == "pending").all())
    for other in clash:
        if set(other.field_changes or {}) & set(changes):
            raise ValueError("pending_request_exists")
    # Identity (primary-tier) edits need the number of approvers the DCP chose
    # (Validation Preferences, default 2); other edits need one. Kept on the
    # request so a later settings change never alters one already in progress.
    from app.services.kyc_decisions import settings as kyc_settings
    needed = kyc_settings(db, tenant_id).identity_edit_approvals if tier == "primary" else 1
    req = ClientEditRequest(
        tenant_id=tenant_id, client_id=client_id, edit_tier=tier,
        requested_by=requested_by, field_changes=changes,
        supporting_docs=supporting_docs, status="pending",
        approvals_required=2 if needed >= 2 else 1,
    )
    db.add(req)
    db.commit()
    return req


def approvals_needed(req: ClientEditRequest) -> int:
    if req.approvals_required:
        return int(req.approvals_required)
    return 2 if req.edit_tier == "primary" else 1     # requests raised before the setting


def next_stage(req: ClientEditRequest) -> str:
    """'final' when the next approval applies the change, else 'first'.
    Two-approval requests (identity edits, by default) need a first and a final
    approval by two different people; one-approval requests go straight to final."""
    if approvals_needed(req) >= 2 and not req.first_approved_by:
        return "first"
    return "final"


def _lock_request(db: Session, tenant_id: int, request_id: int) -> ClientEditRequest:
    req = (db.query(ClientEditRequest)
           .filter(ClientEditRequest.id == request_id,
                   ClientEditRequest.tenant_id == tenant_id)
           .with_for_update().first())
    if not req:
        raise ValueError("request_not_found")
    if req.status != "pending":
        raise ValueError("not_pending")
    return req


def approve(db: Session, *, tenant_id: int, request_id: int, approved_by: int) -> ClientEditRequest:
    req = _lock_request(db, tenant_id, request_id)
    if req.requested_by == approved_by:
        raise EditRefused("You cannot approve an edit you requested.")
    if approvals_needed(req) >= 2 and req.first_approved_by == approved_by:
        raise EditRefused("The final approval must be given by a different person "
                          "from the first approval.")
    now = datetime.now(timezone.utc)
    if next_stage(req) == "first":
        req.first_approved_by = approved_by
        req.first_approved_at = now
        db.commit()
        return req

    borrower = (db.query(Borrower).filter(Borrower.id == req.client_id,
                                          Borrower.tenant_id == tenant_id)
                .with_for_update().first())
    if not borrower:
        raise ValueError("client_not_found")
    changed = set()
    for field, change in (req.field_changes or {}).items():
        field = _ALIASES.get(field, field)
        if field not in _APPLYABLE:
            continue
        new_val = change.get("new_value") if isinstance(change, dict) else change
        new_val = _clean_value(field, new_val)          # re-check at apply time
        if field == "date_of_birth" and new_val:
            new_val = date.fromisoformat(new_val)
        setattr(borrower, field, new_val)
        changed.add(field)
    invalidate_checks(borrower, changed)
    req.status = "approved"
    req.approved_by = approved_by
    req.approved_at = now
    db.commit()
    return req


def invalidate_checks(borrower: Borrower, changed: set) -> None:
    """Evidence gathered for the old values does not carry over to new ones."""
    if "phone" in changed:
        borrower.mpesa_validated = False
        borrower.mpesa_validation_name = None
        borrower.mpesa_validation_method = None
        borrower.mpesa_validation_phone = None
        borrower.mpesa_validated_by = None
        if borrower.kyc_status in ("validated", "escalation"):
            borrower.kyc_status = "pending"
    if "alt_phone" in changed:
        borrower.alt_phone_validated = False
    if changed & IDENTITY_FIELDS:
        borrower.ekyc_status = None
        borrower.ekyc_reference = None
        borrower.ekyc_checked_at = None
        if borrower.kyc_status in ("validated", "escalation"):
            borrower.kyc_status = "pending"
    if "date_of_birth" in changed:
        borrower.date_of_birth_verified = False
        borrower.age_verified_at = None


def reject(db: Session, *, tenant_id: int, request_id: int, approved_by: int,
           reason: str) -> ClientEditRequest:
    req = _lock_request(db, tenant_id, request_id)
    if req.requested_by == approved_by:
        raise EditRefused("You cannot decide an edit you requested.")
    req.status = "rejected"
    req.approved_by = approved_by
    req.rejected_at = datetime.now(timezone.utc)
    req.rejection_reason = reason
    db.commit()
    return req
