"""Pydantic schemas for field operations (Phase 2)."""
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class GpsLogIn(BaseModel):
    latitude: Decimal
    longitude: Decimal
    accuracy_meters: Optional[Decimal] = None
    task_type: Optional[str] = None
    task_ref_id: Optional[int] = None
    device_id: Optional[str] = None


class DailyTaskCreate(BaseModel):
    user_id: Optional[int] = None       # defaults to caller
    task_date: date
    planned_visits: Optional[list] = None


class DailyTaskUpdate(BaseModel):
    planned_visits: Optional[list] = None
    actual_visits: Optional[list] = None
    status: Optional[str] = None


class ClientHomeGeoIn(BaseModel):
    client_id: int
    latitude: Decimal
    longitude: Decimal
    accuracy_meters: Optional[Decimal] = None
    photo_path: Optional[str] = None


class BusinessPhotoIn(BaseModel):
    client_id: int
    visit_id: Optional[int] = None
    photo_path: str
    caption: Optional[str] = None
