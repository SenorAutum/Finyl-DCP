"""Tenant security config, OTP request/verify, device management (Phase 2)."""
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (require_permission, get_current_user, get_tenant_id,
                           write_audit)
from app.core.ownership import require_user_in_tenant
from app.core.net import client_ip
from app.models import OtpToken, TenantSecurityConfig, User, UserDevice
from app.services.authz import user_can
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
    turning_on_devices = bool(body.device_binding_enabled) and not c.device_binding_enabled
    # WP7: switching on "registered devices only" must not lock out the person
    # doing it. Their current browser is registered for them; everyone else
    # asks for approval at their next sign-in (Device Management).
    if turning_on_devices and user.role != "super_admin":
        fp = request.headers.get("x-device-fingerprint") if request else None
        if not fp:
            raise HTTPException(422, "This browser did not identify itself, so switching on "
                                     "registered devices would lock you out. Reload the page "
                                     "and try again.")
        device_binding.register(db, user_id=user.id, fingerprint=fp[:500],
                                name=(request.headers.get("user-agent") or "Browser")[:200],
                                platform="web", ip=client_ip(request))
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
RESERVED_OTP_PURPOSES = {"login", "consent"}

@router.post("/auth/otp/request")
def otp_request(body: OtpRequestIn, user=Depends(get_current_user),
                db: Session = Depends(get_db), request: Request = None):
    # WP6 (F21/F22): sign-in and client-consent codes have their own flows and
    # bindings. Issuing one here would replace a pending code or mint an
    # unbound one, so those purposes are refused on this generic endpoint.
    if body.purpose in RESERVED_OTP_PURPOSES:
        raise HTTPException(422, f"Codes for '{body.purpose}' cannot be requested here")
    ip = client_ip(request)
    code = otp_svc.generate(db, user, purpose=body.purpose, ip=ip)
    delivery = otp_svc.deliver(db, user, code)
    sent_to = [k for k, v in (delivery.get("channels") or {}).items() if v]
    if not sent_to:
        raise HTTPException(503, "The code could not be sent: add a mobile number to your "
                                 "account (User & Access Management) or try again later.")
    return {"status": "sent", "purpose": body.purpose, "delivery": {"channels": sent_to}}


@router.post("/auth/otp/verify")
def otp_verify(body: OtpVerifyIn, user=Depends(get_current_user),
               db: Session = Depends(get_db)):
    if body.purpose in RESERVED_OTP_PURPOSES:
        raise HTTPException(422, f"Codes for '{body.purpose}' cannot be verified here")
    ok, reason = otp_svc.verify(db, user, body.code, purpose=body.purpose)
    if not ok:
        raise HTTPException(400, f"OTP verification failed: {reason}")
    return {"status": "verified", "purpose": body.purpose}


# --- Devices ---------------------------------------------------------------
def _authorize_device_target(db: Session, actor: User, user_id: int, action: str) -> User:
    """Resolve the user whose devices are being touched.

    The target MUST be in the actor's tenant (404 otherwise, so other tenants'
    user ids cannot be probed). Within the tenant a user manages their own
    devices; holders of security.manage may manage anyone's (WP7: was a fixed
    list of role names, so custom roles and per-DCP grants were ignored).
    """
    target = require_user_in_tenant(db, actor, user_id)
    if actor.id == target.id or user_can(db, actor, "security.manage"):
        return target
    raise HTTPException(403, f"Cannot {action} another user's device")


def _device_dict(d) -> dict:
    return {"id": d.id, "device_name": d.device_name, "platform": d.platform,
            "active": d.active, "pending": bool(getattr(d, "pending", False)),
            "status": ("waiting for approval" if getattr(d, "pending", False)
                       else "active" if d.active else "revoked"),
            "code": device_binding.device_code(d.device_fingerprint),
            "registered_at": d.registered_at, "last_seen_at": d.last_seen_at}


@router.get("/users/{user_id}/devices")
def list_devices(user_id: int, actor=Depends(get_current_user), db: Session = Depends(get_db)):
    _authorize_device_target(db, actor, user_id, "view")
    rows = device_binding.list_devices(db, user_id)
    return {"items": [_device_dict(d) for d in rows]}


@router.get("/devices/pending")
def pending_devices(tenant_id: int = Depends(get_tenant_id),
                    _: User = Depends(require_permission("security.manage")),
                    db: Session = Depends(get_db)):
    """WP7: devices waiting for approval across this DCP (from blocked sign-ins)."""
    rows = (db.query(UserDevice, User)
            .join(User, User.id == UserDevice.user_id)
            .filter(User.tenant_id == tenant_id, UserDevice.pending == True)  # noqa: E712
            .order_by(UserDevice.last_seen_at.desc()).all())
    return {"items": [_device_dict(d) | {"user_id": u.id, "user_name": u.full_name,
                                         "user_email": u.email} for d, u in rows]}


@router.post("/users/{user_id}/devices")
def register_device(user_id: int, body: DeviceRegisterIn, actor=Depends(get_current_user),
                    db: Session = Depends(get_db), request: Request = None):
    _authorize_device_target(db, actor, user_id, "register")
    ip = client_ip(request)
    d = device_binding.register(db, user_id=user_id, fingerprint=body.device_fingerprint,
                                name=body.device_name, platform=body.platform, ip=ip)
    return {"id": d.id, "device_name": d.device_name, "active": d.active}


@router.post("/users/{user_id}/devices/{device_id}/approve")
def approve_device(user_id: int, device_id: int, request: Request,
                   actor: User = Depends(require_permission("security.manage")),
                   db: Session = Depends(get_db)):
    """WP7: approve a device requested from a blocked sign-in. Someone else must
    approve: a person cannot approve their own new device."""
    target = require_user_in_tenant(db, actor, user_id)
    if target.id == actor.id and actor.role != "super_admin":
        raise HTTPException(403, "Another administrator must approve your own device.")
    d = device_binding.approve(db, user_id=user_id, device_id=device_id)
    if d is None:
        raise HTTPException(404, "Device not found")
    write_audit(db, tenant_id=target.tenant_id, user=actor, action="device.approve",
                entity_type="user", entity_id=target.id,
                details={"device_id": d.id, "code": device_binding.device_code(d.device_fingerprint)},
                request=request)
    db.commit()
    return _device_dict(d)


@router.delete("/users/{user_id}/devices/{device_id}")
def revoke_device(user_id: int, device_id: int, actor=Depends(get_current_user),
                  db: Session = Depends(get_db), request: Request = None):
    target = _authorize_device_target(db, actor, user_id, "revoke")
    if not device_binding.revoke(db, user_id=user_id, device_id=device_id):
        raise HTTPException(404, "Device not found")
    # WP7 (review 7.7): end the person's open sessions too. Device checks only
    # run at sign-in, so a session opened on the revoked device would otherwise
    # carry on until it expired.
    target.token_version = int(target.token_version or 0) + 1
    write_audit(db, tenant_id=target.tenant_id, user=actor, action="device.revoke",
                entity_type="user", entity_id=target.id,
                details={"device_id": device_id, "sessions_ended": True}, request=request)
    db.commit()
    return {"status": "revoked", "sessions_ended": True}


# --- One-time code history ---------------------------------------------------
@router.get("/security/otp-history")
def otp_history(tenant_id: int = Depends(get_tenant_id),
                _: User = Depends(require_permission("security.manage")),
                db: Session = Depends(get_db),
                purpose: str | None = None, page: int = 1, page_size: int = 50):
    """WP7 (review 7.11): every one-time code issued in this DCP - who it was
    for, what it was for, whether it was used, and how many wrong tries. The old
    screen guessed at activity-log event names that were never recorded, so it
    was always empty. Codes themselves are never shown (only a hash is kept)."""
    page_size = max(1, min(page_size, 200))
    q = (db.query(OtpToken, User)
         .join(User, User.id == OtpToken.user_id)
         .filter(User.tenant_id == tenant_id))
    if purpose:
        q = q.filter(OtpToken.purpose == purpose)
    total = q.count()
    rows = (q.order_by(OtpToken.created_at.desc(), OtpToken.id.desc())
            .offset((max(page, 1) - 1) * page_size).limit(page_size).all())
    now = datetime.now(timezone.utc)

    def _state(t):
        if t.consumed:
            if t.attempts and t.attempts >= otp_svc.OTP_MAX_ATTEMPTS:
                return "locked after wrong tries"
            return "used or replaced"
        exp = t.expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return "expired" if exp is not None and exp < now else "waiting"
    return {
        "total": total, "page": page, "page_size": page_size,
        "purposes": [p for (p,) in db.query(OtpToken.purpose).join(
            User, User.id == OtpToken.user_id).filter(User.tenant_id == tenant_id)
            .distinct().all()],
        "items": [{
            "id": t.id, "created_at": t.created_at, "expires_at": t.expires_at,
            "purpose": t.purpose, "state": _state(t), "wrong_tries": t.attempts or 0,
            "user_id": u.id, "user_name": u.full_name,
            "recipient_last4": (t.recipient or "")[-4:] or None,
            "client_id": (t.context or {}).get("client_id") if t.context else None,
            "ip": t.ip,
        } for t, u in rows],
    }
