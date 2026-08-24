-- ============================================================================
-- Migration 014 — Additive feature surface (7 features)
--
-- Purely ADDITIVE and backward-compatible. Adds five new tenant-scoped tables
-- used by the new read-only / advisory endpoints. NO existing table, column,
-- constraint or money-movement path is altered. Every table carries tenant_id
-- and is uniquely keyed for idempotent upserts. Safe to re-run:
--   * CREATE TABLE IF NOT EXISTS
--   * CREATE (UNIQUE) INDEX IF NOT EXISTS
--   * default Chart-of-Accounts seed uses ON CONFLICT DO NOTHING
--
-- Tables:
--   1. ecl_provision_config  — per-tenant IFRS 9 ECL stage rates (dashboard).
--   2. suspense_entries      — unallocated / overpayment / closed-loan C2B money.
--   3. sms_opt_outs          — per-tenant SMS opt-out register (non-transactional).
--   4. kyc_consents          — per-borrower KYC / data-processing consent capture.
--   5. chart_of_accounts     — per-tenant Chart of Accounts for GL export.
--
-- Applied against schema finyl_dcp.
-- ============================================================================
SET search_path TO finyl_dcp, public;

-- ---------------------------------------------------------------------------
-- 1. IFRS 9 ECL provisioning configuration (per tenant)
--    Stage rates default in code (1% / 20% / 60%); a row here overrides them.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ecl_provision_config (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    stage1_rate         NUMERIC(6, 4) NOT NULL DEFAULT 0.0100,   -- 0-30 dpd
    stage2_rate         NUMERIC(6, 4) NOT NULL DEFAULT 0.2000,   -- 31-90 dpd
    stage3_rate         NUMERIC(6, 4) NOT NULL DEFAULT 0.6000,   -- 90+ dpd / defaulted
    updated_by_user_id  INTEGER REFERENCES users(id),
    updated_at          TIMESTAMP DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ecl_config_tenant
    ON ecl_provision_config (tenant_id);


-- ---------------------------------------------------------------------------
-- 2. Suspense account — money received that could not be applied cleanly.
--    Hooked additively from the existing C2B confirmation callback.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suspense_entries (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    source              VARCHAR(10) NOT NULL DEFAULT 'c2b',       -- c2b | manual
    mpesa_ref           VARCHAR(40),
    phone               VARCHAR(20),
    amount              NUMERIC(12, 2) NOT NULL,
    reason              VARCHAR(20) NOT NULL,                     -- unmatched | overpayment | closed_loan
    status              VARCHAR(12) NOT NULL DEFAULT 'open',      -- open | allocated | refunded
    matched_loan_id     INTEGER REFERENCES loans(id),
    raw_payload         JSON DEFAULT '{}'::json,
    created_at          TIMESTAMP DEFAULT now(),
    resolved_at         TIMESTAMP,
    resolved_by_user_id INTEGER REFERENCES users(id)
);

-- One suspense row per (tenant, mpesa_ref) — the idempotency key. mpesa_ref may
-- be NULL for manual entries, and NULLs are not deduplicated by a unique index,
-- which is the intended behaviour (manual entries are never auto-deduped).
CREATE UNIQUE INDEX IF NOT EXISTS uq_suspense_tenant_ref
    ON suspense_entries (tenant_id, mpesa_ref);

CREATE INDEX IF NOT EXISTS ix_suspense_tenant_status
    ON suspense_entries (tenant_id, status);


-- ---------------------------------------------------------------------------
-- 3. SMS opt-out register — suppresses NON-transactional messages only.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sms_opt_outs (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    phone         VARCHAR(20) NOT NULL,
    opted_out_at  TIMESTAMP DEFAULT now(),
    source        VARCHAR(10) NOT NULL DEFAULT 'manual',          -- keyword | manual | api
    active        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_sms_opt_out_tenant_phone
    ON sms_opt_outs (tenant_id, phone);


-- ---------------------------------------------------------------------------
-- 4. KYC consent capture — per borrower, versioned.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kyc_consents (
    id                       SERIAL PRIMARY KEY,
    tenant_id                INTEGER NOT NULL REFERENCES tenants(id),
    borrower_id              INTEGER NOT NULL REFERENCES borrowers(id),
    consent_data_processing  BOOLEAN NOT NULL DEFAULT FALSE,
    consent_credit_check     BOOLEAN NOT NULL DEFAULT FALSE,
    consent_marketing        BOOLEAN NOT NULL DEFAULT FALSE,
    consent_version          VARCHAR(20),
    consented_at             TIMESTAMP DEFAULT now(),
    ip_address               VARCHAR(45)
);

-- Latest consent per borrower is resolved by created ordering; index the FK.
CREATE INDEX IF NOT EXISTS ix_kyc_consents_tenant_borrower
    ON kyc_consents (tenant_id, borrower_id);


-- ---------------------------------------------------------------------------
-- 5. Chart of Accounts — per tenant, drives the double-entry GL export.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chart_of_accounts (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    code        VARCHAR(20) NOT NULL,
    name        VARCHAR(120) NOT NULL,
    type        VARCHAR(12) NOT NULL,                             -- asset | liability | income | expense | equity
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_coa_tenant_code
    ON chart_of_accounts (tenant_id, code);

-- Seed a default Chart of Accounts for every existing tenant. Idempotent via
-- ON CONFLICT (tenant_id, code). New tenants are seeded lazily by the API
-- (GET /api/v1/accounting/chart-of-accounts seeds-if-empty).
INSERT INTO chart_of_accounts (tenant_id, code, name, type)
SELECT t.id, d.code, d.name, d.type
FROM tenants t
CROSS JOIN (VALUES
    ('1000', 'Loans Receivable',        'asset'),
    ('1010', 'Operational Float / Cash','asset'),
    ('1900', 'Suspense Account',        'asset'),
    ('2000', 'Excise Duty Payable',     'liability'),
    ('4000', 'Interest Income',         'income'),
    ('4010', 'Fee Income',              'income'),
    ('4020', 'Penalty Income',          'income'),
    ('5000', 'Loan Write-offs',         'expense')
) AS d(code, name, type)
ON CONFLICT (tenant_id, code) DO NOTHING;
