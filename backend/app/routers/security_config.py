"""Tenant security config, OTP request/verify, device management (Phase 2)."""
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (require_permission, get_current_user, get_tenant_id,
                           write_audit)
from app.models import TenantSecurityConfig, User
from app.schemas import (SecurityConfigUpdate, OtpRequestIn, OtpVerifyIn,
                         DeviceRegisterIn)
from app.services import otp as otp_svc, device_binding

router = APIRouter(prefix="/api/v1", tags=["security"])

_CFG_FIELDS = ["require_otp", "otp_channels", "device_binding_enabled",
               "geofence_enabled", "geofence_radius_km", "geofence_center_lat",
               "geofence_center_lng", "time_fence_enabled", "time_fence_timezone",
               "stipend_rate_kes_per_km", "screenshot_on_action", "activity_log_enabled"]


def _num(v):
    return float(v) if v is not None else None


def _cfg_dict(c: TenantSecurityConfig) -> dict:
    return {
        "require_otp": c.require_otp, "otp_channels": c.otp_channels,
        "device_binding_enabled": c.device_binding_enabled,
        "geofence_enabled": c.geofence_enabled,
        "geofence_radius_km": _num(c.geofence_radius_km),
        "geofence_center_lat": _num(c.geofence_center_lat),
        "geofence_center_lng": _num(c.geofence_center_lng),
        "time_fence_enabled": c.time_fence_enabled,
        "time_fence_start": c.time_fence_start.strftime("%H:%M") if c.time_fence_start else None,
        "time_fence_end": c.time_fence_end.strftime("%H:%M") if c.time_fence_end else None,
        "time_fence_timezone": c.time_fence_timezone,
        "stipend_rate_kes_per_km": _num(c.stipend_rate_kes_per_km),
        "screenshot_on_action": c.screenshot_on_action,
        "activity_log_enabled": c.activity_log_enabled,
        "updated_at": c.updated_at,
    }


def _get_or_create(db: Session, tenant_id: int) -> TenantSecurityConfig:
    c = (db.query(TenantSecurityConfig)
         .filter(TenantSecurityConfig.tenant_id == tenant_id).first())
    if not c:
        c = TenantSecurityConfig(tenant_id=tenant_id)
        db.add(c)
        db.commit()
        db.refresh(c)
    return c


def _parse_time(s: str | None):
    if not s:
        return None
    try:
        h, m = s.split(":")
        return time(int(h), int(m))
    except Exception:
        raise HTTPException(400, f"Invalid time value: {s}")


# --- Security config -------------------------------------------------------
@router.get("/settings/security")
def get_security(tenant_id: int = Depends(get_tenant_id),
                 user=Depends(require_permission("security.manage")),
                 db: Session = Depends(get_db)):
    return _cfg_dict(_get_or_create(db, tenant_id))


@router.put("/settings/security")
def update_security(body: SecurityConfigUpdate, tenant_id: int = Depends(get_tenant_id),
                    user=Depends(require_permission("security.manage")),
                    db: Session = Depends(get_db), request: Request = None):
    c = _get_or_create(db, tenant_id)
    for f in _CFG_FIELDS:
        val = getattr(body, f, None)
        if val is not None:
            setattr(c, f, val)
    if body.time_fence_start is not None:
        c.time_fence_start = _parse_time(body.time_fence_start)
    if body.time_fence_end is not None:
        c.time_fence_end = _parse_time(body.time_fence_end)
    c.updated_at = datetime.now(timezone.utc)
    c.updated_by = user.id
    write_audit(db, tenant_id=tenant_id, user=user, action="settings.security.update",
                entity_type="tenant", entity_id=tenant_id, request=request)
    db.commit()
    return _cfg_dict(c)


# --- OTP -------------------------------------------------------------------
@router.post("/auth/otp/request")
def otp_request(body: OtpRequestIn, user=Depends(get_current_user),
                db: Session = Depends(get_db), request: Request = None):
    ip = request.headers.get("x-forwarded-for") if request else None
    code = otp_svc.generate(db, user, purpose=body.purpose, ip=ip)
    delivery = otp_svc.deliver(db, user, code)
    return {"status": "sent", "purpose": body.purpose, "delivery": delivery}


@router.post("/auth/otp/verify")
def otp_verify(body: OtpVerifyIn, user=Depends(get_current_user),
               db: Session = Depends(get_db)):
    ok, reason = otp_svc.verify(db, user, body.code, purpose=body.purpose)
    if not ok:
        raise HTTPException(400, f"OTP verification failed: {reason}")
    return {"status": "verified", "purpose": body.purpose}


# --- Devices ---------------------------------------------------------------
def _can_manage_user_devices(actor: User, target_user_id: int) -> bool:
    return actor.id == target_user_id or actor.role in ("super_admin", "system_admin", "tenant_admin")


@router.get("/users/{user_id}/devices")
def list_devices(user_id: int, actor=Depends(get_current_user), db: Session = Depends(get_db)):
    if not _can_manage_user_devices(actor, user_id):
        raise HTTPException(403, "Cannot view another user's devices")
    rows = device_binding.list_devices(db, user_id)
    return {"items": [{"id": d.id, "device_name": d.device_name, "platform": d.platform,
                       "active": d.active, "registered_at": d.registered_at,
                       "last_seen_at": d.last_seen_at} for d in rows]}


@router.post("/users/{user_id}/devices")
def register_device(user_id: int, body: DeviceRegisterIn, actor=Depends(get_current_user),
                    db: Session = Depends(get_db), request: Request = None):
    if not _can_manage_user_devices(actor, user_id):
        raise HTTPException(403, "Cannot register a device for another user")
    ip = request.headers.get("x-forwarded-for") if request else None
    d = device_binding.register(db, user_id=user_id, fingerprint=body.device_fingerprint,
                                name=body.device_name, platform=body.platform, ip=ip)
    return {"id": d.id, "device_name": d.device_name, "active": d.active}


@router.delete("/users/{user_id}/devices/{device_id}")
def revoke_device(user_id: int, device_id: int, actor=Depends(get_current_user),
                  db: Session = Depends(get_db)):
    if not _can_manage_user_devices(actor, user_id):
        raise HTTPException(403, "Cannot revoke another user's device")
    if not device_binding.revoke(db, user_id=user_id, device_id=device_id):
        raise HTTPException(404, "Device not found")
    return {"status": "revoked"}
