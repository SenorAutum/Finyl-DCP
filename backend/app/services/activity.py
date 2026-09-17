"""Activity + screenshot log ingestion and admin query helpers (Phase 2)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ActivityLog, ScreenshotLog, TenantSecurityConfig


def _enabled(db: Session, tenant_id: int) -> bool:
    cfg = (db.query(TenantSecurityConfig)
           .filter(TenantSecurityConfig.tenant_id == tenant_id).first())
    # Default ON when no config row exists (migration default is true).
    return True if cfg is None else bool(cfg.activity_log_enabled)


def log_events(db: Session, *, tenant_id: int, user_id: int, events: list,
               ip: str | None = None, device_fingerprint: str | None = None) -> int:
    """Bulk-insert activity events. Returns number stored (0 if logging disabled)."""
    if not _enabled(db, tenant_id):
        return 0
    n = 0
    for ev in events or []:
        db.add(ActivityLog(
            tenant_id=tenant_id, user_id=user_id,
            session_id=ev.session_id, event_type=ev.event_type,
            event_detail=ev.event_detail,
            ip=ip, device_fingerprint=ev.device_fingerprint or device_fingerprint,
            recorded_at=datetime.now(timezone.utc),
        ))
        n += 1
    db.commit()
    return n


def log_screenshot(db: Session, *, tenant_id: int, user_id: int, storage_path: str,
                   thumbnail_path=None, capture_trigger="action", event_type=None,
                   session_id=None, activity_log_id=None) -> ScreenshotLog:
    row = ScreenshotLog(
        tenant_id=tenant_id, user_id=user_id, storage_path=storage_path,
        thumbnail_path=thumbnail_path, capture_trigger=capture_trigger,
        event_type=event_type, session_id=session_id, activity_log_id=activity_log_id,
        recorded_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    return row


def query_logs(db: Session, *, tenant_id: int, user_id: int | None = None,
               date_from=None, date_to=None, event_type: str = "",
               page: int = 1, page_size: int = 50) -> dict:
    q = db.query(ActivityLog).filter(ActivityLog.tenant_id == tenant_id)
    if user_id:
        q = q.filter(ActivityLog.user_id == user_id)
    if event_type:
        q = q.filter(ActivityLog.event_type == event_type)
    if date_from:
        q = q.filter(ActivityLog.recorded_at >= date_from)
    if date_to:
        q = q.filter(ActivityLog.recorded_at <= date_to)
    total = q.count()
    rows = (q.order_by(ActivityLog.recorded_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "page": page, "items": [
        {"id": r.id, "user_id": r.user_id, "event_type": r.event_type,
         "event_detail": r.event_detail, "ip": r.ip,
         "device_fingerprint": r.device_fingerprint, "recorded_at": r.recorded_at}
        for r in rows]}


def query_screenshots(db: Session, *, tenant_id: int, user_id: int | None = None,
                      date_from=None, date_to=None, page: int = 1, page_size: int = 50) -> dict:
    q = db.query(ScreenshotLog).filter(ScreenshotLog.tenant_id == tenant_id)
    if user_id:
        q = q.filter(ScreenshotLog.user_id == user_id)
    if date_from:
        q = q.filter(ScreenshotLog.recorded_at >= date_from)
    if date_to:
        q = q.filter(ScreenshotLog.recorded_at <= date_to)
    total = q.count()
    rows = (q.order_by(ScreenshotLog.recorded_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "page": page, "items": [
        {"id": r.id, "user_id": r.user_id, "storage_path": r.storage_path,
         "thumbnail_path": r.thumbnail_path, "capture_trigger": r.capture_trigger,
         "event_type": r.event_type, "recorded_at": r.recorded_at}
        for r in rows]}
