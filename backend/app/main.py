"""
Finyl-DCP — FastAPI application entrypoint.

Multi-tenant SaaS for Digital Credit Providers (Kenya).
Modules: lending, payments, dashboard, complaints, crm, call_center, impact,
cbk_reporting, ai_agent — each gated per-tenant via feature flags.
"""
import jwt as pyjwt
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine, ensure_schema
from app.core.obs import configure_logging
from app.core.security import decode_token
from app import models  # noqa: F401 — register all tables on Base.metadata
from app.routers import (admin, ai, auth, call_center, cbk, clients, complaints, crm,
                         dashboard, impact, lending, notifications, payments,
                         access, approvals, reporting, integrations, messaging,
                         accounting)
from app.routers import settings as dcp_settings
# Phase 2 routers
from app.routers import (guarantors, kyc_escalations, face_validation,
                         validation_prefs, field_ops, security_config, activity,
                         collections, third_party, client_edits, search)

# OPS-01: configure structured stdout/journald logging before the app is built.
configure_logging()

app = FastAPI(
    title="Finyl-DCP API",
    version="1.0.0",
    description="Multi-tenant Digital Credit Provider platform — lending engine, "
                "M-Pesa integration hub (live Daraja), executive analytics, consumer "
                "protection, CRM, call center, social impact and CBK compliance.",
    # API-02: never expose interactive API docs / schema publicly.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# API-01: lock CORS to an explicit allowlist of origins. The SPA is served
# same-origin behind nginx (so browser XHR does not even trigger CORS); the
# allowlist is env-driven (settings.ALLOWED_ORIGINS, comma-separated) so the app
# is portable to other hosts (Netlify, custom domains, etc.) WITHOUT code
# changes, while defaulting to the live Abacus production origin. An explicit
# allowlist replaces the previous wildcard, which is invalid with credentials.
_DEFAULT_ORIGIN = "https://finyl-dcp.abacusai.cloud"
_ALLOWED_ORIGINS = []
for _origin in (settings.ALLOWED_ORIGINS or "").split(","):
    _origin = _origin.strip()
    if _origin and _origin not in _ALLOWED_ORIGINS:
        _ALLOWED_ORIGINS.append(_origin)
# Never end up with an empty list (which would silently block the SPA) and never
# use a wildcard together with allow_credentials — fall back to the Abacus origin.
if not _ALLOWED_ORIGINS:
    _ALLOWED_ORIGINS = [_DEFAULT_ORIGIN]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- AUTH-03: server-side forced-password-reset gate -------------------------
# When an authenticated user has force_password_reset=True, every endpoint is
# blocked with 403 {"detail": {"code": "password_reset_required"}} EXCEPT the
# handful needed to actually change the password / inspect the session / log out.
# The frontend detects this code and routes the user to the change-password
# screen. Enforcement is server-side so it cannot be bypassed by the client.
_RESET_ALLOWLIST = {
    "/api/health",
    "/api/v1/auth/me",
    "/api/v1/auth/change-password",
    "/api/v1/auth/logout",
    "/api/v1/auth/login",
    "/api/v1/auth/login/form",
    "/docs", "/openapi.json", "/redoc",
}


@app.middleware("http")
async def enforce_password_reset(request: Request, call_next):
    path = request.url.path
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and path not in _RESET_ALLOWLIST:
        token = auth.split(" ", 1)[1].strip()
        try:
            payload = decode_token(token)
        except pyjwt.PyJWTError:
            payload = None  # let the normal dependency return 401
        if payload is not None:
            db = SessionLocal()
            try:
                user = db.get(models.User, int(payload.get("sub", 0)))
                if user is not None and getattr(user, "force_password_reset", False):
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": {
                            "code": "password_reset_required",
                            "message": "Password reset required. Please change "
                                       "your password before continuing.",
                        }},
                    )
            finally:
                db.close()
    return await call_next(request)


# --- Phase 2: server-side activity-logging middleware ------------------------
# Records every authenticated, state-changing API call (POST/PUT/PATCH/DELETE)
# into activity_logs, honouring each tenant's activity_log_enabled flag. This is
# the server-side companion to the client-driven /api/v1/activity/log endpoint and
# gives an immutable, tamper-evident trail of who changed what (CBK mandate).
# It runs AFTER the handler so the real response status is captured, and never
# blocks or fails the underlying request.
_ACTIVITY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_ACTIVITY_SKIP_PREFIXES = ("/api/v1/activity",)  # avoid logging the log ingest itself


@app.middleware("http")
async def record_activity(request: Request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path
        method = request.method.upper()
        auth = request.headers.get("authorization", "")
        if (method in _ACTIVITY_METHODS and path.startswith("/api/v1/")
                and not path.startswith(_ACTIVITY_SKIP_PREFIXES)
                and auth.lower().startswith("bearer ")):
            token = auth.split(" ", 1)[1].strip()
            try:
                payload = decode_token(token)
            except pyjwt.PyJWTError:
                payload = None
            if payload is not None:
                user_id = int(payload.get("sub", 0))
                tenant_id = payload.get("tenant_id")
                if user_id and tenant_id:
                    xff = request.headers.get("x-forwarded-for")
                    ip = xff.split(",")[0].strip() if xff else (
                        request.client.host if request.client else None)
                    fp = request.headers.get("x-device-fingerprint")
                    db = SessionLocal()
                    try:
                        from app.services import activity as activity_svc
                        activity_svc.record_request(
                            db, tenant_id=int(tenant_id), user_id=user_id,
                            method=method, path=path,
                            status_code=getattr(response, "status_code", 0),
                            ip=ip, device_fingerprint=fp)
                    finally:
                        db.close()
    except Exception:  # pragma: no cover — never break a request over logging
        pass
    return response


@app.on_event("startup")
def on_startup():
    """Ensure the schema exists. Table DDL is owned by migrations/*.sql — API-04:
    `Base.metadata.create_all` runs ONLY when AUTO_CREATE_TABLES is explicitly
    enabled (throwaway/dev DB), never in production, to avoid silent schema drift."""
    ensure_schema()
    if settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)
    # Log the active Daraja (M-Pesa) environment on boot — SECRET-FREE (only the
    # environment name and yes/no configured booleans). Makes it obvious in the
    # journal whether the service came up in sandbox or production.
    import logging
    from app.services import mpesa
    logging.getLogger("finyl.startup").info(mpesa.startup_summary())
    # In-process auto-reconcile worker (APScheduler). Guarded so a missing
    # dependency / disabled flag never blocks app boot.
    try:
        from app.services.scheduler import start_scheduler
        start_scheduler()
    except Exception:  # pragma: no cover
        import logging
        logging.getLogger("finyl.scheduler").exception(
            "start_scheduler failed — app continues without the worker")


@app.on_event("shutdown")
def on_shutdown():
    try:
        from app.services.scheduler import shutdown_scheduler
        shutdown_scheduler()
    except Exception:  # pragma: no cover
        pass


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "finyl-dcp"}


# client_edits shares the /api/v1/clients prefix and defines the literal
# /clients/edit-requests route — it MUST be registered before the clients
# router, whose GET /{client_id} would otherwise swallow "edit-requests".
app.include_router(client_edits.router)

for r in (auth, admin, clients, lending, payments, notifications, dashboard,
          complaints, crm, call_center, impact, cbk, ai,
          access, approvals, reporting, integrations, messaging, dcp_settings,
          accounting,
          # Phase 2
          guarantors, kyc_escalations, face_validation, validation_prefs,
          field_ops, security_config, activity, collections, third_party,
          search):
    app.include_router(r.router)

# Phase 2 secondary routers (cross-prefix / distinct-auth endpoints).
app.include_router(collections.extra_router)
app.include_router(third_party.ingestion_router)

# Legacy /api/v1/borrowers alias — same handlers as /api/v1/clients so anything
# built against the old path keeps working after the Clients rename.
app.include_router(clients.alias_router)

# Integrations module: SMS revenue/usage + logs reporting API (RBAC-scoped, not
# super-admin-gated) and the unauthenticated Uwazii delivery-report (DLR) webhook.
app.include_router(integrations.reporting_router)
app.include_router(integrations.webhook_router)
