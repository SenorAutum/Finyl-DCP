"""Customer engagement models: CRM leads, site visits, call logs, complaints, impact surveys."""
from datetime import datetime, timedelta

from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey,
                        Integer, Numeric, String, Text)
from sqlalchemy.orm import relationship

from app.core.database import Base

CRM_STAGES = ["lead", "contacted", "field_visit", "app_setup", "disbursed"]
CALL_OUTCOMES = ["promise_to_pay", "no_answer", "paid", "dispute", "wrong_number", "call_back"]
COMPLAINT_CATEGORIES = ["system_error", "collection_harassment", "balance_dispute", "fraud", "service_quality", "other"]
COMPLAINT_STATUSES = ["open", "in_progress", "resolved", "closed"]
SLA_DAYS = 14  # CBK Digital Credit Providers Regulations — 14-day complaint resolution ceiling


class CrmLead(Base):
    __tablename__ = "crm_leads"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    phone = Column(String(20))
    sector = Column(String(60))
    region_id = Column(Integer, ForeignKey("regions.id"))
    stage = Column(String(20), default="lead", index=True)
    assigned_staff_id = Column(Integer, ForeignKey("staff.id"))
    estimated_loan_amount = Column(Numeric(12, 2), default=0)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    assigned_staff = relationship("Staff")
    visits = relationship("SiteVisit", back_populates="lead")


class SiteVisit(Base):
    __tablename__ = "site_visits"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("crm_leads.id"), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.id"))
    visit_date = Column(Date)
    latitude = Column(Float)      # mock geo-tag
    longitude = Column(Float)
    outcome = Column(String(40))  # positive | needs_follow_up | not_viable
    notes = Column(Text)

    lead = relationship("CrmLead", back_populates="visits")
    staff = relationship("Staff")


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id = Column(Integer, ForeignKey("staff.id"), nullable=False, index=True)
    borrower_id = Column(Integer, ForeignKey("borrowers.id"), nullable=False)
    loan_id = Column(Integer, ForeignKey("loans.id"))
    call_date = Column(DateTime, default=datetime.utcnow)
    duration_seconds = Column(Integer, default=0)
    call_outcome = Column(String(20), default="no_answer")
    promise_to_pay_date = Column(Date)
    promise_amount = Column(Numeric(12, 2))
    notes = Column(Text)

    agent = relationship("Staff")
    borrower = relationship("Borrower")


class Complaint(Base):
    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    ticket_id = Column(String(40), nullable=False, index=True)
    borrower_id = Column(Integer, ForeignKey("borrowers.id"))
    category = Column(String(30), nullable=False, default="other")
    description = Column(Text)
    status = Column(String(15), default="open", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    sla_deadline = Column(DateTime)     # created_at + 14 days (regulatory ceiling)
    resolved_at = Column(DateTime)
    assigned_staff_id = Column(Integer, ForeignKey("staff.id"))
    remedial_action = Column(Text)

    borrower = relationship("Borrower")
    assigned_staff = relationship("Staff")

    @staticmethod
    def compute_sla(created: datetime) -> datetime:
        return created + timedelta(days=SLA_DAYS)


class ImpactSurvey(Base):
    """Impact questionnaire captured before every 2nd+ loan cycle application."""
    __tablename__ = "impact_surveys"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    survey_id = Column(String(40), nullable=False)
    borrower_id = Column(Integer, ForeignKey("borrowers.id"), nullable=False)
    loan_id = Column(Integer, ForeignKey("loans.id"))
    loan_cycle_number = Column(Integer, default=2)
    monthly_sales_pre = Column(Numeric(12, 2), default=0)
    monthly_sales_post = Column(Numeric(12, 2), default=0)
    jobs_created = Column(Integer, default=0)
    sales_improved = Column(Boolean, default=False)
    next_capital_plan = Column(Text)
    survey_date = Column(Date)

    borrower = relationship("Borrower")


class AmlFlag(Base):
    __tablename__ = "aml_flags"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"))
    borrower_id = Column(Integer, ForeignKey("borrowers.id"))
    flag_type = Column(String(40), nullable=False)   # structuring | rapid_small_repayments | velocity
    severity = Column(String(10), default="medium")  # low | medium | high
    details = Column(Text)
    flagged_at = Column(DateTime, default=datetime.utcnow)
    reviewed = Column(Boolean, default=False)

    borrower = relationship("Borrower")
