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


DEFAULT_LOCATION_MAX_AGE = 15      # minutes (WP11)
LOCATION_REQUIRED = "location_required"


def read_location(headers) -> tuple[float | None, float | None, float | None]:
    """(lat, lng, age in seconds) from the X-Geo-* headers the web app sends.
    Missing or unreadable values come back as None."""
    def num(name):
        try:
            v = headers.get(name)
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    lat, lng, age = num("x-geo-lat"), num("x-geo-lng"), num("x-geo-age")
    if lat is not None and not -90 <= lat <= 90:
        lat = None
    if lng is not None and not -180 <= lng <= 180:
        lng = None
    return lat, lng, age


def fence_set(cfg) -> bool:
    return bool(cfg and cfg.geofence_enabled and cfg.geofence_center_lat is not None
                and cfg.geofence_center_lng is not None and cfg.geofence_radius_km is not None)


def location_needed(cfg, lat, lng, age, require_age: bool = True) -> str | None:
    """WP11: why a fresh location must be asked for (None when the one sent
    will do). The age is how old the browser's position fix is; during the
    session a location without an age is treated as out of date (at sign-in
    the web app has just asked for it, so a missing age is accepted there)."""
    if not fence_set(cfg):
        return None
    if lat is None or lng is None:
        return ("Your organisation needs your location while you work. Allow location "
                "access in your browser and try again.")
    limit = (cfg.location_max_age_minutes or DEFAULT_LOCATION_MAX_AGE) * 60
    if (age is None and require_age) or (age is not None and (age < 0 or age > limit)):
        return "Your location is out of date. Allow location access so it can be updated."
    return None


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
