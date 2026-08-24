"""
Additive feature models (migration 014). All tenant-scoped, all backward
compatible — none touch the existing money-movement, approval or lending tables.

  * EclProvisionConfig — per-tenant IFRS 9 ECL stage rates (dashboard analytics).
  * SuspenseEntry      — unallocated / overpayment / closed-loan C2B money.
  * SmsOptOut          — per-tenant SMS opt-out register (non-transactional SMS).
  * KycConsent         — per-borrower KYC / data-processing consent capture.
  * ChartOfAccount     — per-tenant Chart of Accounts for the GL export.
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, JSON,
                        Numeric, String)

from app.core.database import Base

# --- Default IFRS 9 ECL stage rates (used when a tenant has no config row) ---
# Expressed as fractions (0.01 == 1%). Stage buckets by days-past-due:
#   Stage 1: 0-30 dpd, Stage 2: 31-90 dpd, Stage 3: 90+ dpd or defaulted.
ECL_DEFAULT_STAGE1_RATE = 0.0100
ECL_DEFAULT_STAGE2_RATE = 0.2000
ECL_DEFAULT_STAGE3_RATE = 0.6000

SUSPENSE_SOURCES = ["c2b", "manual"]
SUSPENSE_REASONS = ["unmatched", "overpayment", "closed_loan"]
SUSPENSE_STATUSES = ["open", "allocated", "refunded"]

OPT_OUT_SOURCES = ["keyword", "manual", "api"]

COA_TYPES = ["asset", "liability", "income", "expense", "equity"]


class EclProvisionConfig(Base):
    """Per-tenant IFRS 9 Expected-Credit-Loss stage provisioning rates."""
    __tablename__ = "ecl_provision_config"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    stage1_rate = Column(Numeric(6, 4), nullable=False, default=ECL_DEFAULT_STAGE1_RATE)
    stage2_rate = Column(Numeric(6, 4), nullable=False, default=ECL_DEFAULT_STAGE2_RATE)
    stage3_rate = Column(Numeric(6, 4), nullable=False, default=ECL_DEFAULT_STAGE3_RATE)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SuspenseEntry(Base):
    """Money received via C2B (or captured manually) that could not be applied
    cleanly to a loan: unmatched account, overpayment excess, or a payment to a
    non-collectible (closed/paid/rejected) loan. Resolved by allocate/refund."""
    __tablename__ = "suspense_entries"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    source = Column(String(10), nullable=False, default="c2b")     # c2b | manual
    mpesa_ref = Column(String(40))
    phone = Column(String(20))
    amount = Column(Numeric(12, 2), nullable=False)
    reason = Column(String(20), nullable=False)                    # unmatched | overpayment | closed_loan
    status = Column(String(12), nullable=False, default="open")    # open | allocated | refunded
    matched_loan_id = Column(Integer, ForeignKey("loans.id"))
    raw_payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"))


class SmsOptOut(Base):
    """A phone number that has opted out of NON-transactional SMS for a tenant.

    Enforced in app/services/sms.py before dispatching suppressible events
    (reminders / dunning / marketing). Critical transactional messages
    (disbursement / receipt / qualification) are NEVER suppressed."""
    __tablename__ = "sms_opt_outs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    phone = Column(String(20), nullable=False)
    opted_out_at = Column(DateTime, default=datetime.utcnow)
    source = Column(String(10), nullable=False, default="manual")  # keyword | manual | api
    active = Column(Boolean, nullable=False, default=True)


class KycConsent(Base):
    """Versioned per-borrower KYC consent record. Data-processing consent is
    mandatory when a consent payload is submitted; legacy borrowers with no
    consent row are grandfathered (existing create flow is not hard-blocked)."""
    __tablename__ = "kyc_consents"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    borrower_id = Column(Integer, ForeignKey("borrowers.id"), nullable=False, index=True)
    consent_data_processing = Column(Boolean, nullable=False, default=False)
    consent_credit_check = Column(Boolean, nullable=False, default=False)
    consent_marketing = Column(Boolean, nullable=False, default=False)
    consent_version = Column(String(20))
    consented_at = Column(DateTime, default=datetime.utcnow)
    ip_address = Column(String(45))


class ChartOfAccount(Base):
    """A single GL account in a tenant's Chart of Accounts (double-entry export)."""
    __tablename__ = "chart_of_accounts"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    code = Column(String(20), nullable=False)
    name = Column(String(120), nullable=False)
    type = Column(String(12), nullable=False)   # asset | liability | income | expense | equity
    active = Column(Boolean, nullable=False, default=True)
