-- WP13: the Finyl command centre — Finyl's own side of the platform.
--   * platform users (Finyl staff, partners) with switches and assigned DCPs
--   * Finyl's charges to DCPs: price list, per-DCP billing, invoices, payments
--   * Finyl's loans to DCPs: facilities and drawdowns (repayments are payments)
--   * Finyl's sales pipeline
--   * alerts when something fails, and who is told
--   * nightly figures per DCP for trends
SET search_path TO finyl_dcp, public;

CREATE TABLE IF NOT EXISTS platform_user_access (
    user_id            INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    preset             VARCHAR(30) DEFAULT 'custom',
    title              VARCHAR(80),
    permissions        JSONB DEFAULT '[]'::jsonb,
    all_tenants        BOOLEAN DEFAULT FALSE,
    tenant_ids         JSONB DEFAULT '[]'::jsonb,
    updated_by_user_id INTEGER REFERENCES users(id),
    updated_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform_pricing (
    id                    SERIAL PRIMARY KEY,
    onboarding_fee        NUMERIC(14,2) NOT NULL DEFAULT 50000,
    subscription_rate_pct NUMERIC(6,3)  NOT NULL DEFAULT 1,
    subscription_minimum  NUMERIC(14,2) NOT NULL DEFAULT 30000,
    branch_fee            NUMERIC(14,2) NOT NULL DEFAULT 20000,
    branches_included     INTEGER NOT NULL DEFAULT 1,
    bill_sms              BOOLEAN NOT NULL DEFAULT TRUE,
    payment_terms_days    INTEGER NOT NULL DEFAULT 14,
    invoice_day           INTEGER NOT NULL DEFAULT 1,
    updated_by_user_id    INTEGER REFERENCES users(id),
    updated_at            TIMESTAMPTZ DEFAULT now()
);
INSERT INTO platform_pricing (onboarding_fee, subscription_rate_pct, subscription_minimum, branch_fee)
SELECT 50000, 1, 30000, 20000 WHERE NOT EXISTS (SELECT 1 FROM platform_pricing);

CREATE TABLE IF NOT EXISTS tenant_billing (
    tenant_id             INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    billing_active        BOOLEAN DEFAULT FALSE,
    billing_start         DATE,
    onboarding_fee        NUMERIC(14,2),
    subscription_rate_pct NUMERIC(6,3),
    subscription_minimum  NUMERIC(14,2),
    branch_fee            NUMERIC(14,2),
    branches_included     INTEGER,
    onboarding_invoiced   BOOLEAN DEFAULT FALSE,
    branches_billed       INTEGER DEFAULT 0,
    billing_email         VARCHAR(120),
    notes                 TEXT,
    updated_by_user_id    INTEGER REFERENCES users(id),
    updated_at            TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform_invoices (
    id                 SERIAL PRIMARY KEY,
    number             VARCHAR(30) NOT NULL UNIQUE,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    period_start       DATE NOT NULL,
    period_end         DATE NOT NULL,
    issued_at          TIMESTAMPTZ DEFAULT now(),
    due_date           DATE,
    status             VARCHAR(15) DEFAULT 'issued',
    total              NUMERIC(14,2) NOT NULL DEFAULT 0,
    paid               NUMERIC(14,2) NOT NULL DEFAULT 0,
    outstanding_book   NUMERIC(16,2),
    created_by_user_id INTEGER REFERENCES users(id),
    void_reason        TEXT
);
-- One live invoice per DCP and month; a cancelled (void) one frees the month.
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_invoice_period
    ON platform_invoices (tenant_id, period_start) WHERE status <> 'void';
CREATE INDEX IF NOT EXISTS ix_platform_invoices_tenant ON platform_invoices (tenant_id);
CREATE INDEX IF NOT EXISTS ix_platform_invoices_status ON platform_invoices (status);

CREATE TABLE IF NOT EXISTS platform_invoice_lines (
    id          SERIAL PRIMARY KEY,
    invoice_id  INTEGER NOT NULL REFERENCES platform_invoices(id) ON DELETE CASCADE,
    kind        VARCHAR(20) NOT NULL,
    description VARCHAR(300) NOT NULL,
    quantity    NUMERIC(14,3) DEFAULT 1,
    unit_price  NUMERIC(14,4) DEFAULT 0,
    amount      NUMERIC(14,2) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_platform_invoice_lines_invoice ON platform_invoice_lines (invoice_id);

CREATE TABLE IF NOT EXISTS platform_facilities (
    id                 SERIAL PRIMARY KEY,
    number             VARCHAR(20) NOT NULL UNIQUE,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    limit_amount       NUMERIC(16,2) NOT NULL,
    interest_rate_pct  NUMERIC(6,3) NOT NULL,
    interest_method    VARCHAR(10) NOT NULL DEFAULT 'reducing',
    term_months        INTEGER NOT NULL DEFAULT 12,
    start_date         DATE NOT NULL,
    status             VARCHAR(15) DEFAULT 'active',
    purpose            VARCHAR(200),
    notes              TEXT,
    created_by_user_id INTEGER REFERENCES users(id),
    created_at         TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_platform_facilities_tenant ON platform_facilities (tenant_id);

CREATE TABLE IF NOT EXISTS platform_drawdowns (
    id                 SERIAL PRIMARY KEY,
    facility_id        INTEGER NOT NULL REFERENCES platform_facilities(id) ON DELETE CASCADE,
    amount             NUMERIC(16,2) NOT NULL,
    disbursed_on       DATE NOT NULL,
    reference          VARCHAR(40),
    note               TEXT,
    created_by_user_id INTEGER REFERENCES users(id),
    created_at         TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_platform_drawdowns_facility ON platform_drawdowns (facility_id);

CREATE TABLE IF NOT EXISTS platform_payments (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER REFERENCES tenants(id),
    invoice_id          INTEGER REFERENCES platform_invoices(id),
    facility_id         INTEGER REFERENCES platform_facilities(id),
    amount              NUMERIC(14,2) NOT NULL,
    reference           VARCHAR(40) NOT NULL UNIQUE,
    channel             VARCHAR(20) DEFAULT 'mpesa_paybill',
    paid_at             TIMESTAMPTZ NOT NULL,
    principal           NUMERIC(14,2) DEFAULT 0,
    interest            NUMERIC(14,2) DEFAULT 0,
    note                TEXT,
    recorded_by_user_id INTEGER REFERENCES users(id),
    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_platform_payments_tenant ON platform_payments (tenant_id);
CREATE INDEX IF NOT EXISTS ix_platform_payments_invoice ON platform_payments (invoice_id);
CREATE INDEX IF NOT EXISTS ix_platform_payments_facility ON platform_payments (facility_id);

CREATE TABLE IF NOT EXISTS platform_unmatched_payments (
    id              SERIAL PRIMARY KEY,
    reference       VARCHAR(40) NOT NULL UNIQUE,
    amount          NUMERIC(14,2) NOT NULL,
    account_entered VARCHAR(60),
    payer           VARCHAR(120),
    paid_at         TIMESTAMPTZ,
    status          VARCHAR(15) DEFAULT 'open',
    resolved_note   TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform_leads (
    id                 SERIAL PRIMARY KEY,
    company_name       VARCHAR(160) NOT NULL,
    contact_name       VARCHAR(120),
    contact_phone      VARCHAR(20),
    contact_email      VARCHAR(120),
    stage              VARCHAR(15) NOT NULL DEFAULT 'lead',
    expected_loan_book NUMERIC(16,2),
    expected_branches  INTEGER,
    source             VARCHAR(60),
    next_step          VARCHAR(200),
    next_step_date     DATE,
    owner_user_id      INTEGER REFERENCES users(id),
    tenant_id          INTEGER REFERENCES tenants(id),
    lost_reason        VARCHAR(200),
    notes              TEXT,
    created_by_user_id INTEGER REFERENCES users(id),
    created_at         TIMESTAMPTZ DEFAULT now(),
    updated_at         TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_platform_leads_stage ON platform_leads (stage);

CREATE TABLE IF NOT EXISTS platform_lead_events (
    id         SERIAL PRIMARY KEY,
    lead_id    INTEGER NOT NULL REFERENCES platform_leads(id) ON DELETE CASCADE,
    from_stage VARCHAR(15),
    to_stage   VARCHAR(15),
    note       TEXT,
    user_id    INTEGER REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_platform_lead_events_lead ON platform_lead_events (lead_id);

CREATE TABLE IF NOT EXISTS platform_alerts (
    id                      SERIAL PRIMARY KEY,
    tenant_id               INTEGER,          -- no foreign key: recording an alert must never wait
    kind                    VARCHAR(40) NOT NULL,
    severity                VARCHAR(10) NOT NULL DEFAULT 'warning',
    title                   VARCHAR(200) NOT NULL,
    detail                  TEXT,
    dedupe_key              VARCHAR(200) NOT NULL,
    count                   INTEGER NOT NULL DEFAULT 1,
    status                  VARCHAR(15) NOT NULL DEFAULT 'open',
    first_seen              TIMESTAMPTZ DEFAULT now(),
    last_seen               TIMESTAMPTZ DEFAULT now(),
    acknowledged_by_user_id INTEGER,
    resolved_at             TIMESTAMPTZ,
    resolved_note           TEXT,
    notified_at             TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_platform_alerts_tenant ON platform_alerts (tenant_id);
CREATE INDEX IF NOT EXISTS ix_platform_alerts_kind ON platform_alerts (kind);
CREATE INDEX IF NOT EXISTS ix_platform_alerts_status ON platform_alerts (status);
CREATE INDEX IF NOT EXISTS ix_platform_alerts_dedupe ON platform_alerts (dedupe_key);
CREATE INDEX IF NOT EXISTS ix_platform_alerts_last_seen ON platform_alerts (last_seen);

CREATE TABLE IF NOT EXISTS platform_alert_recipients (
    id           SERIAL PRIMARY KEY,
    name         VARCHAR(120) NOT NULL,
    email        VARCHAR(120),
    phone        VARCHAR(20),
    min_severity VARCHAR(10) NOT NULL DEFAULT 'warning',
    kinds        JSONB DEFAULT '[]'::jsonb,
    tenant_ids   JSONB DEFAULT '[]'::jsonb,
    weekly_summary BOOLEAN NOT NULL DEFAULT TRUE,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT now()
);
-- Autum asked (11 Nov 2026) for alerts to go to this address for now.
INSERT INTO platform_alert_recipients (name, email, min_severity)
SELECT 'Afridon IT support', 'afridon.1.it@gmail.com', 'warning'
WHERE NOT EXISTS (SELECT 1 FROM platform_alert_recipients);

CREATE TABLE IF NOT EXISTS platform_tenant_daily (
    id           SERIAL PRIMARY KEY,
    tenant_id    INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    day          DATE NOT NULL,
    outstanding  NUMERIC(16,2) DEFAULT 0,
    par30        NUMERIC(16,2) DEFAULT 0,
    disbursed    NUMERIC(16,2) DEFAULT 0,
    collected    NUMERIC(16,2) DEFAULT 0,
    clients      INTEGER DEFAULT 0,
    active_loans INTEGER DEFAULT 0,
    sms_sent     INTEGER DEFAULT 0,
    sms_revenue  NUMERIC(14,2) DEFAULT 0,
    health_score INTEGER,
    created_at   TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_platform_tenant_day UNIQUE (tenant_id, day)
);
CREATE INDEX IF NOT EXISTS ix_platform_tenant_daily_tenant ON platform_tenant_daily (tenant_id);
CREATE INDEX IF NOT EXISTS ix_platform_tenant_daily_day ON platform_tenant_daily (day);
