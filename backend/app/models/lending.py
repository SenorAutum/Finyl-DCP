"""Core lending models: borrowers, loans, repayments, payment transactions."""
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey,
                        Integer, Numeric, String, Text, JSON, UniqueConstraint)
from sqlalchemy.orm import relationship

from app.core.database import Base

LOAN_STATUSES = ["pending", "underwriting", "approved", "active", "paid", "overdue", "defaulted", "rejected"]

# Reference lists surfaced to the UI (kept as text columns rather than DB enums so
# a tenant can extend them without a migration).
WALLET_OPERATORS = ["M-Pesa", "Airtel Money", "T-Kash", "Equitel"]
NEXT_OF_KIN_RELATIONSHIPS = ["Spouse", "Parent", "Sibling", "Child", "Guardian", "Other"]
DOC_TYPES = ["national_id_front", "national_id_back", "passport", "kra_pin",
             "business_permit", "payslip", "bank_statement", "other"]


class Borrower(Base):
    """The client record.

    NOTE: the table name stays `borrowers` on purpose — existing analytics views,
    joins and foreign keys across the platform reference it. Everything the API
    and UI expose is named "client".
    """
    __tablename__ = "borrowers"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    first_name = Column(String(60), nullable=False)
    middle_name = Column(String(60))
    last_name = Column(String(60), nullable=False)
    national_id = Column(String(20), nullable=False)
    phone = Column(String(20), nullable=False)
    gender = Column(String(10))
    date_of_birth = Column(Date)
    region_id = Column(Integer, ForeignKey("regions.id"))
    branch_id = Column(Integer, ForeignKey("branches.id"))
    business_sector = Column(String(60))          # e.g. retail, agriculture, boda_boda
    baseline_monthly_sales = Column(Numeric(12, 2), default=0)
    baseline_employees = Column(Integer, default=0)
    kyc_status = Column(String(20), default="draft")   # draft | validated | failed
    credit_score = Column(Integer, default=0)          # 0-900 CRB-style score
    created_at = Column(DateTime, default=datetime.utcnow)

    # --- KYC / National ID details (all additive & nullable) -------------------
    serial_number = Column(String(30))          # ID booklet serial (OCR: "SERIAL NUMBER")
    district_of_birth = Column(String(60))
    place_of_issue = Column(String(60))
    date_of_issue = Column(Date)
    district = Column(String(60))
    division = Column(String(60))
    location = Column(String(60))
    sub_location = Column(String(60))
    current_credit_rating = Column(String(20))  # free text e.g. "A", "B+", "CRB clear"
    is_active = Column(Boolean, default=True)
    onboarded_by = Column(String(120))          # staff/user name that captured the record
    officer_staff_id = Column(Integer, ForeignKey("staff.id"))  # owning relationship officer (portfolio scope)
    approved_by_user_id = Column(Integer, ForeignKey("users.id"))
    profile_status = Column(String(20), default="approved")  # draft | pending_approval | approved | rejected

    # --- M-Pesa name-lookup validation ---------------------------------------
    mpesa_validated = Column(Boolean, default=False)
    mpesa_validation_name = Column(String(120))
    mpesa_validated_at = Column(DateTime)

    # --- External eKYC provider verification ---------------------------------
    ekyc_status = Column(String(20))            # verified | not_verified | error | pending
    ekyc_reference = Column(String(60))
    ekyc_checked_at = Column(DateTime)

    loans = relationship("Loan", back_populates="borrower")
    wallets = relationship("ClientMobileWallet", back_populates="client",
                           cascade="all, delete-orphan")
    next_of_kin = relationship("ClientNextOfKin", back_populates="client",
                               cascade="all, delete-orphan")
    documents = relationship("ClientDocument", back_populates="client",
                             cascade="all, delete-orphan")
    approved_by = relationship("User", foreign_keys=[approved_by_user_id])

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)


class Loan(Base):
    __tablename__ = "loans"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    account_number = Column(String(40), nullable=False, index=True)  # e.g. FL/FY2026/33/38
    borrower_id = Column(Integer, ForeignKey("borrowers.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.id"))          # loan officer
    branch_id = Column(Integer, ForeignKey("branches.id"))
    principal = Column(Numeric(12, 2), nullable=False)
    interest_rate = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    application_date = Column(Date)
    approval_date = Column(Date)
    disbursement_date = Column(Date)
    due_date = Column(Date)
    outstanding_balance = Column(Numeric(12, 2), default=0)
    loan_cycle_number = Column(Integer, default=1)   # borrower's Nth loan — drives impact survey gate
    created_at = Column(DateTime, default=datetime.utcnow)

    # --- RBAC approval / maker-checker / escalation (additive & nullable) ----
    approved_by_user_id = Column(Integer, ForeignKey("users.id"))
    escalation_level = Column(String(10))    # null | region | hq  (pending higher-tier approval)
    decision_note = Column(Text)
    disbursed_by_user_id = Column(Integer, ForeignKey("users.id"))

    borrower = relationship("Borrower", back_populates="loans")
    product = relationship("Product")
    staff = relationship("Staff")
    repayments = relationship("Repayment", back_populates="loan")

    @property
    def total_due(self):
        """Flat interest total due (principal + interest)."""
        return float(self.principal) * (1 + self.interest_rate / 100.0)


class Repayment(Base):
    __tablename__ = "repayments"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    principal_component = Column(Numeric(12, 2), default=0)
    interest_component = Column(Numeric(12, 2), default=0)
    payment_date = Column(DateTime, nullable=False, index=True)
    method = Column(String(20), default="mpesa_c2b")  # mpesa_c2b | stk_push | cash
    mpesa_ref = Column(String(30))

    loan = relationship("Loan", back_populates="repayments")


class PaymentTransaction(Base):
    """Raw M-Pesa (Daraja) transaction log — B2C disbursements, STK pushes, C2B callbacks."""
    __tablename__ = "payment_transactions"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    type = Column(String(15), nullable=False)          # b2c | stk_push | c2b
    loan_id = Column(Integer, ForeignKey("loans.id"))
    amount = Column(Numeric(12, 2), nullable=False)
    phone = Column(String(20))
    mpesa_ref = Column(String(40))
    status = Column(String(20), default="success")     # success | pending | failed
    raw_payload = Column(JSON, default=dict)           # full Daraja-shaped request/response
    created_at = Column(DateTime, default=datetime.utcnow)


class SmsLog(Base):
    __tablename__ = "sms_logs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    recipient_phone = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    trigger_type = Column(String(30), default="manual")  # loan_approval | repayment_reminder | overdue_alert | ticket_resolution | manual
    status = Column(String(15), default="sent")           # sent | delivered | failed | queued
    provider = Column(String(30))                          # e.g. uwazii | mock
    provider_ref = Column(String(80))                      # provider message id
    provider_response = Column(Text)                       # raw provider response (truncated)
    error = Column(Text)                                   # failure reason (null on success)
    sent_at = Column(DateTime, default=datetime.utcnow)

    # --- Delivery reporting (populated by the Uwazii DLR callback) ----------
    delivery_status = Column(String(15), default="unknown")  # unknown | delivered | failed | undelivered
    delivered_at = Column(DateTime)                          # set when a DLR marks it delivered

    # --- Per-message billing (snapshot of the active rate at send time) -----
    billable = Column(Boolean, default=False)                # True only for status='sent'
    sell_price_kes = Column(Numeric(10, 4))                  # price charged to the DCP
    cost_price_kes = Column(Numeric(10, 4))                  # our cost from Uwazii
    margin_kes = Column(Numeric(10, 4))                      # sell - cost


class SmsTemplate(Base):
    """Per-DCP customizable SMS body for a loan-lifecycle event.

    One row per (tenant_id, event_key). The body supports {{placeholder}} tokens
    (see app/services/sms.py CANONICAL placeholders) rendered from a per-event
    context. When a tenant has no row for an event the service falls back to the
    built-in DEFAULT_TEMPLATES, so existing tenants keep working unchanged.
    """
    __tablename__ = "sms_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_key", name="uq_sms_templates_tenant_event"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    event_key = Column(String(40), nullable=False)  # loan_qualified | loan_disbursed | ...
    body = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
