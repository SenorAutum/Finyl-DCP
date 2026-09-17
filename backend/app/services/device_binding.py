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
    db.commit()
    return True


def is_known(db: Session, *, user_id: int, fingerprint: str) -> bool:
    if not fingerprint:
        return False
    dev = (db.query(UserDevice)
           .filter(UserDevice.user_id == user_id,
                   UserDevice.device_fingerprint == fingerprint,
                   UserDevice.active == True).first())  # noqa: E712
    if dev:
        dev.last_seen_at = datetime.now(timezone.utc)
        db.commit()
        return True
    return False
