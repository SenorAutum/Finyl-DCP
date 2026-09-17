"""Field-operations models (migration 023): GPS trails, daily task plans,
location alerts, client home geolocation and business-site photos."""
from datetime import datetime

from sqlalchemy import (Column, Date, DateTime, ForeignKey, Integer, Numeric,
                        String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base

LOCATION_ALERT_TYPES = ["disabled", "geofence_breach", "time_breach"]
DAILY_TASK_STATUSES = ["planned", "active", "completed"]
GPS_TASK_TYPES = ["site_visit", "client_visit", "office", "transit"]


class StaffGpsLog(Base):
    __tablename__ = "staff_gps_logs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    latitude = Column(Numeric(10, 8), nullable=False)
    longitude = Column(Numeric(11, 8), nullable=False)
    accuracy_meters = Column(Numeric(8, 2))
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    task_type = Column(String(50))
    task_ref_id = Column(Integer)
    device_id = Column(String(200))


class StaffDailyTask(Base):
    __tablename__ = "staff_daily_tasks"
    __table_args__ = (UniqueConstraint("user_id", "task_date", name="uq_user_task_date"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    task_date = Column(Date, nullable=False)
    planned_visits = Column(JSONB)
    actual_visits = Column(JSONB)
    distance_km = Column(Numeric(8, 3))
    stipend_kes = Column(Numeric(12, 2))
    status = Column(String(20), nullable=False, default="planned")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class LocationAlert(Base):
    __tablename__ = "location_alerts"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    alert_type = Column(String(30), nullable=False)
    detail = Column(Text)
    triggered_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    acknowledged_by = Column(Integer, ForeignKey("users.id"))
    acknowledged_at = Column(DateTime(timezone=True))


class ClientHomeGeo(Base):
    __tablename__ = "client_home_geo"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    latitude = Column(Numeric(10, 8), nullable=False)
    longitude = Column(Numeric(11, 8), nullable=False)
    accuracy_meters = Column(Numeric(8, 2))
    captured_by = Column(Integer, ForeignKey("users.id"))
    captured_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    photo_path = Column(Text)

    client = relationship("Borrower")


class BusinessSitePhoto(Base):
    __tablename__ = "business_site_photos"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    visit_id = Column(Integer, ForeignKey("site_visits.id"))
    photo_path = Column(Text, nullable=False)
    caption = Column(String(200))
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    uploaded_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    client = relationship("Borrower")
