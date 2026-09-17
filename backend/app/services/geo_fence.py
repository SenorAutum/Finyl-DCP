"""Geo-fence + time-fence helpers (Phase 2).

Pure functions plus a couple of DB-config-aware evaluators. No external deps.
"""
from __future__ import annotations

import math
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import TenantSecurityConfig


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance between two WGS84 points in kilometres."""
    r = 6371.0088
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlmb = math.radians(float(lon2) - float(lon1))
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def get_config(db: Session, tenant_id: int) -> TenantSecurityConfig | None:
    return (db.query(TenantSecurityConfig)
            .filter(TenantSecurityConfig.tenant_id == tenant_id).first())


def evaluate_geofence(db: Session, tenant_id: int, lat, lng) -> str:
    """Return 'inside' | 'outside' | 'disabled'."""
    cfg = get_config(db, tenant_id)
    if not cfg or not cfg.geofence_enabled:
        return "disabled"
    if cfg.geofence_center_lat is None or cfg.geofence_center_lng is None or cfg.geofence_radius_km is None:
        return "disabled"
    if lat is None or lng is None:
        return "outside"
    dist = haversine_km(cfg.geofence_center_lat, cfg.geofence_center_lng, lat, lng)
    return "inside" if dist <= float(cfg.geofence_radius_km) else "outside"


def evaluate_timefence(db: Session, tenant_id: int, now: datetime | None = None) -> str:
    """Return 'inside' | 'outside' | 'disabled' for the tenant working-hours window."""
    cfg = get_config(db, tenant_id)
    if not cfg or not cfg.time_fence_enabled:
        return "disabled"
    if not cfg.time_fence_start or not cfg.time_fence_end:
        return "disabled"
    tz = ZoneInfo(cfg.time_fence_timezone or "Africa/Nairobi")
    now = (now or datetime.now(tz)).astimezone(tz)
    cur: time = now.time()
    start, end = cfg.time_fence_start, cfg.time_fence_end
    if start <= end:
        ok = start <= cur <= end
    else:  # window spans midnight
        ok = cur >= start or cur <= end
    return "inside" if ok else "outside"
