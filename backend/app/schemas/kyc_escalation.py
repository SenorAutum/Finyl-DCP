"""Pydantic schemas for KYC mismatch escalations + validation prefs (Phase 2)."""
from typing import Optional

from pydantic import BaseModel


class KycEscalationCreate(BaseModel):
    client_id: int
    mismatch_type: str
    mismatch_detail: Optional[str] = None
    escalated_to: Optional[int] = None


class KycEscalationUpdate(BaseModel):
    status: Optional[str] = None          # resolved | overridden
    resolution_note: Optional[str] = None
    escalated_to: Optional[int] = None


class ValidationPrefsUpdate(BaseModel):
    age_check_mandatory: Optional[bool] = None
    mpesa_validation_mandatory: Optional[bool] = None
    face_validation_mandatory: Optional[bool] = None
    alt_phone_validation_mandatory: Optional[bool] = None
    crb_check_mandatory: Optional[bool] = None
    guarantor_validation_mandatory: Optional[bool] = None
    ocr_mandatory: Optional[bool] = None
    ekyc_mandatory: Optional[bool] = None
    mpesa_confirmation_mode: Optional[str] = None     # second_person | single_person
    identity_edit_approvals: Optional[int] = None     # 1 | 2
    allow_review_override: Optional[bool] = None
    crb_max_age_days: Optional[int] = None
    min_credit_score: Optional[int] = None            # -1 clears it
    custom_rules: Optional[dict] = None
