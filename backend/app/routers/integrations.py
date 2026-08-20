"""DCP Setup / Integrations (System Admin only).

Surfaces the live status of every external integration and provides
'Test connection' actions. Nothing here fabricates success: each service
reports LIVE / SANDBOX / NOT CONFIGURED from its own credential state.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_role, get_current_user, get_tenant_id
from app.models import (MODULE_KEYS, Tenant, TenantModule, User, SmsLog,
                        SmsRateCard, IntegrationTestLog)
from app.services import sms, mpesa, ekyc, crb

router = APIRouter(
    prefix="/api/v1/integrations",
    tags=["integrations"],
    dependencies=[Depends(require_role("super_admin"))],
)


def _mask(value: str | None) -> str:
    """Show only that a secret is present, never its value."""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 4:
        return "••••"
    return "••••" + v[-4:]


@router.get("/status")
def integrations_status(db: Session = Depends(get_db)):
    """Per-integration status + non-secret config for the DCP Setup screen."""
    # CBK Reporting is a per-tenant feature flag, not an external credential.
    cbk_rows = (db.query(TenantModule, Tenant)
                .join(Tenant, Tenant.id == TenantModule.tenant_id)
                .filter(TenantModule.module_key == "cbk_reporting")
                .order_by(Tenant.id).all())
    cbk_tenants = [{
        "tenant_id": t.id, "tenant": t.name, "code": t.code,
        "enabled": bool(m.enabled),
    } for m, t in cbk_rows]

    return {
        "integrations": [
            {
                "key": "sms",
                "name": "Uwazii SMS",
                "category": "Notifications",
                "status": sms.integration_status(),
                "config": {
                    "provider": "uwazii",
                    "base_url": settings.UWAZII_BASE_URL,
                    "sender_id": settings.UWAZII_SENDER_ID or "",
                    "access_token": _mask(settings.UWAZII_ACCESS_TOKEN),
                },
            },
            {
                "key": "mpesa",
                "name": "M-Pesa (Daraja)",
                "category": "Payments",
                "status": mpesa.integration_status(),
                "config": {
                    "environment": settings.DARAJA_ENVIRONMENT,
                    "shortcode": settings.DARAJA_SHORTCODE or "",
                    "initiator_name": settings.DARAJA_INITIATOR_NAME or "",
                    "consumer_key": _mask(settings.DARAJA_CONSUMER_KEY),
                    "consumer_secret": _mask(settings.DARAJA_CONSUMER_SECRET),
                    "callback_base_url": settings.DARAJA_CALLBACK_BASE_URL,
                },
            },
            {
                "key": "ekyc",
                "name": "eKYC (Creditinfo IDM)",
                "category": "Identity",
                "status": ekyc.integration_status(),
                "config": {
                    "base_url": settings.EKYC_BASE_URL,
                    "username": _mask(settings.EKYC_USERNAME),
                    "strategy_id": settings.EKYC_STRATEGY_ID or "",
                    "mock_mode": bool(settings.EKYC_MOCK),
                },
            },
            {
                "key": "crb",
                "name": "Credit Reference Bureau",
                "category": "Credit",
                "status": crb.integration_status(),
                "config": {
                    "provider": settings.CRB_PROVIDER,
                    "base_url": settings.CRB_BASE_URL,
                    "api_key": _mask(settings.CRB_API_KEY),
                    "username": _mask(settings.CRB_USERNAME),
                },
            },
            {
                "key": "ocr",
                "name": "ID OCR (Vision LLM + Tesseract)",
                "category": "Identity",
                "status": "LIVE" if (settings.LLM_API_KEY or "").strip() else "SANDBOX",
                "config": {
                    "provider": settings.OCR_PROVIDER,
                    "vision_model": settings.LLM_VISION_MODEL or settings.LLM_MODEL,
                    "fallback": "tesseract",
                },
            },
        ],
        "cbk_reporting": {
            "name": "CBK Reporting",
            "category": "Compliance",
            "description": "Per-tenant feature flag. Disabled by default for "
                           "non-compliant DCPs; enable once the tenant is licensed.",
            "tenants": cbk_tenants,
        },
    }


class TestSmsBody(BaseModel):
    phone: str
    message: str = "Finyl DCP test message — Uwazii SMS is live."


@router.post("/test-sms")
def test_sms(body: TestSmsBody, db: Session = Depends(get_db),
             user: User = Depends(require_role("super_admin"))):
    """Send a real SMS through Uwazii to verify the integration."""
    if not sms.is_configured():
        raise HTTPException(422, "Uwazii SMS is not configured — access token required.")
    tenant_id = user.tenant_id or 1
    log = sms.send_sms(db, tenant_id, body.phone, body.message, trigger_type="test")
    db.commit()
    return {
        "ok": (log.status or "").lower() in ("sent", "queued", "success", "delivered"),
        "status": log.status,
        "provider": getattr(log, "provider", None),
        "provider_ref": getattr(log, "provider_ref", None),
        "error": getattr(log, "error", None),
    }


@router.post("/test-mpesa")
def test_mpesa():
    """Attempt a Daraja OAuth token to verify M-Pesa credentials."""
    return mpesa.test_connection()


class TestEkycBody(BaseModel):
    national_id: str = "12345678"
    first_name: str = "Jane"
    last_name: str = "Doe"


@router.post("/test-ekyc")
def test_ekyc(body: TestEkycBody):
    """Verify eKYC configuration. Runs a check only when configured/mock."""
    status = ekyc.integration_status()
    if status == "NOT CONFIGURED":
        return {"ok": False, "status": status,
                "detail": "Creditinfo IDM credentials required."}
    try:
        result = ekyc.verify_identity(
            national_id=body.national_id, first_name=body.first_name,
            last_name=body.last_name)
        return {"ok": True, "status": status,
                "detail": "Identity check executed.",
                "decision": result.get("decision") or result.get("status")}
    except ekyc.EkycNotConfigured as exc:
        return {"ok": False, "status": "NOT CONFIGURED", "detail": str(exc)}
    except Exception as exc:
        return {"ok": False, "status": "ERROR", "detail": str(exc)}


class TestCrbBody(BaseModel):
    national_id: str = "12345678"
    first_name: str = "Jane"
    last_name: str = "Doe"


@router.post("/test-crb")
def test_crb(body: TestCrbBody):
    """Run a CRB check against the configured provider."""
    result = crb.run_check(national_id=body.national_id,
                           first_name=body.first_name, last_name=body.last_name)
    ok = result.get("status") == "ok"
    return {
        "ok": ok,
        "status": result.get("status"),
        "provider": result.get("provider"),
        "detail": result.get("error") or "CRB check executed.",
        "credit_score": result.get("credit_score"),
    }



# ============================================================================
# Integrations registry
# ----------------------------------------------------------------------------
# Adding a NEW integration later = add ONE entry here and implement its
# ``status`` + (optional) ``test`` callables. Nothing else in this module,
# the API surface, or the frontend needs to change.
# ============================================================================
def _sms_config():
    return {
        "provider": "uwazii",
        "base_url": settings.UWAZII_BASE_URL,
        "sender_id": settings.UWAZII_SENDER_ID or "",
        "username": _mask(settings.UWAZII_USERNAME),
        "auth": "two-step (authorize → accesstoken)",
    }


def _mpesa_config():
    return {
        "environment": settings.DARAJA_ENVIRONMENT,
        "shortcode": settings.DARAJA_SHORTCODE or "",
        "initiator_name": settings.DARAJA_INITIATOR_NAME or "",
        "consumer_key": _mask(settings.DARAJA_CONSUMER_KEY),
        "consumer_secret": _mask(settings.DARAJA_CONSUMER_SECRET),
        "callback_base_url": settings.DARAJA_CALLBACK_BASE_URL,
    }


def _ekyc_config():
    return {
        "base_url": settings.EKYC_BASE_URL,
        "username": _mask(settings.EKYC_USERNAME),
        "strategy_id": settings.EKYC_STRATEGY_ID or "",
        "mock_mode": bool(settings.EKYC_MOCK),
    }


def _crb_config():
    return {
        "provider": settings.CRB_PROVIDER,
        "base_url": settings.CRB_BASE_URL,
        "api_key": _mask(settings.CRB_API_KEY),
        "username": _mask(settings.CRB_USERNAME),
    }


# --- Per-integration self-test callables (return {ok, detail, extra...}) -----
def _test_sms(db, user):
    """Two-step auth + a real send to the safe dummy number. Uses the low-level
    dispatch so the connectivity probe is NOT billed to any DCP."""
    if not sms.is_configured():
        return {"ok": False, "detail": "Uwazii credentials not configured."}
    res = sms._dispatch_to_provider("254700000000", "Finyl-DCP integration self-test.")
    ok = res.get("status") == "sent"
    detail = ("Sent — provider_ref %s" % res.get("provider_ref")) if ok else (res.get("error") or "Send failed")
    return {"ok": ok, "detail": detail, "status": res.get("status"),
            "provider_ref": res.get("provider_ref")}


def _test_mpesa(db, user):
    r = mpesa.test_connection()
    ok = bool(r.get("ok"))
    return {"ok": ok, "detail": r.get("detail") or r.get("error") or ("OK" if ok else "Not configured"),
            "raw": r}


def _test_ekyc(db, user):
    status = ekyc.integration_status()
    if status == "NOT CONFIGURED":
        return {"ok": False, "detail": "Creditinfo IDM credentials required."}
    try:
        result = ekyc.verify_identity(national_id="12345678", first_name="Jane", last_name="Doe")
        return {"ok": True, "detail": "Identity check executed.",
                "decision": result.get("decision") or result.get("status")}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _test_crb(db, user):
    result = crb.run_check(national_id="12345678", first_name="Jane", last_name="Doe")
    ok = result.get("status") == "ok"
    return {"ok": ok, "detail": result.get("error") or "CRB check executed.",
            "credit_score": result.get("credit_score"), "provider": result.get("provider")}


REGISTRY = [
    {"key": "uwazii_sms", "name": "Uwazii SMS", "category": "SMS", "provider": "Uwazii Mobile",
     "status": sms.integration_status, "config": _sms_config, "test": _test_sms},
    {"key": "daraja_mpesa", "name": "M-Pesa (Daraja)", "category": "Payments", "provider": "Safaricom Daraja",
     "status": mpesa.integration_status, "config": _mpesa_config, "test": _test_mpesa},
    {"key": "ekyc", "name": "eKYC (Creditinfo IDM)", "category": "Identity", "provider": "Creditinfo",
     "status": ekyc.integration_status, "config": _ekyc_config, "test": _test_ekyc},
    {"key": "crb", "name": "Credit Reference Bureau", "category": "Credit",
     "provider": (settings.CRB_PROVIDER or "metropol").title(),
     "status": crb.integration_status, "config": _crb_config, "test": _test_crb},
    {"key": "ocr", "name": "ID OCR (Vision LLM + Tesseract)", "category": "Identity", "provider": "Vision LLM",
     "status": lambda: ("LIVE" if (settings.LLM_API_KEY or "").strip() else "SANDBOX"),
     "config": lambda: {"provider": settings.OCR_PROVIDER,
                        "vision_model": settings.LLM_VISION_MODEL or settings.LLM_MODEL,
                        "fallback": "tesseract"},
     "test": None},
]
_REGISTRY_BY_KEY = {e["key"]: e for e in REGISTRY}


def _entry_public(entry) -> dict:
    return {
        "key": entry["key"],
        "name": entry["name"],
        "category": entry["category"],
        "provider": entry["provider"],
        "status": entry["status"](),
        "config": entry["config"](),
        "testable": entry.get("test") is not None,
    }


@router.get("")
def list_integrations():
    """Integration registry with live status for each. Super-admin only."""
    return {"integrations": [_entry_public(e) for e in REGISTRY]}


@router.post("/{key}/test")
def run_integration_test(key: str, db: Session = Depends(get_db),
                         user: User = Depends(require_role("super_admin"))):
    """Run a connectivity/self-test for one integration and persist the result."""
    entry = _REGISTRY_BY_KEY.get(key)
    if not entry:
        raise HTTPException(404, f"Unknown integration '{key}'")
    if not entry.get("test"):
        # No live probe — validate configuration instead.
        status = entry["status"]()
        ok = status in ("LIVE", "SANDBOX")
        result = {"ok": ok, "detail": f"Configuration status: {status}"}
    else:
        try:
            result = entry["test"](db, user)
        except Exception as exc:  # never 500 on a test button
            result = {"ok": False, "detail": f"Test raised: {exc}"}

    log = IntegrationTestLog(
        integration_key=key, ok=bool(result.get("ok")),
        detail=str(result.get("detail") or "")[:1000],
        run_by_user_id=getattr(user, "id", None),
        run_by_email=getattr(user, "email", None),
    )
    db.add(log)
    db.commit()
    return {**result, "integration": key, "at": datetime.utcnow().isoformat() + "Z"}


@router.get("/{key}/test-logs")
def integration_test_logs(key: str, limit: int = Query(20, le=100),
                          db: Session = Depends(get_db)):
    """Recent test runs for one integration (most recent first)."""
    if key not in _REGISTRY_BY_KEY:
        raise HTTPException(404, f"Unknown integration '{key}'")
    rows = (db.query(IntegrationTestLog)
            .filter(IntegrationTestLog.integration_key == key)
            .order_by(IntegrationTestLog.created_at.desc()).limit(limit).all())
    return {"logs": [{
        "id": r.id, "ok": r.ok, "detail": r.detail,
        "run_by": r.run_by_email,
        "at": r.created_at.isoformat() + "Z" if r.created_at else None,
    } for r in rows]}


# ============================================================================
# SMS revenue & usage reporting  (RBAC-scoped, separate router — NOT globally
# super-admin gated; each endpoint scopes by role.)
# ============================================================================
reporting_router = APIRouter(prefix="/api/v1/integrations", tags=["integrations-sms"])


def _parse_range(frm: Optional[str], to: Optional[str]):
    """Default to the last 30 days. `to` is inclusive of the whole day."""
    now = datetime.utcnow()
    start = datetime.fromisoformat(frm) if frm else (now - timedelta(days=30))
    end = (datetime.fromisoformat(to) + timedelta(days=1)) if to else (now + timedelta(days=1))
    return start, end


@reporting_router.get("/sms/usage")
def sms_usage(from_: Optional[str] = Query(None, alias="from"),
              to: Optional[str] = Query(None),
              tenant_id: Optional[int] = Query(None),
              trigger_type: Optional[str] = Query(None),
              db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """Per-DCP SMS usage & revenue. Super-admin: all DCPs + platform roll-up.
    Other roles: their own tenant only (revenue based on billable 'sent')."""
    start, end = _parse_range(from_, to)
    is_super = user.role == "super_admin"

    # Portable conditional aggregation via CASE.
    def _count_if(cond):
        return func.coalesce(func.sum(case((cond, 1), else_=0)), 0)

    def _sum_if(cond, col):
        return func.coalesce(func.sum(case((cond, func.coalesce(col, 0)), else_=0)), 0)

    is_sent = SmsLog.status == "sent"
    is_billable = SmsLog.billable == True  # noqa: E712

    sent_i = _count_if(is_sent)
    delivered_i = _count_if(SmsLog.delivery_status == "delivered")
    billable_i = _count_if(is_billable)
    sell_sum = _sum_if(is_billable, SmsLog.sell_price_kes)
    cost_sum = _sum_if(is_billable, SmsLog.cost_price_kes)
    margin_sum = _sum_if(is_billable, SmsLog.margin_kes)

    query = (db.query(
        SmsLog.tenant_id.label("tenant_id"),
        func.count(SmsLog.id).label("total"),
        sent_i.label("sent"),
        delivered_i.label("delivered"),
        billable_i.label("billable"),
        sell_sum.label("sell"),
        cost_sum.label("cost"),
        margin_sum.label("margin"),
    ).filter(SmsLog.sent_at >= start, SmsLog.sent_at < end)
     .group_by(SmsLog.tenant_id))

    if trigger_type:
        query = query.filter(SmsLog.trigger_type == trigger_type)
    if not is_super:
        query = query.filter(SmsLog.tenant_id == user.tenant_id)
    elif tenant_id:
        query = query.filter(SmsLog.tenant_id == tenant_id)

    rows = query.all()
    tmap = {t.id: t for t in db.query(Tenant).all()}

    def _row(r):
        sent = int(r.sent or 0)
        delivered = int(r.delivered or 0)
        t = tmap.get(r.tenant_id)
        return {
            "tenant_id": r.tenant_id,
            "tenant": t.name if t else f"Tenant {r.tenant_id}",
            "code": getattr(t, "code", None),
            "messages_sent": sent,
            "messages_delivered": delivered,
            "delivery_rate": round(delivered / sent, 4) if sent else 0.0,
            "billable_count": int(r.billable or 0),
            "total_sell_kes": float(r.sell or 0),
            "total_cost_kes": float(r.cost or 0),
            "total_margin_kes": float(r.margin or 0),
        }

    per_tenant = sorted((_row(r) for r in rows), key=lambda x: x["total_sell_kes"], reverse=True)
    rollup = {
        "messages_sent": sum(x["messages_sent"] for x in per_tenant),
        "messages_delivered": sum(x["messages_delivered"] for x in per_tenant),
        "billable_count": sum(x["billable_count"] for x in per_tenant),
        "total_sell_kes": round(sum(x["total_sell_kes"] for x in per_tenant), 4),
        "total_cost_kes": round(sum(x["total_cost_kes"] for x in per_tenant), 4),
        "total_margin_kes": round(sum(x["total_margin_kes"] for x in per_tenant), 4),
    }
    sent_total = rollup["messages_sent"]
    rollup["delivery_rate"] = round(rollup["messages_delivered"] / sent_total, 4) if sent_total else 0.0

    return {
        "scope": "platform" if is_super else "tenant",
        "from": start.date().isoformat(),
        "to": (end - timedelta(days=1)).date().isoformat(),
        "rows": per_tenant,
        "rollup": rollup,
    }


@reporting_router.get("/sms/logs")
def sms_logs(from_: Optional[str] = Query(None, alias="from"),
             to: Optional[str] = Query(None),
             tenant_id: Optional[int] = Query(None),
             status: Optional[str] = Query(None),
             delivery_status: Optional[str] = Query(None),
             trigger_type: Optional[str] = Query(None),
             phone: Optional[str] = Query(None),
             page: int = Query(1, ge=1),
             page_size: int = Query(25, le=100),
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """Paginated message logs. Super-admin: cross-tenant (optional filter).
    Other roles: auto-scoped to their own tenant."""
    start, end = _parse_range(from_, to)
    is_super = user.role == "super_admin"

    q = db.query(SmsLog).filter(SmsLog.sent_at >= start, SmsLog.sent_at < end)
    if not is_super:
        q = q.filter(SmsLog.tenant_id == user.tenant_id)
    elif tenant_id:
        q = q.filter(SmsLog.tenant_id == tenant_id)
    if status:
        q = q.filter(SmsLog.status == status)
    if delivery_status:
        q = q.filter(SmsLog.delivery_status == delivery_status)
    if trigger_type:
        q = q.filter(SmsLog.trigger_type == trigger_type)
    if phone:
        q = q.filter(SmsLog.recipient_phone.ilike(f"%{phone}%"))

    total = q.count()
    rows = (q.order_by(SmsLog.sent_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    tmap = {t.id: t for t in db.query(Tenant).all()}

    return {
        "total": total, "page": page, "page_size": page_size,
        "rows": [{
            "id": r.id,
            "tenant_id": r.tenant_id,
            "tenant": (tmap.get(r.tenant_id).name if tmap.get(r.tenant_id) else None),
            "recipient_phone": r.recipient_phone,
            "message": r.message,
            "trigger_type": r.trigger_type,
            "status": r.status,
            "delivery_status": r.delivery_status,
            "provider": r.provider,
            "provider_ref": r.provider_ref,
            "billable": bool(r.billable),
            "sell_price_kes": float(r.sell_price_kes) if r.sell_price_kes is not None else None,
            "margin_kes": float(r.margin_kes) if r.margin_kes is not None else None,
            "sent_at": r.sent_at.isoformat() + "Z" if r.sent_at else None,
            "delivered_at": r.delivered_at.isoformat() + "Z" if r.delivered_at else None,
        } for r in rows],
    }


@reporting_router.get("/sms/rate")
def sms_rate(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Current active SMS rate (read-only, for dashboard display)."""
    row = (db.query(SmsRateCard).filter(SmsRateCard.active == True)  # noqa: E712
           .order_by(SmsRateCard.effective_from.desc()).first())
    if not row:
        return {"configured": False}
    return {"configured": True, "currency": row.currency,
            "sell_price_kes": float(row.sell_price_kes),
            "cost_price_kes": float(row.cost_price_kes),
            "margin_kes": float((row.sell_price_kes or 0) - (row.cost_price_kes or 0))}


# ============================================================================
# Uwazii delivery-report (DLR) callback receiver — UNAUTHENTICATED webhook.
# Defensive: accepts JSON or form; matches SmsLog by provider_ref; only updates
# matching rows; ignores unknown ids; never errors out.
# ============================================================================
webhook_router = APIRouter(tags=["webhooks"])

_DELIVERED = {"delivered", "delivrd", "success", "dlvrd", "1", "delivered_to_handset"}
_FAILED = {"failed", "undeliverable", "rejected", "expired", "error", "0"}
_UNDELIVERED = {"undelivered", "undeliv"}


def _norm_delivery_status(raw) -> str:
    s = str(raw or "").strip().lower()
    if s in _DELIVERED:
        return "delivered"
    if s in _UNDELIVERED:
        return "undelivered"
    if s in _FAILED:
        return "failed"
    # substring fallbacks
    if "deliv" in s and "un" not in s:
        return "delivered"
    if "fail" in s or "reject" in s or "expire" in s:
        return "failed"
    return "unknown"


async def _read_payload(request: Request) -> dict:
    """Parse JSON or form-encoded body defensively; also fold in query params."""
    data: dict = {}
    try:
        ctype = request.headers.get("content-type", "")
        if "application/json" in ctype:
            body = await request.json()
            if isinstance(body, dict):
                data.update(body)
            elif isinstance(body, list) and body and isinstance(body[0], dict):
                data.update(body[0])
        else:
            form = await request.form()
            data.update({k: v for k, v in form.items()})
    except Exception:
        pass
    try:
        data.update({k: v for k, v in request.query_params.items()})
    except Exception:
        pass
    return data


async def _handle_dlr(request: Request, db: Session):
    payload = await _read_payload(request)
    # Find a message id under any of the common keys.
    ref = None
    for k in ("id_state", "message_id", "messageId", "id", "msgid", "smsId", "reference"):
        if payload.get(k) not in (None, ""):
            ref = str(payload.get(k))
            break
    # Find a status string under any of the common keys.
    status_raw = None
    for k in ("status", "dlr", "delivery_status", "state", "deliveryStatus", "status_desc"):
        if payload.get(k) not in (None, ""):
            status_raw = payload.get(k)
            break

    result = {"received": True, "matched": False}
    if not ref:
        return result
    try:
        log = (db.query(SmsLog).filter(SmsLog.provider_ref == ref)
               .order_by(SmsLog.id.desc()).first())
        if log:
            new_status = _norm_delivery_status(status_raw)
            log.delivery_status = new_status
            if new_status == "delivered" and not log.delivered_at:
                log.delivered_at = datetime.utcnow()
            db.commit()
            result.update({"matched": True, "provider_ref": ref, "delivery_status": new_status})
    except Exception:
        db.rollback()
    return result


@webhook_router.post("/api/v1/integrations/sms/dlr")
async def sms_dlr(request: Request, db: Session = Depends(get_db)):
    """Uwazii delivery-report callback (configure this URL in the Uwazii account)."""
    return await _handle_dlr(request, db)


# no-/api alias in case a proxy strips the prefix (harmless duplicate route).
@webhook_router.post("/integrations/sms/dlr")
async def sms_dlr_alias(request: Request, db: Session = Depends(get_db)):
    return await _handle_dlr(request, db)
