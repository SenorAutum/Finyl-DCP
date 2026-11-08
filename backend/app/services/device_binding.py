"""Device fingerprint registration + binding checks (Phase 2)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import UserDevice


def register(db: Session, *, user_id: int, fingerprint: str, name: str | None = None,
             platform: str | None = None, ip: str | None = None) -> UserDevice:
    existing = (db.query(UserDevice)
                .filter(UserDevice.user_id == user_id,
                        UserDevice.device_fingerprint == fingerprint).first())
    if existing:
        existing.active = True
        existing.pending = False
        existing.last_seen_at = datetime.now(timezone.utc)
        if name:
            existing.device_name = name
        if platform:
            existing.platform = platform
        db.commit()
        return existing
    dev = UserDevice(user_id=user_id, device_fingerprint=fingerprint,
                     device_name=name, platform=platform, registered_by_ip=ip,
                     last_seen_at=datetime.now(timezone.utc))
    db.add(dev)
    db.commit()
    return dev


def list_devices(db: Session, user_id: int) -> list[UserDevice]:
    return (db.query(UserDevice)
            .filter(UserDevice.user_id == user_id)
            .order_by(UserDevice.registered_at.desc()).all())


def revoke(db: Session, *, user_id: int, device_id: int) -> bool:
    dev = (db.query(UserDevice)
           .filter(UserDevice.id == device_id, UserDevice.user_id == user_id).first())
    if not dev:
        return False
    dev.active = False
    dev.pending = False
    db.commit()
    return True


def device_code(fingerprint: str) -> str:
    """Short code staff can read out to an administrator, e.g. 'A1B2-C3D4'."""
    clean = "".join(c for c in (fingerprint or "").upper() if c.isalnum())[:8]
    return f"{clean[:4]}-{clean[4:8]}" if len(clean) > 4 else clean


def request_approval(db: Session, *, user_id: int, fingerprint: str, ip: str | None = None,
                     user_agent: str | None = None) -> UserDevice | None:
    """WP7 (review 7.7): a sign-in blocked because the device is not registered
    leaves a request for an administrator to approve, instead of a dead end.
    Returns the request (None when no fingerprint was sent)."""
    if not fingerprint:
        return None
    fingerprint = fingerprint[:500]
    dev = (db.query(UserDevice)
           .filter(UserDevice.user_id == user_id,
                   UserDevice.device_fingerprint == fingerprint).first())
    if dev is None:
        dev = UserDevice(user_id=user_id, device_fingerprint=fingerprint,
                         device_name=(user_agent or "Browser")[:200], platform="web",
                         registered_by_ip=ip, active=False, pending=True)
        db.add(dev)
    elif not dev.active:
        dev.pending = True
    dev.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return dev


def approve(db: Session, *, user_id: int, device_id: int) -> UserDevice | None:
    dev = (db.query(UserDevice)
           .filter(UserDevice.id == device_id, UserDevice.user_id == user_id).first())
    if dev is None:
        return None
    dev.active = True
    dev.pending = False
    db.commit()
    return dev


def is_known(db: Session, *, user_id: int, fingerprint: str) -> bool:
    if not fingerprint:
        return False
    dev = (db.query(UserDevice)
           .filter(UserDevice.user_id == user_id,
                   UserDevice.device_fingerprint == fingerprint,
                   UserDevice.active == True,  # noqa: E712
                   UserDevice.pending == False).first())  # noqa: E712
    if dev:
        dev.last_seen_at = datetime.now(timezone.utc)
        db.commit()
        return True
    return False
