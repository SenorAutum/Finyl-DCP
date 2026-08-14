-- ============================================================================
-- Finyl-DCP migration 004 — LIVE integrations overhaul.
--
-- Adds provider-tracking columns to sms_logs (live Uwazii dispatch) and the
-- new tables backing M-Pesa statement analysis, CRB checks and per-tenant
-- integration config (DCP Setup screen).
--
-- ADDITIVE ONLY. No existing column is dropped or renamed; every new column is
-- nullable / defaulted so existing data and analytics are untouched.
-- Idempotent: safe to re-run.
-- ============================================================================
SET search_path TO finyl_dcp, public;

-- --- sms_logs: live provider dispatch tracking ------------------------------
ALTER TABLE sms_logs ADD COLUMN IF NOT EXISTS provider          varchar(30);
ALTER TABLE sms_logs ADD COLUMN IF NOT EXISTS provider_ref      varchar(80);
ALTER TABLE sms_logs ADD COLUMN IF NOT EXISTS provider_response text;
ALTER TABLE sms_logs ADD COLUMN IF NOT EXISTS error             text;

-- --- M-Pesa statement creditworthiness analysis -----------------------------
CREATE TABLE IF NOT EXISTS mpesa_statement_analysis (
    id                      serial PRIMARY KEY,
    tenant_id               integer NOT NULL REFERENCES tenants(id),
    client_id               integer NOT NULL REFERENCES borrowers(id),
    loan_id                 integer REFERENCES loans(id),
    period_start            timestamp,
    period_end              timestamp,
    months_covered          double precision DEFAULT 0,
    transactions_count      integer DEFAULT 0,
    summary                 json,
    detected_lenders        json,
    integrity_flags         json,
    affordability_score     integer DEFAULT 0,
    comfortable_installment numeric(12,2) DEFAULT 0,
    monthly_debt_service    numeric(12,2) DEFAULT 0,
    net_monthly_cash_flow   numeric(12,2) DEFAULT 0,
    tampering_suspected     boolean DEFAULT false,
    source_filename         varchar(200),
    created_by_user_id      integer REFERENCES users(id),
    created_at              timestamp DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mpesa_stmt_tenant ON mpesa_statement_analysis (tenant_id);
CREATE INDEX IF NOT EXISTS ix_mpesa_stmt_client ON mpesa_statement_analysis (client_id);

-- --- CRB (credit reference bureau) checks -----------------------------------
CREATE TABLE IF NOT EXISTS crb_checks (
    id                 serial PRIMARY KEY,
    tenant_id          integer NOT NULL REFERENCES tenants(id),
    client_id          integer NOT NULL REFERENCES borrowers(id),
    provider           varchar(30),
    status             varchar(20),
    reference          varchar(80),
    credit_score       integer,
    active_accounts    integer,
    defaults_count     integer,
    total_outstanding  numeric(14,2),
    raw                json,
    error              text,
    created_by_user_id integer REFERENCES users(id),
    created_at         timestamp DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_crb_tenant ON crb_checks (tenant_id);
CREATE INDEX IF NOT EXISTS ix_crb_client ON crb_checks (client_id);

-- --- Per-tenant integration config (DCP Setup overrides) --------------------
CREATE TABLE IF NOT EXISTS tenant_integration_config (
    id                 serial PRIMARY KEY,
    tenant_id          integer NOT NULL REFERENCES tenants(id),
    integration        varchar(30) NOT NULL,
    config             json,
    secrets            json,
    enabled            boolean DEFAULT true,
    updated_by_user_id integer REFERENCES users(id),
    updated_at         timestamp DEFAULT now(),
    CONSTRAINT uq_tenant_integration UNIQUE (tenant_id, integration)
);
CREATE INDEX IF NOT EXISTS ix_tenant_integration_tenant ON tenant_integration_config (tenant_id);

-- --- Non-compliant DCPs: CBK Reporting OFF by default -----------------------
-- A DCP must be CBK-licensed before it can file reports. Flip the flag off for
-- the non-compliant demo tenants (idempotent).
UPDATE tenant_modules tm
   SET enabled = false
  FROM tenants t
 WHERE t.id = tm.tenant_id
   AND tm.module_key = 'cbk_reporting'
   AND t.code IN ('PESAF', 'JENGA');
