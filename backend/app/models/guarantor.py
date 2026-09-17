"""Guarantor models (migration 017).

Guarantors are tenant-scoped and cascade-delete with their client. National ID is
sensitive PII: stored encrypted (EncryptedText) with a blind-index hash column for
lookups, mirroring the Borrower pattern. DB enums are represented as String on the
ORM side — the values match the native enum labels, so both AUTO_CREATE_TABLES
(dev) and the SQL migrations (prod) work interchangeably.
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, Numeric,
                        String, Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.crypto import EncryptedText
from app.core.database import Base

GUARANTOR_TYPES = ["personal", "business"]
LOAN_CATEGORIES = ["personal", "business", "secured", "unsecured"]
GUARANTOR_KYC_STATUSES = ["pending", "validated", "failed"]


class Guarantor(Base):
    __tablename__ = "guarantors"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    full_name = Column(String(300), nullable=False)
    national_id = Column(EncryptedText)              # application-layer Fernet
    national_id_hash = Column(String(200), index=True)   # blind index for lookups
    phone = Column(String(20))
    relationship_type = Column("relationship", String(100))
    address = Column(Text)
    occupation = Column(String(200))
    monthly_income = Column(Numeric(14, 2))
    guarantor_type = Column(String(20), nullable=False, default="personal")
    mpesa_validated = Column(Boolean, nullable=False, default=False)
    mpesa_validation_name = Column(String(300))
    mpesa_validated_at = Column(DateTime(timezone=True))
    kyc_status = Column(String(50), nullable=False, default="pending")
    active = Column(Boolean, nullable=False, default=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    client = relationship("Borrower")
    business = relationship("GuarantorBusiness", back_populates="guarantor",
                            cascade="all, delete-orphan")
    documents = relationship("GuarantorDocument", back_populates="guarantor",
                             cascade="all, delete-orphan")


class GuarantorBusiness(Base):
    __tablename__ = "guarantor_business"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    guarantor_id = Column(Integer, ForeignKey("guarantors.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    business_name = Column(String(300), nullable=False)
    business_reg_no = Column(String(100))
    operational_age_months = Column(Integer)
    sector = Column(String(100))
    directors = Column(JSONB)
    monthly_turnover = Column(Numeric(14, 2))
    business_address = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    guarantor = relationship("Guarantor", back_populates="business")


class GuarantorDocument(Base):
    __tablename__ = "guarantor_documents"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    guarantor_id = Column(Integer, ForeignKey("guarantors.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    doc_type = Column(String(100), nullable=False)
    file_name = Column(String(500), nullable=False)
    original_name = Column(String(500))
    mime_type = Column(String(100))
    size_bytes = Column(Integer)
    storage_path = Column(Text, nullable=False)
    ocr_applied = Column(Boolean, nullable=False, default=False)
    ocr_text = Column(EncryptedText)
    ocr_field_mapping = Column(JSONB)
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    uploaded_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    guarantor = relationship("Guarantor", back_populates="documents")
