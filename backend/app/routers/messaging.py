"""
Per-DCP customizable SMS messaging.

Each tenant (DCP) can tailor the SMS wording sent at every loan-lifecycle event
(loan qualified, disbursed, repayment reminder, overdue alert, defaulted, payment
receipt). Bodies use {{placeholder}} tokens rendered from a per-event context.

Access model (mirrors the approver-config surface):
  * super_admin may manage ANY DCP by supplying ?tenant_id= (falls back to the
    X-Tenant-Id header, then their own tenant);
  * tenant admins (tenant_admin / system_admin — anyone holding messaging.manage)
    may manage ONLY their own tenant.
All mutations are audited.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, write_audit
from app.core.permissions import has_permission
from app.core.config import settings
from app.models import SmsTemplate, Tenant, User, SmsAutomationSetting, SmsOptOut
from app.schemas import (MessageTemplateIn, MessagePreviewIn, MessageTestIn,
                         SmsAutomationIn, OptOutIn)
from app.services import sms
from app.routers.notifications import get_automation_config, run_tenant_jobs

router = APIRouter(prefix="/api/v1/messaging", tags=["messaging"])

_PERM = "messaging.manage"


class MsgCtx:
    """Resolved (user, tenant_id) for a messaging request."""
    def __init__(self, user: User, tenant_id: int):
        self.user = user
        self.tenant_id = tenant_id


def messaging_ctx(tenant_id: int | None = None,
                  user: User = Depends(get_current_user),
                  x_tenant_id: str | None = Header(default=None),
                  db: Session = Depends(get_db)) -> MsgCtx:
    """Gate on messaging.manage and resolve the effective tenant.

    super_admin picks the DCP via ?tenant_id= (or X-Tenant-Id, or own tenant, or
    — so the picker can bootstrap — the first tenant); every other permitted role
    is pinned to its own tenant regardless of any tenant_id supplied.
    """
    if user.role != "super_admin":
        # DCP admins (any role holding messaging.manage) may manage their OWN
        # tenant's SMS templates; they can never target another tenant.
        from app.services.authz import effective_permissions
        if "messaging.manage" not in effective_permissions(db, user.tenant_id, user.role):
            raise HTTPException(403, "You do not have permission to manage messaging")
        if user.tenant_id is None:
            raise HTTPException(400, "No tenant context")
        return MsgCtx(user, int(user.tenant_id))
    tid = tenant_id
    if tid is None and x_tenant_id:
        try:
            tid = int(x_tenant_id)
        except ValueError:
            tid = None
    if tid is None:
        tid = user.tenant_id
    if tid is None:
        first = db.query(Tenant).order_by(Tenant.id).first()
        tid = first.id if first else None
    if tid is None:
        raise HTTPException(400, "No tenants exist to configure")
    return MsgCtx(user, int(tid))


def _sample_context(db: Session, tenant_id: int) -> dict:
    """Representative values used to render previews / test sends."""
    return {
        "first_name": "Jane",
        "last_name": "Wanjiku",
        "amount": "25,000",
        "due_date": "2026-09-15",
        "balance": "12,500",
        "account_number": f"FL/FY2026/{tenant_id}/42",
        "days_left": "3",
        "dcp_name": sms.dcp_name(db, tenant_id),
        "loan_ref": "QGR7XK2ABC",
    }


def _tenant_list(db: Session) -> list[dict]:
    return [{"id": t.id, "name": t.name, "code": t.code, "active": t.active}
            for t in db.query(Tenant).order_by(Tenant.id)]


# ---------------------------------------------------------------------------
# List — every event, merging defaults for any the tenant has not customised.
# ---------------------------------------------------------------------------
@router.get("/templates")
def list_templates(ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db)):
    rows = {r.event_key: r for r in db.query(SmsTemplate)
            .filter(SmsTemplate.tenant_id == ctx.tenant_id).all()}
    templates = []
    for ek in sms.EVENT_KEYS:
        row = rows.get(ek)
        if row is not None:
            templates.append({
                "event_key": ek, "label": sms.EVENT_LABELS.get(ek, ek),
                "body": row.body, "active": bool(row.active),
                "source": "custom",
                "default_body": sms.DEFAULT_TEMPLATES.get(ek, ""),
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            })
        else:
            templates.append({
                "event_key": ek, "label": sms.EVENT_LABELS.get(ek, ek),
                "body": sms.DEFAULT_TEMPLATES.get(ek, ""), "active": True,
                "source": "default",
                "default_body": sms.DEFAULT_TEMPLATES.get(ek, ""),
                "updated_at": None,
            })
    out = {
        "tenant_id": ctx.tenant_id,
        "templates": templates,
        "variables": [{"token": k, "description": v}
                      for k, v in sms.CANONICAL_PLACEHOLDERS.items()],
        "sample_context": _sample_context(db, ctx.tenant_id),
    }
    # Super admin also gets the DCP list to drive the picker.
    if ctx.user.role == "super_admin":
        out["tenants"] = _tenant_list(db)
    return out


# ---------------------------------------------------------------------------
# Variables reference (placeholders + event keys with labels).
# ---------------------------------------------------------------------------
@router.get("/variables")
def list_variables(ctx: MsgCtx = Depends(messaging_ctx)):
    return {
        "variables": [{"token": k, "description": v}
                      for k, v in sms.CANONICAL_PLACEHOLDERS.items()],
        "events": [{"event_key": k, "label": sms.EVENT_LABELS.get(k, k)}
                   for k in sms.EVENT_KEYS],
    }


# ---------------------------------------------------------------------------
# Upsert a single event template (body + active).
# ---------------------------------------------------------------------------
@router.put("/templates/{event_key}")
def upsert_template(event_key: str, body: MessageTemplateIn, request: Request,
                    ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db)):
    if event_key not in sms.EVENT_KEYS:
        raise HTTPException(400, f"event_key must be one of {sms.EVENT_KEYS}")
    if not (body.body or "").strip():
        raise HTTPException(400, "Template body cannot be empty")
    row = (db.query(SmsTemplate)
           .filter(SmsTemplate.tenant_id == ctx.tenant_id,
                   SmsTemplate.event_key == event_key).first())
    if row:
        row.body = body.body
        row.active = body.active
    else:
        row = SmsTemplate(tenant_id=ctx.tenant_id, event_key=event_key,
                          body=body.body, active=body.active)
        db.add(row)
    db.flush()
    write_audit(db, tenant_id=ctx.tenant_id, user=ctx.user, action="messaging.template_set",
                entity_type="sms_template", entity_id=row.id,
                details={"event_key": event_key, "active": body.active}, request=request)
    db.commit()
    return {"event_key": event_key, "body": row.body, "active": bool(row.active),
            "source": "custom",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


# ---------------------------------------------------------------------------
# Preview — render the submitted (or stored/default) body against sample data.
# ---------------------------------------------------------------------------
@router.post("/templates/{event_key}/preview")
def preview_template(event_key: str, body: MessagePreviewIn,
                     ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db)):
    if event_key not in sms.EVENT_KEYS:
        raise HTTPException(400, f"event_key must be one of {sms.EVENT_KEYS}")
    context = _sample_context(db, ctx.tenant_id)
    if body.body is not None:
        source_body = body.body
    else:
        source_body, _ = sms.get_template(db, ctx.tenant_id, event_key)
    return {"event_key": event_key, "rendered": sms.render_body(source_body, context),
            "sample_context": context}


# ---------------------------------------------------------------------------
# Send-test — dispatch a REAL SMS of the rendered template to a supplied phone.
# ---------------------------------------------------------------------------
@router.post("/templates/{event_key}/send-test")
def send_test(event_key: str, body: MessageTestIn, request: Request,
              ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db)):
    if event_key not in sms.EVENT_KEYS:
        raise HTTPException(400, f"event_key must be one of {sms.EVENT_KEYS}")
    if not (body.phone or "").strip():
        raise HTTPException(400, "A destination phone number is required")
    context = _sample_context(db, ctx.tenant_id)
    if body.body is not None:
        source_body = body.body
    else:
        source_body, _ = sms.get_template(db, ctx.tenant_id, event_key)
    rendered = sms.render_body(source_body, context)
    log = sms.send_sms(db, ctx.tenant_id, body.phone, rendered, f"test_{event_key}")
    write_audit(db, tenant_id=ctx.tenant_id, user=ctx.user, action="messaging.send_test",
                entity_type="sms_template", entity_id=event_key,
                details={"phone": body.phone, "status": log.status}, request=request)
    db.commit()
    return {"event_key": event_key, "status": log.status, "message": rendered,
            "sms_log_id": log.id, "error": log.error}


# ===========================================================================
# Automation — per-DCP switch for the daily lifecycle-SMS batch.
# ===========================================================================
@router.get("/automation")
def get_automation(ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db)):
    """Current tenant's automation config (falls back to defaults if unset)."""
    cfg = get_automation_config(db, ctx.tenant_id)
    out = {**cfg, "timezone_note": "send_hour is an hour (0-23) in the server timezone."}
    if ctx.user.role == "super_admin":
        out["tenants"] = _tenant_list(db)
    return out


@router.put("/automation")
def set_automation(body: SmsAutomationIn, request: Request,
                   ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db)):
    """Upsert automation_enabled + send_hour for the tenant."""
    if not 0 <= body.send_hour <= 23:
        raise HTTPException(400, "send_hour must be between 0 and 23")
    row = (db.query(SmsAutomationSetting)
           .filter(SmsAutomationSetting.tenant_id == ctx.tenant_id).first())
    if row:
        row.automation_enabled = body.automation_enabled
        row.send_hour = body.send_hour
        row.updated_by_user_id = ctx.user.id
    else:
        row = SmsAutomationSetting(tenant_id=ctx.tenant_id,
                                   automation_enabled=body.automation_enabled,
                                   send_hour=body.send_hour,
                                   updated_by_user_id=ctx.user.id)
        db.add(row)
    db.flush()
    write_audit(db, tenant_id=ctx.tenant_id, user=ctx.user, action="messaging.automation_set",
                entity_type="sms_automation", entity_id=row.id,
                details={"automation_enabled": body.automation_enabled,
                         "send_hour": body.send_hour}, request=request)
    db.commit()
    return {"tenant_id": ctx.tenant_id,
            "automation_enabled": bool(row.automation_enabled),
            "send_hour": int(row.send_hour), "source": "custom",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


@router.post("/automation/run-now")
def run_now(request: Request, grace_days: int = 30,
            ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db)):
    """Manually run the lifecycle-SMS batch (reminders + overdue alerts +
    defaulting) for the CURRENT tenant only. Dispatches REAL SMS."""
    counts = run_tenant_jobs(db, ctx.tenant_id, grace_days)
    write_audit(db, tenant_id=ctx.tenant_id, user=ctx.user, action="messaging.automation_run_now",
                entity_type="sms_automation", entity_id=ctx.tenant_id,
                details=counts, request=request)
    db.commit()
    return {"tenant_id": ctx.tenant_id, **counts}



# ---------------------------------------------------------------------------
# SMS opt-out register — suppress non-transactional SMS for a phone number
# ---------------------------------------------------------------------------
@router.get("/opt-outs")
def list_opt_outs(ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db),
                  active_only: bool = True):
    """List the tenant's SMS opt-out register."""
    q = db.query(SmsOptOut).filter(SmsOptOut.tenant_id == ctx.tenant_id)
    if active_only:
        q = q.filter(SmsOptOut.active.is_(True))
    rows = q.order_by(SmsOptOut.id.desc()).all()
    return {"tenant_id": ctx.tenant_id, "count": len(rows), "items": [{
        "id": r.id, "phone": r.phone, "source": r.source,
        "active": bool(r.active),
        "opted_out_at": r.opted_out_at.isoformat() if r.opted_out_at else None,
    } for r in rows]}


@router.post("/opt-outs")
def add_opt_out(body: OptOutIn, request: Request,
                ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db)):
    """Add (or re-activate) a phone number in the opt-out register."""
    phone = sms.normalise_phone(body.phone)
    row = (db.query(SmsOptOut)
           .filter(SmsOptOut.tenant_id == ctx.tenant_id,
                   SmsOptOut.phone == phone).first())
    if row:
        row.active = True
        row.source = body.source
    else:
        row = SmsOptOut(tenant_id=ctx.tenant_id, phone=phone,
                        source=body.source, active=True)
        db.add(row)
    db.flush()
    write_audit(db, tenant_id=ctx.tenant_id, user=ctx.user, action="messaging.opt_out_add",
                entity_type="sms_opt_out", entity_id=row.id,
                details={"phone": phone, "source": body.source}, request=request)
    db.commit()
    return {"id": row.id, "phone": row.phone, "active": True, "source": row.source}


@router.delete("/opt-outs/{phone}")
def remove_opt_out(phone: str, request: Request,
                   ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db)):
    """Remove a phone from the opt-out register (re-enable non-transactional SMS)."""
    norm = sms.normalise_phone(phone)
    row = (db.query(SmsOptOut)
           .filter(SmsOptOut.tenant_id == ctx.tenant_id,
                   SmsOptOut.phone == norm).first())
    if not row:
        raise HTTPException(404, "Opt-out entry not found")
    row.active = False
    db.flush()
    write_audit(db, tenant_id=ctx.tenant_id, user=ctx.user, action="messaging.opt_out_remove",
                entity_type="sms_opt_out", entity_id=row.id,
                details={"phone": norm}, request=request)
    db.commit()
    return {"phone": norm, "active": False}


@router.get("/status")
def messaging_status(ctx: MsgCtx = Depends(messaging_ctx), db: Session = Depends(get_db)):
    """Messaging configuration/status: sender ID and opt-out enforcement summary."""
    active_opt_outs = (db.query(SmsOptOut)
                       .filter(SmsOptOut.tenant_id == ctx.tenant_id,
                               SmsOptOut.active.is_(True)).count())
    return {
        "tenant_id": ctx.tenant_id,
        "sender_id": settings.UWAZII_SENDER_ID,
        "provider": sms.PROVIDER_NAME,
        "opt_out_enforcement": True,
        "suppressible_events": sorted(sms.SUPPRESSIBLE_EVENTS),
        "active_opt_out_count": active_opt_outs,
    }
