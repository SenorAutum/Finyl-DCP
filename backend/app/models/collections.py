"""Collections models (migration 026): M-Pesa Ratiba consents, bank-statement
analysis, promise-to-pay tracking and per-officer collection efficiency."""
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Integer,
                        Numeric, String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base

PTP_CONTACT_METHODS = ["call", "site_visit"]
PTP_STATUSES = ["pending", "honored", "broken", "partial", "rescheduled"]
RATIBA_STATUSES = ["active", "cancelled", "pending", "failed"]


class MpesaRatibaConsent(Base):
    __tablename__ = "mpesa_ratiba_consents"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    phone = Column(String(20), nullable=False)
    consent_given = Column(Boolean, nullable=False, default=False)
    consent_at = Column(DateTime(timezone=True))
    consent_ip = Column(String(45))
    ratiba_ref = Column(String(200))
    ratiba_status = Column(String(50))
    deduction_amount = Column(Numeric(14, 2))
    deduction_day = Column(Integer)
    raw_response = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class BankStatement(Base):
    __tablename__ = "bank_statements"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    bank_name = Column(String(100), nullable=False)
    account_number_hash = Column(String(200))
    period_start = Column(Date)
    period_end = Column(Date)
    months_covered = Column(Integer)
    transactions_count = Column(Integer)
    avg_monthly_credit = Column(Numeric(14, 2))
    avg_monthly_debit = Column(Numeric(14, 2))
    net_monthly_cashflow = Column(Numeric(14, 2))
    affordability_score = Column(Numeric(5, 2))
    comfortable_installment = Column(Numeric(14, 2))
    summary = Column(JSONB)
    detected_lenders = Column(JSONB)
    tampering_suspected = Column(Boolean, nullable=False, default=False)
    integrity_flags = Column(JSONB)
    source_filename = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    client = relationship("Borrower")


class PromiseToPay(Base):
    __tablename__ = "promise_to_pay"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    logged_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    ptp_date = Column(Date, nullable=False, index=True)
    amount = Column(Numeric(14, 2), nullable=False)
    contact_method = Column(String(30), nullable=False, default="call")
    status = Column(String(30), nullable=False, default="pending", index=True)
    reminder_sent_at = Column(JSONB)
    honored_at = Column(DateTime(timezone=True))
    honored_amount = Column(Numeric(14, 2))
    broken_at = Column(DateTime(timezone=True))
    rescheduled_ptp_id = Column(Integer, ForeignKey("promise_to_pay.id"))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    client = relationship("Borrower")


class CollectionEfficiency(Base):
    __tablename__ = "collection_efficiency"
    __table_args__ = (UniqueConstraint("tenant_id", "officer_user_id", "period_month",
                                       name="uq_collect_eff_officer_month"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    officer_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    period_month = Column(Date, nullable=False, index=True)
    ptps_logged = Column(Integer, nullable=False, default=0)
    ptps_honored = Column(Integer, nullable=False, default=0)
    ptps_broken = Column(Integer, nullable=False, default=0)
    ptps_partial = Column(Integer, nullable=False, default=0)
    amount_promised = Column(Numeric(14, 2), nullable=False, default=0)
    amount_collected = Column(Numeric(14, 2), nullable=False, default=0)
    efficiency_pct = Column(Numeric(5, 2))
    calls_made = Column(Integer, nullable=False, default=0)
    visits_made = Column(Integer, nullable=False, default=0)
    computed_at = Column(DateTime(timezone=True), default=datetime.utcnow)
