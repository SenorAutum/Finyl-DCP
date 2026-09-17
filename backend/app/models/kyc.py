"""KYC enhancement + face-validation models (migrations 018, 020)."""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, Numeric,
                        String, Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base

KYC_ESCALATION_STATUSES = ["open", "resolved", "overridden"]
FACE_VALIDATION_RESULTS = ["pass", "fail", "manual_review"]
MISMATCH_TYPES = ["name_mismatch", "dob_mismatch", "phone_mismatch",
                  "id_mismatch", "face_mismatch", "other"]


class KycMismatchEscalation(Base):
    __tablename__ = "kyc_mismatch_escalations"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    mismatch_type = Column(String(100), nullable=False)
    mismatch_detail = Column(Text)
    logged_by = Column(Integer, ForeignKey("users.id"))
    escalated_to = Column(Integer, ForeignKey("users.id"))
    status = Column(String(30), nullable=False, default="open", index=True)
    resolution_note = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    resolved_at = Column(DateTime(timezone=True))

    client = relationship("Borrower")


class TenantValidationPrefs(Base):
    __tablename__ = "tenant_validation_prefs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, unique=True, index=True)
    age_check_mandatory = Column(Boolean, nullable=False, default=True)
    mpesa_validation_mandatory = Column(Boolean, nullable=False, default=True)
    face_validation_mandatory = Column(Boolean, nullable=False, default=False)
    alt_phone_validation_mandatory = Column(Boolean, nullable=False, default=False)
    crb_check_mandatory = Column(Boolean, nullable=False, default=False)
    guarantor_validation_mandatory = Column(Boolean, nullable=False, default=False)
    ocr_mandatory = Column(Boolean, nullable=False, default=True)
    custom_rules = Column(JSONB)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_by = Column(Integer, ForeignKey("users.id"))


class FaceValidationLog(Base):
    __tablename__ = "face_validation_logs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    validation_provider = Column(String(100), nullable=False, default="smile_identity")
    match_score = Column(Numeric(5, 4))
    result = Column(String(30), nullable=False, default="manual_review")
    liveness_pass = Column(Boolean)
    id_image_path = Column(Text)
    selfie_image_path = Column(Text)
    smile_job_id = Column(String(200))
    raw_response = Column(JSONB)
    validated_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    validated_by = Column(Integer, ForeignKey("users.id"))

    client = relationship("Borrower")
