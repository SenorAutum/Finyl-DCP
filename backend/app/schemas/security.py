"""Pydantic schemas for security config, OTP and device binding (Phase 2)."""
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class SecurityConfigUpdate(BaseModel):
    require_otp: Optional[bool] = None
    otp_channels: Optional[list] = None
    device_binding_enabled: Optional[bool] = None
    geofence_enabled: Optional[bool] = None
    geofence_radius_km: Optional[Decimal] = None
    geofence_center_lat: Optional[Decimal] = None
    geofence_center_lng: Optional[Decimal] = None
    time_fence_enabled: Optional[bool] = None
    time_fence_start: Optional[str] = None      # "HH:MM"
    time_fence_end: Optional[str] = None
    time_fence_timezone: Optional[str] = None
    stipend_rate_kes_per_km: Optional[Decimal] = None
    screenshot_on_action: Optional[bool] = None
    activity_log_enabled: Optional[bool] = None


class OtpRequestIn(BaseModel):
    purpose: str = "login"


class OtpVerifyIn(BaseModel):
    code: str
    purpose: str = "login"


class DeviceRegisterIn(BaseModel):
    device_fingerprint: str
    device_name: Optional[str] = None
    platform: Optional[str] = None
