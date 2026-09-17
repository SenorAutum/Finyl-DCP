"""Client-edit-request + active-loan-lock models (migration 021).

When a loan is active the client record is edit-locked. Edits are proposed as
tiered requests (primary = high-sensitivity fields needing approval; secondary =
lower-sensitivity) and applied only once approved.
"""
from datetime import datetime

from sqlalchemy import (Column, DateTime, ForeignKey, Integer, Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base

EDIT_TIERS = ["primary", "secondary"]
EDIT_REQUEST_STATUSES = ["pending", "approved", "rejected", "cancelled"]


class ClientEditRequest(Base):
    __tablename__ = "client_edit_requests"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    edit_tier = Column(Text, nullable=False)            # primary | secondary
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"))
    requested_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    approved_at = Column(DateTime(timezone=True))
    rejected_at = Column(DateTime(timezone=True))
    field_changes = Column(JSONB, nullable=False)       # {field: {old_value, new_value}}
    supporting_docs = Column(JSONB)
    status = Column(Text, nullable=False, default="pending", index=True)
    rejection_reason = Column(Text)

    client = relationship("Borrower")


class LoanActiveLockLog(Base):
    __tablename__ = "loan_active_lock_log"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    locked_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    locked_by = Column(Integer, ForeignKey("users.id"))
    unlock_at = Column(DateTime(timezone=True))
    unlock_reason = Column(Text)
