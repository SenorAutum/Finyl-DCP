"""WP17 — CRM expansion (migration 046): interactions, follow-up tasks, client
segments and SMS campaigns."""
from datetime import datetime

from sqlalchemy import Column, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.core.timeutil import UTCDateTime


class CrmInteraction(Base):
    __tablename__ = "crm_interactions"
    __table_args__ = (Index("ix_crm_interactions_borrower", "borrower_id"),
                      Index("ix_crm_interactions_lead", "lead_id"),
                      Index("ix_crm_interactions_tenant", "tenant_id", "happened_at"))

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    borrower_id = Column(Integer, ForeignKey("borrowers.id"))
    lead_id = Column(Integer, ForeignKey("crm_leads.id"))
    kind = Column(String(12), nullable=False)          # call | visit | sms | whatsapp | meeting | email | note
    direction = Column(String(8), nullable=False, default="out")   # in | out
    subject = Column(String(200))
    notes = Column(Text)
    outcome = Column(String(30))                        # reached | no_answer | interested | not_interested | promise | complaint | other
    happened_at = Column(UTCDateTime, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class CrmTask(Base):
    __tablename__ = "crm_tasks"
    __table_args__ = (Index("ix_crm_tasks_assigned", "tenant_id", "assigned_user_id", "status"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    title = Column(String(200), nullable=False)
    notes = Column(Text)
    due_date = Column(Date, nullable=False)
    priority = Column(String(8), nullable=False, default="normal")   # low | normal | high
    status = Column(String(10), nullable=False, default="open")       # open | done | cancelled
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    borrower_id = Column(Integer, ForeignKey("borrowers.id"))
    lead_id = Column(Integer, ForeignKey("crm_leads.id"))
    loan_id = Column(Integer, ForeignKey("loans.id"))
    interaction_id = Column(Integer, ForeignKey("crm_interactions.id"))
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    completed_at = Column(UTCDateTime)
    completion_note = Column(Text)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class CrmSegment(Base):
    __tablename__ = "crm_segments"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_crm_segment_name"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(120), nullable=False)
    description = Column(Text)
    rules = Column(JSONB, nullable=False, default=dict)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class CrmCampaign(Base):
    __tablename__ = "crm_campaigns"
    __table_args__ = (Index("ix_crm_campaigns_status", "tenant_id", "status"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(120), nullable=False)
    segment_id = Column(Integer, ForeignKey("crm_segments.id"), nullable=False)
    purpose = Column(String(10), nullable=False, default="marketing")   # marketing | service
    message = Column(Text, nullable=False)
    # draft | pending (approval) | approved (waiting for send_at) | sending | sent | rejected | cancelled
    status = Column(String(12), nullable=False, default="draft")
    send_at = Column(UTCDateTime)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    approved_by_user_id = Column(Integer, ForeignKey("users.id"))
    approved_at = Column(UTCDateTime)
    reject_reason = Column(Text)
    recipients = Column(Integer, nullable=False, default=0)
    sent = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    skipped = Column(Integer, nullable=False, default=0)
    started_at = Column(UTCDateTime)
    finished_at = Column(UTCDateTime)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class CrmCampaignRecipient(Base):
    __tablename__ = "crm_campaign_recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "borrower_id", name="uq_crm_campaign_recipient"),)

    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("crm_campaigns.id", ondelete="CASCADE"), nullable=False)
    borrower_id = Column(Integer, ForeignKey("borrowers.id"), nullable=False)
    phone = Column(String(20))
    status = Column(String(12), nullable=False)          # sent | failed | skipped
    reason = Column(String(60))
    sms_log_id = Column(Integer, ForeignKey("sms_logs.id"))
