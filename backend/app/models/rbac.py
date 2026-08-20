"""
RBAC support models: approval thresholds, audit trail, maker-checker pending
actions, and HQ-Operations reporting artefacts (schedules, templates, anomaly
flags). All additive — nothing here replaces existing tables.
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer,
                        Numeric, String, Text, JSON)

from app.core.database import Base

# scope_type: role | branch | region  (scope_id references the role name string
# encoded as text via a companion column, or a branch/region id)
# threshold_type: loan_approval | disbursement | refund
THRESHOLD_TYPES = ["loan_approval", "disbursement", "refund"]
SCOPE_TYPES = ["role", "branch", "region"]


class ApprovalThreshold(Base):
    """Configurable per-role / per-branch / per-region monetary limits.

    A loan_approval threshold caps how large a loan a given actor may approve
    before it must escalate. A disbursement / refund threshold sets the amount
    above which a second authorised user (checker) must sign off (maker-checker).
    """
    __tablename__ = "approval_thresholds"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    scope_type = Column(String(10), nullable=False)          # role | branch | region
    scope_key = Column(String(60), nullable=False)           # role name OR branch/region id (as text)
    threshold_type = Column(String(20), nullable=False)      # loan_approval | disbursement | refund
    amount = Column(Numeric(14, 2), nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ApproverSetting(Base):
    """Per-DCP toggle of which roles act as approvers for each approval type.

    A row's absence means "use the default" (i.e. whether the role holds the
    underlying approval permission and is not the front-line originator). A
    present row overrides that with an explicit enabled/disabled flag, letting
    each DCP customise its own approval model.
    """
    __tablename__ = "approver_settings"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    approval_type = Column(String(20), nullable=False)   # loan | client | disbursement | refund
    role = Column(String(40), nullable=False)            # approver role key
    enabled = Column(Boolean, nullable=False, default=True)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


APPROVAL_TYPES = ["loan", "client", "disbursement", "refund"]


class SmsAutomationSetting(Base):
    """Per-DCP SMS automation config.

    Controls whether the daily lifecycle-SMS batch (repayment reminders, overdue
    alerts, defaulting) runs automatically for a tenant, and at which server-local
    hour. Absence of a row falls back to the defaults (enabled, 07:00). A tenant
    with automation_enabled=False is skipped by the all-tenants runner and instead
    dispatches SMS manually via the "Send now" action.
    """
    __tablename__ = "sms_automation_settings"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True, unique=True)
    automation_enabled = Column(Boolean, nullable=False, default=True)
    send_hour = Column(Integer, nullable=False, default=7)   # 0-23, server-local hour
    updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Defaults applied when a tenant has no sms_automation_settings row yet.
SMS_AUTOMATION_DEFAULT_ENABLED = True
SMS_AUTOMATION_DEFAULT_HOUR = 7


class RolePermissionOverride(Base):
    """Per-(tenant, role, permission) grant/revoke layered on the static catalog.

    The static role -> permission map in app/core/permissions.py is the base;
    a row here with granted=True ADDS a permission to a role for that tenant and
    granted=False REMOVES one. This makes the Roles & Permissions matrix editable
    per DCP without mutating code. super_admin is never affected (its wildcard is
    resolved in code and never persisted / never stripped).
    """
    __tablename__ = "role_permission_overrides"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    role = Column(String(40), nullable=False)
    permission_key = Column(String(60), nullable=False)
    granted = Column(Boolean, nullable=False, default=True)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CustomRole(Base):
    """A tenant-defined role, OR a label override for a built-in role.

    When role_key matches a built-in role the row only overrides its display
    label. When role_key is new it defines a custom role whose permission set is
    empty by default and built up entirely via RolePermissionOverride rows.
    """
    __tablename__ = "custom_roles"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    role_key = Column(String(40), nullable=False)
    label = Column(String(120), nullable=False)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(Base):
    """Immutable-ish append log of privileged actions across the platform."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    user_email = Column(String(160))
    action = Column(String(60), nullable=False, index=True)  # e.g. login, user.create, loan.approve
    entity_type = Column(String(40))                         # user | loan | client | branch | ...
    entity_id = Column(String(40))
    details = Column(JSON, default=dict)                     # before/after or free-form context
    ip = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class PendingApproval(Base):
    """Maker-checker queue for money-movement above the configured threshold.

    A disbursement or refund initiated by the maker parks here in
    `pending_approval`; a different authorised user approves or rejects it.
    """
    __tablename__ = "pending_approvals"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    action_type = Column(String(20), nullable=False)        # disbursement | refund
    loan_id = Column(Integer, ForeignKey("loans.id"))
    amount = Column(Numeric(14, 2), nullable=False)
    phone = Column(String(20))
    reason = Column(String(200))
    status = Column(String(20), default="pending_approval", index=True)  # pending_approval | approved | rejected
    maker_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    maker_at = Column(DateTime, default=datetime.utcnow)
    checker_user_id = Column(Integer, ForeignKey("users.id"))
    checker_at = Column(DateTime)
    details = Column(JSON, default=dict)


class ReportSchedule(Base):
    """HQ-Operations recurring report definition (scheduler stub / cron hook)."""
    __tablename__ = "report_schedules"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String(120), nullable=False)
    report_type = Column(String(40), nullable=False)        # loan_book | par | disbursement | collections | productivity
    frequency = Column(String(20), default="weekly")        # daily | weekly | monthly
    recipients = Column(String(400))                        # comma-separated emails
    active = Column(Boolean, default=True)
    last_run_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class ReportTemplate(Base):
    """HQ-Operations saved custom report template definition."""
    __tablename__ = "report_templates"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String(120), nullable=False)
    definition = Column(JSON, default=dict)                 # columns, filters, grouping
    created_at = Column(DateTime, default=datetime.utcnow)


class AnomalyFlag(Base):
    """HQ-Operations anomaly flag routed to Credit / Compliance follow-up."""
    __tablename__ = "anomaly_flags"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    entity_type = Column(String(40))                        # loan | client | payment | branch
    entity_id = Column(String(40))
    note = Column(Text, nullable=False)
    status = Column(String(20), default="open")             # open | acknowledged | resolved
    created_at = Column(DateTime, default=datetime.utcnow)
