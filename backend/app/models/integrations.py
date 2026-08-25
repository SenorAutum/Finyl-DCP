"""Models for the live-integration features: M-Pesa statement creditworthiness
analysis, CRB (credit reference bureau) checks and per-tenant integration config."""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        JSON, Numeric, String, Text, UniqueConstraint)

from app.core.database import Base


class MpesaStatementAnalysis(Base):
    """Creditworthiness analysis derived from an official Safaricom M-Pesa
    statement PDF (parsed locally — no Safaricom API involved)."""
    __tablename__ = "mpesa_statement_analysis"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id"), nullable=False, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"))       # optional link to an application

    period_start = Column(DateTime)
    period_end = Column(DateTime)
    months_covered = Column(Float, default=0)
    transactions_count = Column(Integer, default=0)

    summary = Column(JSON, default=dict)            # inflow/outflow/net/balance/regularity metrics
    detected_lenders = Column(JSON, default=list)   # [{name, category, inflow, outflow, count, ...}]
    integrity_flags = Column(JSON, default=list)    # [{code, severity, detail}]

    affordability_score = Column(Integer, default=0)         # 0-100
    comfortable_installment = Column(Numeric(12, 2), default=0)
    monthly_debt_service = Column(Numeric(12, 2), default=0)
    net_monthly_cash_flow = Column(Numeric(12, 2), default=0)
    tampering_suspected = Column(Boolean, default=False)

    source_filename = Column(String(200))
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class CrbCheck(Base):
    """A credit-reference-bureau enquiry result (Metropol/TransUnion/Creditinfo)."""
    __tablename__ = "crb_checks"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("borrowers.id"), nullable=False, index=True)

    provider = Column(String(30))                   # metropol | transunion | creditinfo
    status = Column(String(20))                     # ok | error | not_configured
    reference = Column(String(80))
    credit_score = Column(Integer)                  # bureau score (e.g. 200-900)
    active_accounts = Column(Integer)
    defaults_count = Column(Integer)
    total_outstanding = Column(Numeric(14, 2))
    raw = Column(JSON, default=dict)                # full bureau payload
    error = Column(Text)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class SmsRateCard(Base):
    """Configurable SMS pricing. The single ``active=True`` row drives billing;
    keeping history means a rate change is a new row (old one deactivated), so
    past SmsLog snapshots stay accurate. Prices are per MESSAGE (not per segment)."""
    __tablename__ = "sms_rate_cards"

    id = Column(Integer, primary_key=True)
    sell_price_kes = Column(Numeric(10, 4), nullable=False)   # charged to the DCP
    cost_price_kes = Column(Numeric(10, 4), nullable=False)   # our cost from Uwazii
    currency = Column(String(3), default="KES")
    effective_from = Column(DateTime, default=datetime.utcnow)
    active = Column(Boolean, default=True, index=True)
    note = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class IntegrationTestLog(Base):
    """Auditable history of 'Test connection' runs from the Integrations console."""
    __tablename__ = "integration_test_logs"

    id = Column(Integer, primary_key=True)
    integration_key = Column(String(40), nullable=False, index=True)  # uwazii_sms | daraja_mpesa | ...
    ok = Column(Boolean, default=False)
    detail = Column(Text)
    run_by_user_id = Column(Integer, ForeignKey("users.id"))
    run_by_email = Column(String(120))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class MpesaWebhookEvent(Base):
    """Durable ingestion log + dead-letter queue for Daraja M-Pesa webhooks.

    Every incoming callback is persisted here as ``received`` BEFORE the existing
    processing runs, so a delivery is never lost on a transient failure. The row
    then flips to ``processed`` (success) or ``failed`` (internal error, with the
    error text + a scheduled ``next_retry_at``). The APScheduler retry worker
    reprocesses due ``failed`` rows idempotently and escalates to ``dead`` after
    ``WEBHOOK_MAX_ATTEMPTS``, emitting an alert.

    ``raw_payload`` is the verbatim body and MAY CONTAIN PII (payer MSISDN /
    names); the scheduled purge NULLs it for ``processed`` rows older than
    ``WEBHOOK_RAW_RETENTION_HOURS`` (ODPC data minimisation), keeping only
    non-PII metadata for audit. Mirrors migration 016 exactly (the ORM builds
    this table for the SQLite test DB; migrations own the live Postgres schema)."""
    __tablename__ = "mpesa_webhook_events"

    id = Column(Integer, primary_key=True)
    # Resolved lazily — NULL until the tenant is determined (or unresolvable).
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    endpoint = Column(String(40), nullable=False)      # b2c-result | b2c-timeout | stk-callback | c2b-callback
    shortcode = Column(String(20))                     # BusinessShortCode when extractable
    received_at = Column(DateTime, default=datetime.utcnow, index=True)
    processed_at = Column(DateTime)
    raw_payload = Column(JSON)                         # verbatim body — MAY CONTAIN PII (purged after retention)
    processing_status = Column(String(20), nullable=False, default="received")  # received|processed|failed|dead
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text)
    next_retry_at = Column(DateTime)


class TenantIntegrationConfig(Base):
    """Per-tenant integration overrides captured from the DCP Setup screen.

    Platform env vars remain the base configuration; a row here lets a tenant
    override non-secret settings (and store secrets, encrypted at rest in a real
    deployment). Secrets are write-only from the UI ("leave blank to keep")."""
    __tablename__ = "tenant_integration_config"
    __table_args__ = (UniqueConstraint("tenant_id", "integration", name="uq_tenant_integration"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    integration = Column(String(30), nullable=False)   # sms | daraja | ekyc | crb | cbk_reporting
    config = Column(JSON, default=dict)                 # non-secret fields
    secrets = Column(JSON, default=dict)               # secret fields (masked in responses)
    enabled = Column(Boolean, default=True)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
