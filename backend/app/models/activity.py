"""Activity + screenshot monitoring models (migration 025)."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base

CAPTURE_TRIGGERS = ["action", "periodic", "anomaly", "manual"]


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    session_id = Column(UUID(as_uuid=False))
    event_type = Column(String(100), nullable=False, index=True)
    event_detail = Column(JSONB)
    ip = Column(String(45))
    device_fingerprint = Column(String(500))
    recorded_at = Column(DateTime(timezone=True), default=datetime.utcnow, index=True)


class ScreenshotLog(Base):
    __tablename__ = "screenshot_logs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    session_id = Column(UUID(as_uuid=False))
    storage_path = Column(Text, nullable=False)
    thumbnail_path = Column(Text)
    capture_trigger = Column(String(30), nullable=False, default="action")
    event_type = Column(String(100))
    activity_log_id = Column(Integer, ForeignKey("activity_logs.id"))
    recorded_at = Column(DateTime(timezone=True), default=datetime.utcnow, index=True)
