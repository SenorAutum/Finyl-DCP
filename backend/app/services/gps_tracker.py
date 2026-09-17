"""GPS ping ingestion, daily distance + stipend computation, location alerts (Phase 2)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (StaffGpsLog, StaffDailyTask, LocationAlert,
                        TenantSecurityConfig)
from app.services.geo_fence import haversine_km, evaluate_geofence


def ingest_ping(db: Session, *, tenant_id: int, user_id: int, latitude, longitude,
                accuracy_meters=None, task_type=None, task_ref_id=None,
                device_id=None) -> dict:
    """Store a GPS ping and evaluate geofence; emit an alert on breach."""
    log = StaffGpsLog(
        tenant_id=tenant_id, user_id=user_id, latitude=latitude, longitude=longitude,
        accuracy_meters=accuracy_meters, task_type=task_type, task_ref_id=task_ref_id,
        device_id=device_id, timestamp=datetime.now(timezone.utc),
    )
    db.add(log)
    geo = evaluate_geofence(db, tenant_id, latitude, longitude)
    alert = None
    if geo == "outside":
        alert = LocationAlert(tenant_id=tenant_id, user_id=user_id,
                              alert_type="geofence_breach",
                              detail=f"Ping outside geofence at {latitude},{longitude}")
        db.add(alert)
    db.commit()
    return {"id": log.id, "geofence": geo, "alert_raised": alert is not None}


def report_location_disabled(db: Session, *, tenant_id: int, user_id: int,
                             detail: str | None = None) -> LocationAlert:
    alert = LocationAlert(tenant_id=tenant_id, user_id=user_id, alert_type="disabled",
                          detail=detail or "Location services disabled on device")
    db.add(alert)
    db.commit()
    return alert


def daily_distance_km(db: Session, *, tenant_id: int, user_id: int, day: date) -> float:
    """Running haversine sum over a day's ordered pings."""
    rows = (db.query(StaffGpsLog)
            .filter(StaffGpsLog.tenant_id == tenant_id,
                    StaffGpsLog.user_id == user_id)
            .order_by(StaffGpsLog.timestamp.asc()).all())
    pts = [r for r in rows if r.timestamp and r.timestamp.date() == day]
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        total += haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)
    return round(total, 3)


def compute_stipend(db: Session, *, tenant_id: int, user_id: int, day: date) -> dict:
    """Compute km + transport stipend for one officer-day and upsert the task row."""
    km = daily_distance_km(db, tenant_id=tenant_id, user_id=user_id, day=day)
    cfg = (db.query(TenantSecurityConfig)
           .filter(TenantSecurityConfig.tenant_id == tenant_id).first())
    rate = Decimal(str(cfg.stipend_rate_kes_per_km)) if cfg and cfg.stipend_rate_kes_per_km else Decimal("0")
    stipend = (Decimal(str(km)) * rate).quantize(Decimal("0.01"))
    task = (db.query(StaffDailyTask)
            .filter(StaffDailyTask.tenant_id == tenant_id,
                    StaffDailyTask.user_id == user_id,
                    StaffDailyTask.task_date == day).first())
    if not task:
        task = StaffDailyTask(tenant_id=tenant_id, user_id=user_id, task_date=day,
                              status="active")
        db.add(task)
    task.distance_km = Decimal(str(km))
    task.stipend_kes = stipend
    db.commit()
    return {"user_id": user_id, "date": day.isoformat(), "distance_km": km,
            "stipend_kes": float(stipend)}
