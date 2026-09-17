"""Staff activity + screenshot monitoring (Phase 2)."""
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (require_module, require_permission, get_current_user,
                           get_device_fingerprint)
from app.schemas import ActivityBulkIn, ScreenshotIn
from app.services import activity as activity_service

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])


def _parse_dt(v: str | None):
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except Exception:
        return None


@router.post("/log")
def log_events(body: ActivityBulkIn,
               tenant_id: int = Depends(require_module("lending")),
               user=Depends(get_current_user),
               fingerprint: str | None = Depends(get_device_fingerprint),
               db: Session = Depends(get_db), request: Request = None):
    ip = None
    if request is not None:
        ip = request.headers.get("x-forwarded-for") or (
            request.client.host if request.client else None)
    stored = activity_service.log_events(
        db, tenant_id=tenant_id, user_id=user.id, events=body.events,
        ip=ip, device_fingerprint=fingerprint)
    return {"stored": stored}


@router.post("/screenshot")
def log_screenshot(body: ScreenshotIn,
                   tenant_id: int = Depends(require_module("lending")),
                   user=Depends(get_current_user),
                   db: Session = Depends(get_db)):
    row = activity_service.log_screenshot(
        db, tenant_id=tenant_id, user_id=user.id, storage_path=body.storage_path,
        thumbnail_path=body.thumbnail_path, capture_trigger=body.capture_trigger,
        event_type=body.event_type, session_id=body.session_id,
        activity_log_id=body.activity_log_id)
    return {"id": row.id, "recorded_at": row.recorded_at}


@router.get("/logs")
def list_logs(user_id: int = 0, event_type: str = "", date_from: str = "",
              date_to: str = "", page: int = 1, page_size: int = 50,
              tenant_id: int = Depends(require_module("lending")),
              admin=Depends(require_permission("activity.view")),
              db: Session = Depends(get_db)):
    return activity_service.query_logs(
        db, tenant_id=tenant_id, user_id=user_id or None,
        date_from=_parse_dt(date_from), date_to=_parse_dt(date_to),
        event_type=event_type, page=page, page_size=page_size)


@router.get("/screenshots")
def list_screenshots(user_id: int = 0, date_from: str = "", date_to: str = "",
                     page: int = 1, page_size: int = 50,
                     tenant_id: int = Depends(require_module("lending")),
                     admin=Depends(require_permission("activity.view")),
                     db: Session = Depends(get_db)):
    return activity_service.query_screenshots(
        db, tenant_id=tenant_id, user_id=user_id or None,
        date_from=_parse_dt(date_from), date_to=_parse_dt(date_to),
        page=page, page_size=page_size)
