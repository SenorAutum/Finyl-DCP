"""Pydantic schemas for activity + screenshot monitoring (Phase 2)."""
from typing import Optional

from pydantic import BaseModel


class ActivityEvent(BaseModel):
    event_type: str
    event_detail: Optional[dict] = None
    session_id: Optional[str] = None
    device_fingerprint: Optional[str] = None


class ActivityBulkIn(BaseModel):
    events: list[ActivityEvent]


class ScreenshotIn(BaseModel):
    storage_path: str
    thumbnail_path: Optional[str] = None
    capture_trigger: str = "action"
    event_type: Optional[str] = None
    session_id: Optional[str] = None
    activity_log_id: Optional[int] = None
