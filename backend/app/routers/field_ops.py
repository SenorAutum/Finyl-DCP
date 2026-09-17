"""Field operations: GPS trails, daily tasks, home geo, business photos, alerts (Phase 2)."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (require_module, require_permission, get_current_user,
                           get_scope, write_audit)
from app.models import (StaffGpsLog, StaffDailyTask, LocationAlert, ClientHomeGeo,
                        BusinessSitePhoto, DAILY_TASK_STATUSES, Borrower)
from app.schemas import (GpsLogIn, DailyTaskCreate, DailyTaskUpdate, ClientHomeGeoIn,
                         BusinessPhotoIn)
from app.services import gps_tracker

router = APIRouter(prefix="/api/v1/field", tags=["field-ops"])


# --- GPS -------------------------------------------------------------------
@router.post("/gps-log")
def submit_gps(body: GpsLogIn, tenant_id: int = Depends(require_module("lending")),
               user=Depends(get_current_user), db: Session = Depends(get_db)):
    res = gps_tracker.ingest_ping(
        db, tenant_id=tenant_id, user_id=user.id, latitude=body.latitude,
        longitude=body.longitude, accuracy_meters=body.accuracy_meters,
        task_type=body.task_type, task_ref_id=body.task_ref_id, device_id=body.device_id)
    return res


@router.get("/gps-trail")
def gps_trail(user_id: int, day: date | None = None,
              tenant_id: int = Depends(require_module("lending")),
              viewer=Depends(require_permission("field_ops.gps_view")),
              db: Session = Depends(get_db)):
    day = day or date.today()
    rows = (db.query(StaffGpsLog)
            .filter(StaffGpsLog.tenant_id == tenant_id, StaffGpsLog.user_id == user_id)
            .order_by(StaffGpsLog.timestamp.asc()).all())
    pts = [r for r in rows if r.timestamp and r.timestamp.date() == day]
    km = gps_tracker.daily_distance_km(db, tenant_id=tenant_id, user_id=user_id, day=day)
    return {"user_id": user_id, "date": day.isoformat(), "distance_km": km,
            "points": [{"lat": float(p.latitude), "lng": float(p.longitude),
                        "accuracy_meters": float(p.accuracy_meters) if p.accuracy_meters else None,
                        "timestamp": p.timestamp, "task_type": p.task_type} for p in pts]}


# --- Daily tasks -----------------------------------------------------------
def _task_dict(t: StaffDailyTask) -> dict:
    return {"id": t.id, "user_id": t.user_id, "task_date": t.task_date,
            "planned_visits": t.planned_visits, "actual_visits": t.actual_visits,
            "distance_km": float(t.distance_km) if t.distance_km else None,
            "stipend_kes": float(t.stipend_kes) if t.stipend_kes else None,
            "status": t.status}


@router.post("/daily-tasks")
def create_task(body: DailyTaskCreate, tenant_id: int = Depends(require_module("lending")),
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    uid = body.user_id or user.id
    existing = (db.query(StaffDailyTask)
                .filter(StaffDailyTask.tenant_id == tenant_id,
                        StaffDailyTask.user_id == uid,
                        StaffDailyTask.task_date == body.task_date).first())
    if existing:
        existing.planned_visits = body.planned_visits
        db.commit()
        return _task_dict(existing)
    t = StaffDailyTask(tenant_id=tenant_id, user_id=uid, task_date=body.task_date,
                       planned_visits=body.planned_visits, status="planned")
    db.add(t)
    db.commit()
    return _task_dict(t)


@router.get("/daily-tasks")
def list_tasks(user_id: int = 0, day: date | None = None,
               tenant_id: int = Depends(require_module("lending")),
               user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(StaffDailyTask).filter(StaffDailyTask.tenant_id == tenant_id)
    # Officers see their own; managers may pass user_id.
    if user_id:
        q = q.filter(StaffDailyTask.user_id == user_id)
    else:
        q = q.filter(StaffDailyTask.user_id == user.id)
    if day:
        q = q.filter(StaffDailyTask.task_date == day)
    rows = q.order_by(StaffDailyTask.task_date.desc()).all()
    return {"items": [_task_dict(t) for t in rows]}


@router.put("/daily-tasks/{task_id}")
def update_task(task_id: int, body: DailyTaskUpdate,
                tenant_id: int = Depends(require_module("lending")),
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = (db.query(StaffDailyTask)
         .filter(StaffDailyTask.id == task_id, StaffDailyTask.tenant_id == tenant_id).first())
    if not t:
        raise HTTPException(404, "Task not found")
    if body.status and body.status not in DAILY_TASK_STATUSES:
        raise HTTPException(400, "Invalid status")
    if body.planned_visits is not None:
        t.planned_visits = body.planned_visits
    if body.actual_visits is not None:
        t.actual_visits = body.actual_visits
    if body.status:
        t.status = body.status
    db.commit()
    return _task_dict(t)


# --- Client home geo + business photos -------------------------------------
@router.post("/client-home-geo")
def capture_home_geo(body: ClientHomeGeoIn, tenant_id: int = Depends(require_module("clients")),
                     user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(Borrower).filter(Borrower.id == body.client_id,
                                     Borrower.tenant_id == tenant_id).first():
        raise HTTPException(404, "Client not found")
    row = ClientHomeGeo(tenant_id=tenant_id, client_id=body.client_id,
                        latitude=body.latitude, longitude=body.longitude,
                        accuracy_meters=body.accuracy_meters, photo_path=body.photo_path,
                        captured_by=user.id)
    db.add(row)
    db.commit()
    return {"id": row.id, "client_id": row.client_id, "captured_at": row.captured_at}


@router.post("/business-photos")
def upload_business_photo(body: BusinessPhotoIn, tenant_id: int = Depends(require_module("clients")),
                          user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(Borrower).filter(Borrower.id == body.client_id,
                                     Borrower.tenant_id == tenant_id).first():
        raise HTTPException(404, "Client not found")
    row = BusinessSitePhoto(tenant_id=tenant_id, client_id=body.client_id,
                            visit_id=body.visit_id, photo_path=body.photo_path,
                            caption=body.caption, uploaded_by=user.id)
    db.add(row)
    db.commit()
    return {"id": row.id, "client_id": row.client_id, "uploaded_at": row.uploaded_at}


# --- Location alerts -------------------------------------------------------
@router.get("/location-alerts")
def list_alerts(acknowledged: bool | None = None,
                tenant_id: int = Depends(require_module("lending")),
                viewer=Depends(require_permission("field_ops.gps_view")),
                db: Session = Depends(get_db)):
    q = db.query(LocationAlert).filter(LocationAlert.tenant_id == tenant_id)
    if acknowledged is True:
        q = q.filter(LocationAlert.acknowledged_at.isnot(None))
    elif acknowledged is False:
        q = q.filter(LocationAlert.acknowledged_at.is_(None))
    rows = q.order_by(LocationAlert.triggered_at.desc()).all()
    return {"items": [{"id": a.id, "user_id": a.user_id, "alert_type": a.alert_type,
                       "detail": a.detail, "triggered_at": a.triggered_at,
                       "acknowledged_at": a.acknowledged_at} for a in rows]}


@router.post("/location-alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, tenant_id: int = Depends(require_module("lending")),
                      viewer=Depends(require_permission("field_ops.gps_view")),
                      db: Session = Depends(get_db)):
    a = (db.query(LocationAlert)
         .filter(LocationAlert.id == alert_id, LocationAlert.tenant_id == tenant_id).first())
    if not a:
        raise HTTPException(404, "Alert not found")
    a.acknowledged_by = viewer.id
    a.acknowledged_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "acknowledged", "id": a.id}
