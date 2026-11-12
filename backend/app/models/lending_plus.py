"""WP16 — lending additions (migration 045): loan actions that need a second
person (write-off, waiver, reschedule, top-up), the collateral register, and
group lending (groups, members, meetings)."""
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.core.timeutil import UTCDateTime


class LoanAction(Base):
    __tablename__ = "loan_actions"
    __table_args__ = (
        Index("ix_loan_actions_status", "tenant_id", "status"),
        Index("ix_loan_actions_loan", "loan_id"),
        Index("uq_loan_action_pending", "loan_id", "kind", unique=True, postgresql_where=text("status = 'pending'")),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False)
    kind = Column(String(15), nullable=False)            # writeoff | waiver | reschedule | topup
    status = Column(String(12), nullable=False, default="pending")   # pending | done | rejected | cancelled
    details = Column(JSONB, nullable=False, default=dict)
    reason = Column(Text, nullable=False)
    requested_by_user_id = Column(Integer, ForeignKey("users.id"))
    decided_by_user_id = Column(Integer, ForeignKey("users.id"))
    decided_at = Column(UTCDateTime)
    decision_note = Column(Text)
    journal_id = Column(Integer, ForeignKey("acc_journals.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class LoanCollateral(Base):
    __tablename__ = "loan_collateral"
    __table_args__ = (Index("ix_loan_collateral_loan", "loan_id"),
                      Index("ix_loan_collateral_borrower", "tenant_id", "borrower_id"))

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    borrower_id = Column(Integer, ForeignKey("borrowers.id"), nullable=False)
    loan_id = Column(Integer, ForeignKey("loans.id"))
    # logbook | title_deed | household | business_equipment | stock | savings | chattel | other
    kind = Column(String(20), nullable=False)
    description = Column(String(300), nullable=False)
    identifier = Column(String(80))                       # registration / title / serial number
    estimated_value = Column(Numeric(14, 2), nullable=False, default=0)
    forced_sale_value = Column(Numeric(14, 2))
    valued_by = Column(String(120))
    valued_on = Column(Date)
    location = Column(String(200))
    insured = Column(Boolean, nullable=False, default=False)
    insurer = Column(String(120))
    policy_no = Column(String(60))
    insurance_expiry = Column(Date)
    status = Column(String(12), nullable=False, default="held")   # held | released | seized | sold
    received_on = Column(Date, nullable=False)
    released_on = Column(Date)
    status_note = Column(Text)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class LendingGroup(Base):
    __tablename__ = "lending_groups"
    __table_args__ = (Index("uq_lending_group_code", "tenant_id", "code", unique=True),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    code = Column(String(20), nullable=False)
    name = Column(String(120), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"))
    officer_staff_id = Column(Integer, ForeignKey("staff.id"))
    registration_no = Column(String(60))                  # social services / self-help group certificate
    meeting_day = Column(String(10))
    meeting_frequency = Column(String(12), nullable=False, default="weekly")   # weekly | fortnightly | monthly
    meeting_place = Column(String(200))
    status = Column(String(12), nullable=False, default="active")              # active | dormant | closed
    formed_on = Column(Date)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class LendingGroupMember(Base):
    __tablename__ = "lending_group_members"
    __table_args__ = (Index("uq_group_member_active", "group_id", "borrower_id", unique=True,
                            postgresql_where=text("active")),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    group_id = Column(Integer, ForeignKey("lending_groups.id", ondelete="CASCADE"), nullable=False)
    borrower_id = Column(Integer, ForeignKey("borrowers.id"), nullable=False)
    role = Column(String(12), nullable=False, default="member")   # chair | secretary | treasurer | member
    joined_on = Column(Date, nullable=False)
    left_on = Column(Date)
    active = Column(Boolean, nullable=False, default=True)


class LendingGroupMeeting(Base):
    __tablename__ = "lending_group_meetings"
    __table_args__ = (UniqueConstraint("group_id", "held_on", name="uq_group_meeting"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    group_id = Column(Integer, ForeignKey("lending_groups.id", ondelete="CASCADE"), nullable=False)
    held_on = Column(Date, nullable=False)
    present = Column(JSONB, nullable=False, default=list)       # borrower ids present
    collected = Column(Numeric(14, 2), nullable=False, default=0)
    notes = Column(Text)
    recorded_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)
