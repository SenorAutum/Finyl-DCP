"""Tenant KYC validation preferences (Phase 2)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission, get_tenant_id, write_audit
from app.models import TenantValidationPrefs
from app.schemas import ValidationPrefsUpdate
from app.services import kyc_decisions

router = APIRouter(prefix="/api/v1/settings/validation-prefs", tags=["validation-prefs"])

_FIELDS = ["age_check_mandatory", "mpesa_validation_mandatory", "face_validation_mandatory",
           "alt_phone_validation_mandatory", "crb_check_mandatory",
           "guarantor_validation_mandatory", "ocr_mandatory", "ekyc_mandatory",
           "custom_rules",
           # WP6: how strict the process is
           "mpesa_confirmation_mode", "identity_edit_approvals", "allow_review_override",
           "crb_max_age_days", "min_credit_score"]


def _dict(p: TenantValidationPrefs) -> dict:
    return {f: getattr(p, f) for f in _FIELDS} | {
        "updated_at": p.updated_at,
        # WP6 (F18): the custom rules the decision engine actually enforces.
        "supported_custom_rules": kyc_decisions.SUPPORTED_CUSTOM_RULES}


def _get_or_create(db: Session, tenant_id: int) -> TenantValidationPrefs:
    p = (db.query(TenantValidationPrefs)
         .filter(TenantValidationPrefs.tenant_id == tenant_id).first())
    if not p:
        p = TenantValidationPrefs(tenant_id=tenant_id)
        db.add(p)
        db.commit()
        db.refresh(p)
    return p


@router.get("")
def get_prefs(tenant_id: int = Depends(get_tenant_id),
              user=Depends(require_permission("security.manage")),
              db: Session = Depends(get_db)):
    return _dict(_get_or_create(db, tenant_id))


@router.put("")
def update_prefs(body: ValidationPrefsUpdate,
                 tenant_id: int = Depends(get_tenant_id),
                 user=Depends(require_permission("security.manage")),
                 db: Session = Depends(get_db), request: Request = None):
    problems = kyc_decisions.validate_custom_rules(body.custom_rules)
    if problems:
        raise HTTPException(422, "Custom rules: " + "; ".join(problems))
    if (body.mpesa_confirmation_mode is not None
            and body.mpesa_confirmation_mode not in kyc_decisions.MPESA_CONFIRMATION_MODES):
        raise HTTPException(422, "mpesa_confirmation_mode must be 'second_person' or 'single_person'")
    if body.identity_edit_approvals is not None and body.identity_edit_approvals not in (1, 2):
        raise HTTPException(422, "identity_edit_approvals must be 1 or 2")
    if body.crb_max_age_days is not None and not 1 <= body.crb_max_age_days <= 3650:
        raise HTTPException(422, "crb_max_age_days must be between 1 and 3650")
    if body.min_credit_score is not None and not -1 <= body.min_credit_score <= 999:
        raise HTTPException(422, "min_credit_score must be between 0 and 999 (-1 to clear it)")
    p = _get_or_create(db, tenant_id)
    for f in _FIELDS:
        val = getattr(body, f, None)
        if val is not None:
            setattr(p, f, val)
    if body.min_credit_score == -1:
        p.min_credit_score = None
    p.updated_at = datetime.now(timezone.utc)
    p.updated_by = user.id
    write_audit(db, tenant_id=tenant_id, user=user, action="settings.validation_prefs.update",
                entity_type="tenant", entity_id=tenant_id,
                details={f: getattr(body, f) for f in _FIELDS
                         if getattr(body, f, None) is not None}, request=request)
    db.commit()
    return _dict(p)
