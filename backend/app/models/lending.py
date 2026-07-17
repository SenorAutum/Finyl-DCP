"""Core lending models: borrowers, loans, repayments, payment transactions."""
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey,
                        Integer, Numeric, String, Text, JSON)
from sqlalchemy.orm import relationship

from app.core.database import Base

LOAN_STATUSES = ["pending", "underwriting", "approved", "active", "paid", "overdue", "defaulted", "rejected"]


class Borrower(Base):
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

    loans = relationship("Loan", back_populates="borrower")

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
    status = Column(String(15), default="sent")
    sent_at = Column(DateTime, default=datetime.utcnow)
