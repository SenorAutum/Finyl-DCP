"""OTP / device-binding / tenant security config models (migration 024)."""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, Numeric,
                        String, Time, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base

OTP_PURPOSES = ["login", "password_reset", "device_bind"]


class OtpToken(Base):
    __tablename__ = "otp_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    otp_hash = Column(String(200), nullable=False)
    purpose = Column(String(30), nullable=False, default="login")
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    consumed = Column(Boolean, nullable=False, default=False)
    attempts = Column(Integer, nullable=False, default=0)
    ip = Column(String(45))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class UserDevice(Base):
    __tablename__ = "user_devices"
    __table_args__ = (UniqueConstraint("user_id", "device_fingerprint",
                                       name="uq_user_device"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    device_fingerprint = Column(String(500), nullable=False)
    device_name = Column(String(200))
    platform = Column(String(50))
    registered_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    last_seen_at = Column(DateTime(timezone=True))
    active = Column(Boolean, nullable=False, default=True)
    registered_by_ip = Column(String(45))


class TenantSecurityConfig(Base):
    __tablename__ = "tenant_security_config"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, unique=True, index=True)
    require_otp = Column(Boolean, nullable=False, default=False)
    otp_channels = Column(JSONB, nullable=False, default=lambda: ["sms", "email"])
    device_binding_enabled = Column(Boolean, nullable=False, default=False)
    geofence_enabled = Column(Boolean, nullable=False, default=False)
    geofence_radius_km = Column(Numeric(8, 3))
    geofence_center_lat = Column(Numeric(10, 8))
    geofence_center_lng = Column(Numeric(11, 8))
    time_fence_enabled = Column(Boolean, nullable=False, default=False)
    time_fence_start = Column(Time)
    time_fence_end = Column(Time)
    time_fence_timezone = Column(String(50), nullable=False, default="Africa/Nairobi")
    stipend_rate_kes_per_km = Column(Numeric(8, 2))
    screenshot_on_action = Column(Boolean, nullable=False, default=True)
    activity_log_enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_by = Column(Integer, ForeignKey("users.id"))
