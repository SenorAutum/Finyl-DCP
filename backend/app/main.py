"""
Finyl-DCP — FastAPI application entrypoint.

Multi-tenant SaaS for Digital Credit Providers (Kenya).
Modules: lending, payments, dashboard, complaints, crm, call_center, impact,
cbk_reporting, ai_agent — each gated per-tenant via feature flags.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import Base, engine, ensure_schema
from app import models  # noqa: F401 — register all tables on Base.metadata
from app.routers import (admin, ai, auth, call_center, cbk, clients, complaints, crm,
                         dashboard, impact, lending, notifications, payments,
                         access, approvals, reporting, integrations)

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
          access, approvals, reporting, integrations):
    app.include_router(r.router)

# Legacy /api/v1/borrowers alias — same handlers as /api/v1/clients so anything
# built against the old path keeps working after the Clients rename.
app.include_router(clients.alias_router)

# Integrations module: SMS revenue/usage + logs reporting API (RBAC-scoped, not
# super-admin-gated) and the unauthenticated Uwazii delivery-report (DLR) webhook.
app.include_router(integrations.reporting_router)
app.include_router(integrations.webhook_router)
