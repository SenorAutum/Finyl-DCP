"""WP13 — the Finyl command centre (Finyl's own side of the platform, migration 042).

Everything here belongs to Finyl as the SaaS provider, not to a DCP:
  * who on Finyl's side may see what (platform users, their switches and the
    DCPs assigned to them);
  * Finyl's charges to DCPs (pricing, invoices, payments);
  * Finyl's loans to DCPs (facilities, drawdowns, repayments);
  * Finyl's sales pipeline (potential and onboarded DCPs);
  * alerts when something fails, and who is told;
  * a nightly snapshot of each DCP's figures, for trends.
"""
from datetime import datetime

from sqlalchemy import (Boolean, Index, text, Column, Date, ForeignKey, Integer, Numeric, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.core.timeutil import UTCDateTime


# --------------------------------------------------------------------------- #
# Who on Finyl's side sees what
# --------------------------------------------------------------------------- #
class PlatformUserAccess(Base):
    """Switches for a Finyl-side user (role 'platform_user'): which parts of the
    command centre they may use and which DCPs they may see."""
    __tablename__ = "platform_user_access"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    preset = Column(String(30), default="custom")        # director | finance | operations | support | custom
    title = Column(String(80))                           # shown on their profile, e.g. "Partner, Acme Capital"
    permissions = Column(JSONB, default=list)            # platform.* keys switched on
    all_tenants = Column(Boolean, default=False)         # True = every DCP, now and future
    tenant_ids = Column(JSONB, default=list)             # the DCPs assigned when all_tenants is False
    updated_by_user_id = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(UTCDateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --------------------------------------------------------------------------- #
# Finyl's charges to DCPs
# --------------------------------------------------------------------------- #
class PlatformPricing(Base):
    """The standard price list (one row). Each DCP can have its own prices."""
    __tablename__ = "platform_pricing"

    id = Column(Integer, primary_key=True)
    onboarding_fee = Column(Numeric(14, 2), nullable=False, default=50000)
    subscription_rate_pct = Column(Numeric(6, 3), nullable=False, default=1)      # % of outstanding loan book
    subscription_minimum = Column(Numeric(14, 2), nullable=False, default=30000)  # per month
    branch_fee = Column(Numeric(14, 2), nullable=False, default=20000)            # per new branch
    branches_included = Column(Integer, nullable=False, default=1)                # covered by onboarding
    bill_sms = Column(Boolean, nullable=False, default=True)                      # add SMS sent to the invoice
    payment_terms_days = Column(Integer, nullable=False, default=14)
    invoice_day = Column(Integer, nullable=False, default=1)                      # day of month invoices go out
    updated_by_user_id = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(UTCDateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TenantBilling(Base):
    """One DCP's billing set-up. Blank prices use the standard price list."""
    __tablename__ = "tenant_billing"

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    billing_active = Column(Boolean, default=False)       # invoices are produced only when on
    billing_start = Column(Date)                          # first month billed
    onboarding_fee = Column(Numeric(14, 2))
    subscription_rate_pct = Column(Numeric(6, 3))
    subscription_minimum = Column(Numeric(14, 2))
    branch_fee = Column(Numeric(14, 2))
    branches_included = Column(Integer)
    onboarding_invoiced = Column(Boolean, default=False)
    branches_billed = Column(Integer, default=0)          # extra branches already charged
    billing_email = Column(String(120))
    notes = Column(Text)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(UTCDateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PlatformInvoice(Base):
    __tablename__ = "platform_invoices"
    # One live invoice per DCP and month; a cancelled (void) one frees the month.
    __table_args__ = (Index("uq_platform_invoice_period", "tenant_id", "period_start", unique=True,
                            postgresql_where=text("status <> 'void'"),
                            sqlite_where=text("status <> 'void'")),)

    id = Column(Integer, primary_key=True)
    number = Column(String(30), nullable=False, unique=True)     # INV-2026-11-0001
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    issued_at = Column(UTCDateTime, default=datetime.utcnow)
    due_date = Column(Date)
    status = Column(String(15), default="issued", index=True)   # issued | part_paid | paid | void
    total = Column(Numeric(14, 2), nullable=False, default=0)
    paid = Column(Numeric(14, 2), nullable=False, default=0)
    outstanding_book = Column(Numeric(16, 2))                    # the DCP's loan book the fee was based on
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    void_reason = Column(Text)


class PlatformInvoiceLine(Base):
    __tablename__ = "platform_invoice_lines"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("platform_invoices.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    kind = Column(String(20), nullable=False)       # onboarding | subscription | branch | sms | other
    description = Column(String(300), nullable=False)
    quantity = Column(Numeric(14, 3), default=1)
    unit_price = Column(Numeric(14, 4), default=0)
    amount = Column(Numeric(14, 2), nullable=False)


class PlatformPayment(Base):
    """Money a DCP paid Finyl: for an invoice or a loan facility."""
    __tablename__ = "platform_payments"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    invoice_id = Column(Integer, ForeignKey("platform_invoices.id"), index=True)
    facility_id = Column(Integer, ForeignKey("platform_facilities.id"), index=True)
    amount = Column(Numeric(14, 2), nullable=False)
    reference = Column(String(40), nullable=False, unique=True)   # M-Pesa receipt or bank reference
    channel = Column(String(20), default="mpesa_paybill")
    paid_at = Column(UTCDateTime, nullable=False)
    principal = Column(Numeric(14, 2), default=0)                 # facility payments only
    interest = Column(Numeric(14, 2), default=0)
    note = Column(Text)
    recorded_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class PlatformUnmatchedPayment(Base):
    """A payment into Finyl's own paybill that names no invoice or facility."""
    __tablename__ = "platform_unmatched_payments"

    id = Column(Integer, primary_key=True)
    reference = Column(String(40), nullable=False, unique=True)
    amount = Column(Numeric(14, 2), nullable=False)
    account_entered = Column(String(60))
    payer = Column(String(120))
    paid_at = Column(UTCDateTime)
    status = Column(String(15), default="open")          # open | allocated | refunded
    resolved_note = Column(Text)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- #
# Finyl's loans to DCPs
# --------------------------------------------------------------------------- #
class PlatformFacility(Base):
    __tablename__ = "platform_facilities"

    id = Column(Integer, primary_key=True)
    number = Column(String(20), nullable=False, unique=True)     # FAC-0001 (the paybill account)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    limit_amount = Column(Numeric(16, 2), nullable=False)
    interest_rate_pct = Column(Numeric(6, 3), nullable=False)     # per month
    interest_method = Column(String(10), nullable=False, default="reducing")   # reducing | flat
    term_months = Column(Integer, nullable=False, default=12)
    start_date = Column(Date, nullable=False)
    status = Column(String(15), default="active", index=True)    # active | suspended | closed
    purpose = Column(String(200))
    notes = Column(Text)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class PlatformDrawdown(Base):
    __tablename__ = "platform_drawdowns"

    id = Column(Integer, primary_key=True)
    facility_id = Column(Integer, ForeignKey("platform_facilities.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    amount = Column(Numeric(16, 2), nullable=False)
    disbursed_on = Column(Date, nullable=False)
    reference = Column(String(40))
    note = Column(Text)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- #
# Finyl's sales pipeline
# --------------------------------------------------------------------------- #
class PlatformLead(Base):
    __tablename__ = "platform_leads"

    id = Column(Integer, primary_key=True)
    company_name = Column(String(160), nullable=False)
    contact_name = Column(String(120))
    contact_phone = Column(String(20))
    contact_email = Column(String(120))
    stage = Column(String(15), nullable=False, default="lead", index=True)
    expected_loan_book = Column(Numeric(16, 2))
    expected_branches = Column(Integer)
    source = Column(String(60))
    next_step = Column(String(200))
    next_step_date = Column(Date)
    owner_user_id = Column(Integer, ForeignKey("users.id"))
    tenant_id = Column(Integer, ForeignKey("tenants.id"))          # once it became a DCP on Finyl
    lost_reason = Column(String(200))
    notes = Column(Text)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)
    updated_at = Column(UTCDateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PlatformLeadEvent(Base):
    __tablename__ = "platform_lead_events"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("platform_leads.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    from_stage = Column(String(15))
    to_stage = Column(String(15))
    note = Column(Text)
    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #
class PlatformAlert(Base):
    __tablename__ = "platform_alerts"

    id = Column(Integer, primary_key=True)
    # No foreign key on purpose: recording an alert must never wait on (or fail
    # because of) the work that went wrong. Null = the platform itself.
    tenant_id = Column(Integer, index=True)
    kind = Column(String(40), nullable=False, index=True)
    severity = Column(String(10), nullable=False, default="warning")    # info | warning | critical
    title = Column(String(200), nullable=False)
    detail = Column(Text)
    dedupe_key = Column(String(200), nullable=False, index=True)
    count = Column(Integer, nullable=False, default=1)
    status = Column(String(15), nullable=False, default="open", index=True)  # open | acknowledged | resolved
    first_seen = Column(UTCDateTime, default=datetime.utcnow)
    last_seen = Column(UTCDateTime, default=datetime.utcnow, index=True)
    acknowledged_by_user_id = Column(Integer)
    resolved_at = Column(UTCDateTime)
    resolved_note = Column(Text)
    notified_at = Column(UTCDateTime)


class PlatformAlertRecipient(Base):
    __tablename__ = "platform_alert_recipients"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(120))
    phone = Column(String(20))
    min_severity = Column(String(10), nullable=False, default="warning")
    kinds = Column(JSONB, default=list)          # empty = every kind
    tenant_ids = Column(JSONB, default=list)     # empty = every DCP (and platform-wide alerts)
    weekly_summary = Column(Boolean, nullable=False, default=True)   # Monday summary email
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- #
# Nightly figures per DCP
# --------------------------------------------------------------------------- #
class PlatformTenantDaily(Base):
    __tablename__ = "platform_tenant_daily"
    __table_args__ = (UniqueConstraint("tenant_id", "day", name="uq_platform_tenant_day"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    day = Column(Date, nullable=False, index=True)
    outstanding = Column(Numeric(16, 2), default=0)
    par30 = Column(Numeric(16, 2), default=0)
    disbursed = Column(Numeric(16, 2), default=0)
    collected = Column(Numeric(16, 2), default=0)
    clients = Column(Integer, default=0)
    active_loans = Column(Integer, default=0)
    sms_sent = Column(Integer, default=0)
    sms_revenue = Column(Numeric(14, 2), default=0)
    health_score = Column(Integer)
    created_at = Column(UTCDateTime, default=datetime.utcnow)
