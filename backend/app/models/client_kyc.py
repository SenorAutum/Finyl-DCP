"""
Client KYC satellite tables: mobile wallets, next of kin and uploaded documents.

All three are tenant-scoped (denormalised `tenant_id` so every query can be
filtered by tenant without a join) and cascade-delete with the client.
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, String,
                        Text)
from sqlalchemy.orm import relationship

from app.core.database import Base


class ClientMobileWallet(Base):
    """A mobile-money wallet held by the client (M-Pesa, Airtel Money, T-Kash, Equitel)."""
    __tablename__ = "client_mobile_wallets"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    mobile_number = Column(String(20))
    wallet_number = Column(String(30))
    operator = Column(String(30))       # see models.lending.WALLET_OPERATORS
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Borrower", back_populates="wallets")


class ClientNextOfKin(Base):
    """Guarantor / emergency contact captured at onboarding."""
    __tablename__ = "client_next_of_kin"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    full_name = Column(String(120))
    relationship_type = Column("relationship", String(30))  # column name kept short/readable
    mobile_number = Column(String(20))
    national_id = Column(String(20))
    address = Column(String(160))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Borrower", back_populates="next_of_kin")


class ClientDocument(Base):
    """Any uploaded KYC document. Binary content lives on disk under
    settings.STORAGE_DIR (swap for S3/GCS by changing app.services.storage)."""
    __tablename__ = "client_documents"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    file_name = Column(String(200))         # stored (sanitised, unique) name
    original_name = Column(String(200))     # name as uploaded
    mime_type = Column(String(120))
    size_bytes = Column(Integer, default=0)
    doc_type = Column(String(40), default="other")   # see models.lending.DOC_TYPES
    storage_path = Column(Text)             # absolute/relative path within STORAGE_DIR
    ocr_applied = Column(Boolean, default=False)
    ocr_text = Column(Text)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    uploaded_by = Column(String(120))

    client = relationship("Borrower", back_populates="documents")
