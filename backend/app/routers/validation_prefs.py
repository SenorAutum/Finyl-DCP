"""Tenant KYC validation preferences (Phase 2)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission, get_tenant_id, write_audit
from app.models import TenantValidationPrefs
from app.schemas import ValidationPrefsUpdate

router = APIRouter(prefix="/api/v1/settings/validation-prefs", tags=["validation-prefs"])

_FIELDS = ["age_check_mandatory", "mpesa_validation_mandatory", "face_validation_mandatory",
           "alt_phone_validation_mandatory", "crb_check_mandatory",
           "guarantor_validation_mandatory", "ocr_mandatory", "custom_rules"]


def _dict(p: TenantValidationPrefs) -> dict:
    return {f: getattr(p, f) for f in _FIELDS} | {"updated_at": p.updated_at}


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
    p = _get_or_create(db, tenant_id)
    for f in _FIELDS:
        val = getattr(body, f, None)
        if val is not None:
            setattr(p, f, val)
    p.updated_at = datetime.now(timezone.utc)
    p.updated_by = user.id
    write_audit(db, tenant_id=tenant_id, user=user, action="settings.validation_prefs.update",
                entity_type="tenant", entity_id=tenant_id, request=request)
    db.commit()
    return _dict(p)
