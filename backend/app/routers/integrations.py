"""DCP Setup / Integrations (System Admin only).

Surfaces the live status of every external integration and provides
'Test connection' actions. Nothing here fabricates success: each service
reports LIVE / SANDBOX / NOT CONFIGURED from its own credential state.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_role
from app.models import MODULE_KEYS, Tenant, TenantModule, User
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
                    "environment": settings.DARAJA_ENV,
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
