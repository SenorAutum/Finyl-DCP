"""Tenancy & auth models: tenants, feature-flag matrix, users."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base

# Canonical list of feature-flag module keys used across backend + frontend.
MODULE_KEYS = [
    "lending", "payments", "dashboard", "complaints", "crm",
    "call_center", "impact", "cbk_reporting", "ai_agent",
]


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    code = Column(String(20), nullable=False, unique=True)
    logo_color = Column(String(16), default="#10B981")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    modules = relationship("TenantModule", back_populates="tenant", cascade="all, delete-orphan")
    users = relationship("User", back_populates="tenant")


class TenantModule(Base):
    """Feature-flag matrix row: whether `module_key` is enabled for a tenant."""
    __tablename__ = "tenant_modules"
    __table_args__ = (UniqueConstraint("tenant_id", "module_key", name="uq_tenant_module"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    module_key = Column(String(40), nullable=False)
    enabled = Column(Boolean, default=True)

    tenant = relationship("Tenant", back_populates="modules")


class User(Base):
    """Auth principal.

    Roles (permission-driven, see app.core.permissions): super_admin,
    system_admin, relationship_officer, branch_manager, regional_manager,
    disbursement_officer, reconciliation_officer, hq_operations. Legacy roles
    tenant_admin / loan_officer / call_agent are kept for backward compatibility.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(160), nullable=False, unique=True, index=True)
    hashed_password = Column(String(200), nullable=False)
    full_name = Column(String(120), nullable=False)
    role = Column(String(30), nullable=False, default="relationship_officer")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)  # null for super_admin
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # --- RBAC additions (all additive & nullable) ----------------------------
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)   # scoping for branch_manager
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=True)    # scoping for regional_manager
    is_locked = Column(Boolean, default=False)                              # admin lock-out
    force_password_reset = Column(Boolean, default=False)
    deactivated_at = Column(DateTime, nullable=True)

    # --- AUTH-02 brute-force lockout + AUTH-04 token revocation (all additive) --
    failed_login_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    locked_until = Column(DateTime(timezone=True), nullable=True)           # temporary auto-lock window
    token_version = Column(Integer, nullable=False, default=0, server_default="0")  # bump to revoke all tokens

    tenant = relationship("Tenant", back_populates="users")
