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

from app.core.database import Base, SessionLocal, engine, ensure_schema
from app.core.security import decode_token
from app import models  # noqa: F401 — register all tables on Base.metadata
from app.routers import (admin, ai, auth, call_center, cbk, clients, complaints, crm,
                         dashboard, impact, lending, notifications, payments,
                         access, approvals, reporting, integrations, messaging)

app = FastAPI(
    title="Finyl-DCP API",
    version="1.0.0",
    description="Multi-tenant Digital Credit Provider platform — lending engine, "
                "M-Pesa integration hub (live Daraja), executive analytics, consumer "
                "protection, CRM, call center, social impact and CBK compliance.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # same-origin in production behind nginx; open for local dev
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


@app.on_event("startup")
def on_startup():
    """Create schema/tables if missing (idempotent). For production migrations,
    see migrations/001_init.sql (generated from these models)."""
    ensure_schema()
    Base.metadata.create_all(bind=engine)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "finyl-dcp"}


for r in (auth, admin, clients, lending, payments, notifications, dashboard,
          complaints, crm, call_center, impact, cbk, ai,
          access, approvals, reporting, integrations, messaging):
    app.include_router(r.router)

# Legacy /api/v1/borrowers alias — same handlers as /api/v1/clients so anything
# built against the old path keeps working after the Clients rename.
app.include_router(clients.alias_router)

# Integrations module: SMS revenue/usage + logs reporting API (RBAC-scoped, not
# super-admin-gated) and the unauthenticated Uwazii delivery-report (DLR) webhook.
app.include_router(integrations.reporting_router)
app.include_router(integrations.webhook_router)
