-- Base schema (ORM parity). Safe to run on existing databases -- all statements are idempotent.
--
-- This migration retrospectively provisions the entire base schema defined by the
-- SQLAlchemy ORM models (backend/app/models/*.py) using CREATE TABLE IF NOT EXISTS,
-- so a fresh database can be brought up via the SQL-only migration path alone.
-- On an existing database (tables already created by the ORM) every statement is a
-- no-op. Types mirror the ORM (plain TIMESTAMP / JSON) to preserve parity with the
-- live schema; later migrations (002+) layer additive changes on top.

SET search_path TO finyl_dcp, public;

-- ---------------------------------------------------------------------------
-- Core tenancy & org hierarchy
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tenants (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(120) NOT NULL UNIQUE,
    code        VARCHAR(20) NOT NULL UNIQUE,
    logo_color  VARCHAR(16) DEFAULT '#10B981',
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_modules (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    module_key  VARCHAR(40) NOT NULL,
    enabled     BOOLEAN DEFAULT TRUE,
    CONSTRAINT uq_tenant_module UNIQUE (tenant_id, module_key)
);

CREATE TABLE IF NOT EXISTS regions (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    name        VARCHAR(80) NOT NULL
);

CREATE TABLE IF NOT EXISTS branches (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    region_id   INTEGER NOT NULL REFERENCES regions(id),
    name        VARCHAR(80) NOT NULL
);

CREATE TABLE IF NOT EXISTS staff (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    branch_id   INTEGER NOT NULL REFERENCES branches(id),
    name        VARCHAR(120) NOT NULL,
    role        VARCHAR(40) NOT NULL DEFAULT 'loan_officer',
    phone       VARCHAR(20),
    salary      NUMERIC(12,2) DEFAULT 0,
    petty_cash  NUMERIC(12,2) DEFAULT 0,
    hire_date   DATE,
    active      BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS users (
    id                    SERIAL PRIMARY KEY,
    email                 VARCHAR(160) NOT NULL UNIQUE,
    hashed_password       VARCHAR NOT NULL,
    full_name             VARCHAR(120) NOT NULL,
    role                  VARCHAR(30) NOT NULL DEFAULT 'relationship_officer',
    tenant_id             INTEGER REFERENCES tenants(id),
    staff_id              INTEGER REFERENCES staff(id),
    active                BOOLEAN DEFAULT TRUE,
    created_at            TIMESTAMP NOT NULL DEFAULT NOW(),
    branch_id             INTEGER REFERENCES branches(id),
    region_id             INTEGER REFERENCES regions(id),
    is_locked             BOOLEAN DEFAULT FALSE,
    force_password_reset  BOOLEAN DEFAULT FALSE,
    deactivated_at        TIMESTAMP,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until          TIMESTAMPTZ,
    token_version         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_users_email     ON users(email);
CREATE INDEX IF NOT EXISTS ix_users_tenant_id ON users(tenant_id);

-- ---------------------------------------------------------------------------
-- Products & borrowers
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS products (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    name                VARCHAR(120) NOT NULL,
    code                VARCHAR(20) NOT NULL,
    interest_rate       NUMERIC(6,3) NOT NULL DEFAULT 10.0,
    interest_method     VARCHAR(20) DEFAULT 'flat',
    tenure_value        INTEGER DEFAULT 4,
    tenure_unit         VARCHAR(10) DEFAULT 'weeks',
    repayment_frequency VARCHAR(15) DEFAULT 'weekly',
    min_amount          NUMERIC(12,2) DEFAULT 1000,
    max_amount          NUMERIC(12,2) DEFAULT 100000,
    min_age             INTEGER DEFAULT 18,
    max_age             INTEGER DEFAULT 65,
    penalty_rate        NUMERIC(6,3) DEFAULT 1.0,
    rules               JSON DEFAULT '{}',
    active              BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS borrowers (
    id                     SERIAL PRIMARY KEY,
    tenant_id              INTEGER NOT NULL REFERENCES tenants(id),
    first_name             VARCHAR(60) NOT NULL,
    middle_name            VARCHAR(60),
    last_name              VARCHAR(60) NOT NULL,
    national_id            TEXT NOT NULL,                 -- EncryptedText (application-layer Fernet)
    national_id_hash       VARCHAR(64),                   -- blind index for lookups
    phone                  VARCHAR(20) NOT NULL,
    gender                 VARCHAR(10),
    date_of_birth          DATE,
    region_id              INTEGER REFERENCES regions(id),
    branch_id              INTEGER REFERENCES branches(id),
    business_sector        VARCHAR(60),
    baseline_monthly_sales NUMERIC(12,2) DEFAULT 0,
    baseline_employees     INTEGER DEFAULT 0,
    kyc_status             VARCHAR(20) DEFAULT 'draft',
    credit_score           INTEGER DEFAULT 0,
    created_at             TIMESTAMP NOT NULL DEFAULT NOW(),
    serial_number          VARCHAR(30),
    district_of_birth      VARCHAR(60),
    place_of_issue         VARCHAR(60),
    date_of_issue          DATE,
    district               VARCHAR(60),
    division               VARCHAR(60),
    location               VARCHAR(60),
    sub_location           VARCHAR(60),
    current_credit_rating  VARCHAR(20),
    is_active              BOOLEAN DEFAULT TRUE,
    onboarded_by           VARCHAR(120),
    officer_staff_id       INTEGER REFERENCES staff(id),
    approved_by_user_id    INTEGER REFERENCES users(id),
    profile_status         VARCHAR(20) DEFAULT 'approved',
    mpesa_validated        BOOLEAN DEFAULT FALSE,
    mpesa_validation_name  VARCHAR(120),
    mpesa_validated_at     TIMESTAMP,
    ekyc_status            VARCHAR(20),
    ekyc_reference         VARCHAR(60),
    ekyc_checked_at        TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_borrowers_national_id_hash ON borrowers(national_id_hash);

-- ---------------------------------------------------------------------------
-- Loans, repayments & payments
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS loans (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
    account_number       VARCHAR(40) NOT NULL,
    borrower_id          INTEGER NOT NULL REFERENCES borrowers(id),
    product_id           INTEGER NOT NULL REFERENCES products(id),
    staff_id             INTEGER REFERENCES staff(id),
    branch_id            INTEGER REFERENCES branches(id),
    principal            NUMERIC(12,2) NOT NULL,
    interest_rate        NUMERIC(6,3) NOT NULL,
    status               VARCHAR(20) NOT NULL DEFAULT 'pending',
    application_date      DATE,
    approval_date         DATE,
    disbursement_date     DATE,
    due_date              DATE,
    outstanding_balance   NUMERIC(12,2) DEFAULT 0,
    loan_cycle_number     INTEGER DEFAULT 1,
    created_at            TIMESTAMP NOT NULL DEFAULT NOW(),
    approved_by_user_id   INTEGER REFERENCES users(id),
    escalation_level      VARCHAR(10),
    decision_note         TEXT,
    disbursed_by_user_id  INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS ix_loans_account_number ON loans(account_number);
CREATE INDEX IF NOT EXISTS ix_loans_borrower_id    ON loans(borrower_id);
CREATE INDEX IF NOT EXISTS ix_loans_status         ON loans(status);

CREATE TABLE IF NOT EXISTS repayments (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    loan_id             INTEGER NOT NULL REFERENCES loans(id),
    amount              NUMERIC(12,2) NOT NULL,
    principal_component NUMERIC(12,2) DEFAULT 0,
    interest_component  NUMERIC(12,2) DEFAULT 0,
    payment_date        TIMESTAMP NOT NULL,
    method              VARCHAR(20) DEFAULT 'mpesa_c2b',
    mpesa_ref           VARCHAR(30)
);
CREATE INDEX IF NOT EXISTS ix_repayments_loan_id      ON repayments(loan_id);
CREATE INDEX IF NOT EXISTS ix_repayments_payment_date ON repayments(payment_date);

CREATE TABLE IF NOT EXISTS payment_transactions (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    type        VARCHAR(15) NOT NULL,
    loan_id     INTEGER REFERENCES loans(id),
    amount      NUMERIC(12,2) NOT NULL,
    phone       VARCHAR(20),
    mpesa_ref   VARCHAR(40),
    status      VARCHAR(20) DEFAULT 'success',
    raw_payload JSON DEFAULT '{}',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- SMS logging & templates
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sms_logs (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
    recipient_phone VARCHAR(20) NOT NULL,
    message         TEXT NOT NULL,
    trigger_type    VARCHAR(30) DEFAULT 'manual',
    status          VARCHAR(15) DEFAULT 'sent',
    provider        VARCHAR(30),
    provider_ref    VARCHAR(80),
    provider_response TEXT,
    error           TEXT,
    sent_at         TIMESTAMP,
    delivery_status VARCHAR(15) DEFAULT 'unknown',
    delivered_at    TIMESTAMP,
    billable        BOOLEAN DEFAULT FALSE,
    sell_price_kes  NUMERIC(10,4),
    cost_price_kes  NUMERIC(10,4),
    margin_kes      NUMERIC(10,4)
);

CREATE TABLE IF NOT EXISTS sms_templates (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    event_key   VARCHAR(40) NOT NULL,
    body        TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMP,
    CONSTRAINT uq_sms_templates_tenant_event UNIQUE (tenant_id, event_key)
);

-- ---------------------------------------------------------------------------
-- Client sub-records
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS client_mobile_wallets (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    client_id     INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    mobile_number VARCHAR(20),
    wallet_number VARCHAR(30),
    operator      VARCHAR(30),
    active        BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_client_mobile_wallets_client_id ON client_mobile_wallets(client_id);

CREATE TABLE IF NOT EXISTS client_next_of_kin (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    client_id     INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    full_name     VARCHAR(120),
    relationship  VARCHAR(30),
    mobile_number VARCHAR(20),
    national_id   TEXT,                          -- EncryptedText (application-layer Fernet)
    address       VARCHAR(160),
    active        BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_client_next_of_kin_client_id ON client_next_of_kin(client_id);

CREATE TABLE IF NOT EXISTS client_documents (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    client_id     INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    file_name     VARCHAR(200),
    original_name VARCHAR(200),
    mime_type     VARCHAR(120),
    size_bytes    INTEGER DEFAULT 0,
    doc_type      VARCHAR(40) DEFAULT 'other',
    storage_path  TEXT,
    ocr_applied   BOOLEAN DEFAULT FALSE,
    ocr_text      TEXT,                          -- EncryptedText (application-layer Fernet)
    uploaded_at   TIMESTAMP,
    uploaded_by   VARCHAR(120)
);
CREATE INDEX IF NOT EXISTS ix_client_documents_client_id ON client_documents(client_id);

-- ---------------------------------------------------------------------------
-- CRM, engagement & field ops (base)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_leads (
    id                    SERIAL PRIMARY KEY,
    tenant_id             INTEGER NOT NULL REFERENCES tenants(id),
    name                  VARCHAR(120) NOT NULL,
    phone                 VARCHAR(20),
    sector                VARCHAR(60),
    region_id             INTEGER REFERENCES regions(id),
    stage                 VARCHAR(20) DEFAULT 'lead',
    assigned_staff_id     INTEGER REFERENCES staff(id),
    estimated_loan_amount NUMERIC(12,2) DEFAULT 0,
    notes                 TEXT,
    created_at            TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_crm_leads_stage ON crm_leads(stage);

CREATE TABLE IF NOT EXISTS site_visits (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    lead_id     INTEGER NOT NULL REFERENCES crm_leads(id),
    staff_id    INTEGER REFERENCES staff(id),
    visit_date  DATE,
    latitude    FLOAT,
    longitude   FLOAT,
    outcome     VARCHAR(40),
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS call_logs (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    agent_id            INTEGER NOT NULL REFERENCES staff(id),
    borrower_id         INTEGER NOT NULL REFERENCES borrowers(id),
    loan_id             INTEGER REFERENCES loans(id),
    call_date           TIMESTAMP,
    duration_seconds    INTEGER DEFAULT 0,
    call_outcome        VARCHAR(20) DEFAULT 'no_answer',
    promise_to_pay_date DATE,
    promise_amount      NUMERIC(12,2),
    notes               TEXT
);
CREATE INDEX IF NOT EXISTS ix_call_logs_agent_id ON call_logs(agent_id);

CREATE TABLE IF NOT EXISTS complaints (
    id                SERIAL PRIMARY KEY,
    tenant_id         INTEGER NOT NULL REFERENCES tenants(id),
    ticket_id         VARCHAR(40) NOT NULL,
    borrower_id       INTEGER REFERENCES borrowers(id),
    category          VARCHAR(30) NOT NULL DEFAULT 'other',
    description       TEXT,
    status            VARCHAR(15) DEFAULT 'open',
    created_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    sla_deadline      TIMESTAMP,
    resolved_at       TIMESTAMP,
    assigned_staff_id INTEGER REFERENCES staff(id),
    remedial_action   TEXT
);
CREATE INDEX IF NOT EXISTS ix_complaints_ticket_id ON complaints(ticket_id);
CREATE INDEX IF NOT EXISTS ix_complaints_status    ON complaints(status);

CREATE TABLE IF NOT EXISTS impact_surveys (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    survey_id          VARCHAR(40) NOT NULL,
    borrower_id        INTEGER NOT NULL REFERENCES borrowers(id),
    loan_id            INTEGER REFERENCES loans(id),
    loan_cycle_number  INTEGER DEFAULT 2,
    monthly_sales_pre  NUMERIC(12,2) DEFAULT 0,
    monthly_sales_post NUMERIC(12,2) DEFAULT 0,
    jobs_created       INTEGER DEFAULT 0,
    sales_improved     BOOLEAN DEFAULT FALSE,
    next_capital_plan  TEXT,
    survey_date        DATE
);

CREATE TABLE IF NOT EXISTS aml_flags (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    loan_id     INTEGER REFERENCES loans(id),
    borrower_id INTEGER REFERENCES borrowers(id),
    flag_type   VARCHAR(40) NOT NULL,
    severity    VARCHAR(10) DEFAULT 'medium',
    details     TEXT,
    flagged_at  TIMESTAMP,
    reviewed    BOOLEAN DEFAULT FALSE
);

-- ---------------------------------------------------------------------------
-- Approval / RBAC / config
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS approval_thresholds (
    id             SERIAL PRIMARY KEY,
    tenant_id      INTEGER NOT NULL REFERENCES tenants(id),
    scope_type     VARCHAR(10) NOT NULL,
    scope_key      VARCHAR(60) NOT NULL,
    threshold_type VARCHAR(20) NOT NULL,
    amount         NUMERIC(14,2) NOT NULL DEFAULT 0,
    created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS approver_settings (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    approval_type      VARCHAR(20) NOT NULL,
    role               VARCHAR(40) NOT NULL,
    enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by_user_id INTEGER REFERENCES users(id),
    updated_at         TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sms_automation_settings (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL UNIQUE REFERENCES tenants(id),
    automation_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    send_hour          INTEGER NOT NULL DEFAULT 7,
    updated_by_user_id INTEGER REFERENCES users(id),
    updated_at         TIMESTAMP
);

CREATE TABLE IF NOT EXISTS role_permission_overrides (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    role               VARCHAR(40) NOT NULL,
    permission_key     VARCHAR(60) NOT NULL,
    granted            BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by_user_id INTEGER REFERENCES users(id),
    updated_at         TIMESTAMP
);

CREATE TABLE IF NOT EXISTS custom_roles (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    role_key           VARCHAR(40) NOT NULL,
    label              VARCHAR(120) NOT NULL,
    updated_by_user_id INTEGER REFERENCES users(id),
    created_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER REFERENCES tenants(id),
    user_id     INTEGER REFERENCES users(id),
    user_email  VARCHAR(160),
    action      VARCHAR(60) NOT NULL,
    entity_type VARCHAR(40),
    entity_id   VARCHAR(40),
    details     JSON DEFAULT '{}',
    ip          VARCHAR(50),
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_audit_logs_tenant_id  ON audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_user_id    ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_action     ON audit_logs(action);
CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs(created_at);

CREATE TABLE IF NOT EXISTS pending_approvals (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
    action_type     VARCHAR(20) NOT NULL,
    loan_id         INTEGER REFERENCES loans(id),
    amount          NUMERIC(14,2) NOT NULL,
    phone           VARCHAR(20),
    reason          VARCHAR(200),
    status          VARCHAR(20) DEFAULT 'pending_approval',
    maker_user_id   INTEGER NOT NULL REFERENCES users(id),
    maker_at        TIMESTAMP,
    checker_user_id INTEGER REFERENCES users(id),
    checker_at      TIMESTAMP,
    details         JSON DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_pending_approvals_status ON pending_approvals(status);

-- ---------------------------------------------------------------------------
-- Reporting & analytics
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS report_schedules (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    user_id     INTEGER REFERENCES users(id),
    name        VARCHAR(120) NOT NULL,
    report_type VARCHAR(40) NOT NULL,
    frequency   VARCHAR(20) DEFAULT 'weekly',
    recipients  VARCHAR(400),
    active      BOOLEAN DEFAULT TRUE,
    last_run_at TIMESTAMP,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS report_templates (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    user_id     INTEGER REFERENCES users(id),
    name        VARCHAR(120) NOT NULL,
    definition  JSON DEFAULT '{}',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS anomaly_flags (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    user_id     INTEGER REFERENCES users(id),
    entity_type VARCHAR(40),
    entity_id   VARCHAR(40),
    note        TEXT NOT NULL,
    status      VARCHAR(20) DEFAULT 'open',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mpesa_statement_analysis (
    id                    SERIAL PRIMARY KEY,
    tenant_id             INTEGER NOT NULL REFERENCES tenants(id),
    client_id             INTEGER NOT NULL REFERENCES borrowers(id),
    loan_id               INTEGER REFERENCES loans(id),
    period_start          TIMESTAMP,
    period_end            TIMESTAMP,
    months_covered        FLOAT DEFAULT 0,
    transactions_count    INTEGER DEFAULT 0,
    summary               JSON DEFAULT '{}',
    detected_lenders      JSON DEFAULT '[]',
    integrity_flags       JSON DEFAULT '[]',
    affordability_score   INTEGER DEFAULT 0,
    comfortable_installment NUMERIC(12,2) DEFAULT 0,
    monthly_debt_service  NUMERIC(12,2) DEFAULT 0,
    net_monthly_cash_flow NUMERIC(12,2) DEFAULT 0,
    tampering_suspected   BOOLEAN DEFAULT FALSE,
    source_filename       VARCHAR(200),
    created_by_user_id    INTEGER REFERENCES users(id),
    created_at            TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_mpesa_statement_analysis_client_id ON mpesa_statement_analysis(client_id);

CREATE TABLE IF NOT EXISTS crb_checks (
    id                SERIAL PRIMARY KEY,
    tenant_id         INTEGER NOT NULL REFERENCES tenants(id),
    client_id         INTEGER NOT NULL REFERENCES borrowers(id),
    provider          VARCHAR(30),
    status            VARCHAR(20),
    reference         VARCHAR(80),
    credit_score      INTEGER,
    active_accounts   INTEGER,
    defaults_count    INTEGER,
    total_outstanding NUMERIC(14,2),
    raw               JSON DEFAULT '{}',
    error             TEXT,
    created_by_user_id INTEGER REFERENCES users(id),
    created_at        TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_crb_checks_client_id ON crb_checks(client_id);

-- ---------------------------------------------------------------------------
-- Platform-global tables (no tenant_id)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sms_rate_cards (
    id             SERIAL PRIMARY KEY,
    sell_price_kes NUMERIC(10,4) NOT NULL,
    cost_price_kes NUMERIC(10,4) NOT NULL,
    currency       VARCHAR(3) DEFAULT 'KES',
    effective_from TIMESTAMP,
    active         BOOLEAN DEFAULT TRUE,
    note           VARCHAR(200),
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_sms_rate_cards_active ON sms_rate_cards(active);

CREATE TABLE IF NOT EXISTS integration_test_logs (
    id              SERIAL PRIMARY KEY,
    integration_key VARCHAR(40) NOT NULL,
    ok              BOOLEAN DEFAULT FALSE,
    detail          TEXT,
    run_by_user_id  INTEGER REFERENCES users(id),
    run_by_email    VARCHAR(120),
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_integration_test_logs_integration_key ON integration_test_logs(integration_key);
CREATE INDEX IF NOT EXISTS ix_integration_test_logs_created_at      ON integration_test_logs(created_at);

-- ---------------------------------------------------------------------------
-- Integrations, provisioning & suspense (base)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mpesa_webhook_events (
    id                SERIAL PRIMARY KEY,
    tenant_id         INTEGER REFERENCES tenants(id),
    endpoint          VARCHAR(40) NOT NULL,
    shortcode         VARCHAR(20),
    received_at       TIMESTAMP,
    processed_at      TIMESTAMP,
    raw_payload       JSON,
    processing_status VARCHAR(20) NOT NULL DEFAULT 'received',
    attempts          INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT,
    next_retry_at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_mpesa_webhook_events_tenant_id   ON mpesa_webhook_events(tenant_id);
CREATE INDEX IF NOT EXISTS ix_mpesa_webhook_events_received_at ON mpesa_webhook_events(received_at);

CREATE TABLE IF NOT EXISTS tenant_integration_config (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    integration        VARCHAR(30) NOT NULL,
    config             JSON DEFAULT '{}',
    secrets            JSON DEFAULT '{}',
    enabled            BOOLEAN DEFAULT TRUE,
    updated_by_user_id INTEGER REFERENCES users(id),
    updated_at         TIMESTAMP,
    CONSTRAINT uq_tenant_integration UNIQUE (tenant_id, integration)
);

CREATE TABLE IF NOT EXISTS ecl_provision_config (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    stage1_rate        NUMERIC(6,4) NOT NULL DEFAULT 0.0100,
    stage2_rate        NUMERIC(6,4) NOT NULL DEFAULT 0.2000,
    stage3_rate        NUMERIC(6,4) NOT NULL DEFAULT 0.6000,
    updated_by_user_id INTEGER REFERENCES users(id),
    updated_at         TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suspense_entries (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    source              VARCHAR(10) NOT NULL DEFAULT 'c2b',
    mpesa_ref           VARCHAR(40),
    phone               VARCHAR(20),
    amount              NUMERIC(12,2) NOT NULL,
    reason              VARCHAR(20) NOT NULL,
    status              VARCHAR(12) NOT NULL DEFAULT 'open',
    matched_loan_id     INTEGER REFERENCES loans(id),
    raw_payload         JSON DEFAULT '{}',
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMP,
    resolved_by_user_id INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS sms_opt_outs (
    id           SERIAL PRIMARY KEY,
    tenant_id    INTEGER NOT NULL REFERENCES tenants(id),
    phone        VARCHAR(20) NOT NULL,
    opted_out_at TIMESTAMP,
    source       VARCHAR(10) NOT NULL DEFAULT 'manual',
    active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS kyc_consents (
    id                      SERIAL PRIMARY KEY,
    tenant_id               INTEGER NOT NULL REFERENCES tenants(id),
    borrower_id             INTEGER NOT NULL REFERENCES borrowers(id),
    consent_data_processing BOOLEAN NOT NULL DEFAULT FALSE,
    consent_credit_check    BOOLEAN NOT NULL DEFAULT FALSE,
    consent_marketing       BOOLEAN NOT NULL DEFAULT FALSE,
    consent_version         VARCHAR(20),
    consented_at            TIMESTAMP,
    ip_address              VARCHAR(45)
);
CREATE INDEX IF NOT EXISTS ix_kyc_consents_borrower_id ON kyc_consents(borrower_id);

CREATE TABLE IF NOT EXISTS chart_of_accounts (
    id        SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    code      VARCHAR(20) NOT NULL,
    name      VARCHAR(120) NOT NULL,
    type      VARCHAR(12) NOT NULL,
    active    BOOLEAN NOT NULL DEFAULT TRUE
);
