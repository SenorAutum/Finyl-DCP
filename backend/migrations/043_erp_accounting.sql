-- WP14: ERP accounting — real double-entry books for every DCP (and Finyl itself).
--   * chart of accounts gains groups (subtype), cash/bank flag and system flag
--   * accounting settings per DCP: posting map, period lock, numbering, approval limits
--   * journals and journal lines (auto-posted from loans/payments, or manual with approval)
--   * payables: suppliers, bills, expense claims, payments out
--   * bank and M-Pesa reconciliation: statements, lines, matches
--   * budgets per year (and optionally per branch)
--   * fixed assets: categories, register, monthly depreciation runs
--   * tenants.is_internal marks Finyl's own workspace (kept out of DCP lists and billing)
SET search_path TO finyl_dcp, public;

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_internal BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS subtype     VARCHAR(20);
ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS is_cash     BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS is_system   BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE chart_of_accounts ADD COLUMN IF NOT EXISTS description TEXT;

CREATE TABLE IF NOT EXISTS acc_settings (
    tenant_id                INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    fiscal_year_start_month  INTEGER NOT NULL DEFAULT 1,
    books_start              DATE,
    lock_date                DATE,
    auto_post                BOOLEAN NOT NULL DEFAULT TRUE,
    posting_map              JSONB NOT NULL DEFAULT '{}'::jsonb,
    journal_approval_limit   NUMERIC(16,2) NOT NULL DEFAULT 0,
    payment_approval_limit   NUMERIC(16,2) NOT NULL DEFAULT 0,
    next_journal_no          INTEGER NOT NULL DEFAULT 1,
    next_auto_no             INTEGER NOT NULL DEFAULT 1,
    next_bill_no             INTEGER NOT NULL DEFAULT 1,
    next_claim_no            INTEGER NOT NULL DEFAULT 1,
    next_payment_no          INTEGER NOT NULL DEFAULT 1,
    next_asset_no            INTEGER NOT NULL DEFAULT 1,
    last_auto_post_at        TIMESTAMPTZ,
    updated_by_user_id       INTEGER REFERENCES users(id),
    updated_at               TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS acc_journals (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
    number               VARCHAR(30) NOT NULL,
    entry_date           DATE NOT NULL,
    description          VARCHAR(300) NOT NULL,
    kind                 VARCHAR(10) NOT NULL DEFAULT 'manual',
    source_type          VARCHAR(30),
    source_id            INTEGER,
    status               VARCHAR(12) NOT NULL DEFAULT 'posted',
    branch_id            INTEGER REFERENCES branches(id),
    total                NUMERIC(16,2) NOT NULL DEFAULT 0,
    reference            VARCHAR(60),
    created_by_user_id   INTEGER REFERENCES users(id),
    approved_by_user_id  INTEGER REFERENCES users(id),
    posted_at            TIMESTAMPTZ,
    reversal_of_id       INTEGER REFERENCES acc_journals(id),
    reversed_by_id       INTEGER REFERENCES acc_journals(id),
    reject_reason        TEXT,
    created_at           TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_acc_journal_number ON acc_journals (tenant_id, number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_acc_journal_source ON acc_journals (tenant_id, source_type, source_id)
    WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_acc_journals_tenant_date ON acc_journals (tenant_id, entry_date);
CREATE INDEX IF NOT EXISTS ix_acc_journals_status ON acc_journals (tenant_id, status);

CREATE TABLE IF NOT EXISTS acc_journal_lines (
    id            SERIAL PRIMARY KEY,
    journal_id    INTEGER NOT NULL REFERENCES acc_journals(id) ON DELETE CASCADE,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    account_id    INTEGER NOT NULL REFERENCES chart_of_accounts(id),
    debit         NUMERIC(16,2) NOT NULL DEFAULT 0,
    credit        NUMERIC(16,2) NOT NULL DEFAULT 0,
    description   VARCHAR(300),
    branch_id     INTEGER REFERENCES branches(id),
    loan_id       INTEGER REFERENCES loans(id),
    borrower_id   INTEGER REFERENCES borrowers(id),
    supplier_id   INTEGER,
    asset_id      INTEGER,
    employee_id   INTEGER,
    reconciled    BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_acc_lines_journal ON acc_journal_lines (journal_id);
CREATE INDEX IF NOT EXISTS ix_acc_lines_account ON acc_journal_lines (tenant_id, account_id);
CREATE INDEX IF NOT EXISTS ix_acc_lines_loan ON acc_journal_lines (loan_id) WHERE loan_id IS NOT NULL;

-- --------------------------------------------------------------------------- payables
CREATE TABLE IF NOT EXISTS acc_suppliers (
    id                          SERIAL PRIMARY KEY,
    tenant_id                   INTEGER NOT NULL REFERENCES tenants(id),
    name                        VARCHAR(160) NOT NULL,
    kra_pin                     VARCHAR(20),
    phone                       VARCHAR(20),
    email                       VARCHAR(160),
    payment_method              VARCHAR(15) DEFAULT 'bank',
    mpesa_number                VARCHAR(30),
    bank_name                   VARCHAR(80),
    bank_account                VARCHAR(40),
    default_expense_account_id  INTEGER REFERENCES chart_of_accounts(id),
    wht_rate                    NUMERIC(5,2) NOT NULL DEFAULT 0,
    notes                       TEXT,
    active                      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_acc_supplier_name ON acc_suppliers (tenant_id, name);

CREATE TABLE IF NOT EXISTS acc_bills (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
    number               VARCHAR(30) NOT NULL,
    supplier_id          INTEGER NOT NULL REFERENCES acc_suppliers(id),
    supplier_ref         VARCHAR(60),
    bill_date            DATE NOT NULL,
    due_date             DATE,
    branch_id            INTEGER REFERENCES branches(id),
    description          VARCHAR(300),
    subtotal             NUMERIC(16,2) NOT NULL DEFAULT 0,
    vat                  NUMERIC(16,2) NOT NULL DEFAULT 0,
    wht                  NUMERIC(16,2) NOT NULL DEFAULT 0,
    total                NUMERIC(16,2) NOT NULL DEFAULT 0,
    paid                 NUMERIC(16,2) NOT NULL DEFAULT 0,
    status               VARCHAR(12) NOT NULL DEFAULT 'pending',
    source_type          VARCHAR(30),
    source_id            INTEGER,
    journal_id           INTEGER REFERENCES acc_journals(id),
    created_by_user_id   INTEGER REFERENCES users(id),
    approved_by_user_id  INTEGER REFERENCES users(id),
    void_reason          TEXT,
    created_at           TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_acc_bill_number ON acc_bills (tenant_id, number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_acc_bill_source ON acc_bills (tenant_id, source_type, source_id)
    WHERE source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS acc_bill_lines (
    id           SERIAL PRIMARY KEY,
    bill_id      INTEGER NOT NULL REFERENCES acc_bills(id) ON DELETE CASCADE,
    account_id   INTEGER NOT NULL REFERENCES chart_of_accounts(id),
    description  VARCHAR(300),
    amount       NUMERIC(16,2) NOT NULL DEFAULT 0,
    vat_rate     NUMERIC(5,2) NOT NULL DEFAULT 0,
    vat          NUMERIC(16,2) NOT NULL DEFAULT 0,
    branch_id    INTEGER REFERENCES branches(id)
);

CREATE TABLE IF NOT EXISTS acc_expense_claims (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
    number               VARCHAR(30) NOT NULL,
    claimant_user_id     INTEGER NOT NULL REFERENCES users(id),
    claim_date           DATE NOT NULL,
    description          VARCHAR(300) NOT NULL,
    branch_id            INTEGER REFERENCES branches(id),
    total                NUMERIC(16,2) NOT NULL DEFAULT 0,
    paid                 NUMERIC(16,2) NOT NULL DEFAULT 0,
    status               VARCHAR(12) NOT NULL DEFAULT 'submitted',
    approved_by_user_id  INTEGER REFERENCES users(id),
    reject_reason        TEXT,
    journal_id           INTEGER REFERENCES acc_journals(id),
    created_at           TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_acc_claim_number ON acc_expense_claims (tenant_id, number);

CREATE TABLE IF NOT EXISTS acc_expense_claim_lines (
    id           SERIAL PRIMARY KEY,
    claim_id     INTEGER NOT NULL REFERENCES acc_expense_claims(id) ON DELETE CASCADE,
    account_id   INTEGER NOT NULL REFERENCES chart_of_accounts(id),
    description  VARCHAR(300),
    amount       NUMERIC(16,2) NOT NULL DEFAULT 0,
    receipt_ref  VARCHAR(60)
);

CREATE TABLE IF NOT EXISTS acc_payments_out (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
    number               VARCHAR(30) NOT NULL,
    kind                 VARCHAR(10) NOT NULL,
    bill_id              INTEGER REFERENCES acc_bills(id),
    claim_id             INTEGER REFERENCES acc_expense_claims(id),
    supplier_id          INTEGER REFERENCES acc_suppliers(id),
    payee                VARCHAR(160),
    amount               NUMERIC(16,2) NOT NULL,
    paid_on              DATE NOT NULL,
    account_id           INTEGER NOT NULL REFERENCES chart_of_accounts(id),
    method               VARCHAR(20),
    reference            VARCHAR(60),
    status               VARCHAR(12) NOT NULL DEFAULT 'pending',
    created_by_user_id   INTEGER REFERENCES users(id),
    approved_by_user_id  INTEGER REFERENCES users(id),
    reject_reason        TEXT,
    journal_id           INTEGER REFERENCES acc_journals(id),
    created_at           TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_acc_payment_number ON acc_payments_out (tenant_id, number);

CREATE TABLE IF NOT EXISTS acc_attachments (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    entity_type         VARCHAR(20) NOT NULL,
    entity_id           INTEGER NOT NULL,
    filename            VARCHAR(200) NOT NULL,
    storage_path        VARCHAR(400) NOT NULL,
    uploaded_by_user_id INTEGER REFERENCES users(id),
    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_acc_attachments_entity ON acc_attachments (tenant_id, entity_type, entity_id);

-- --------------------------------------------------------------------------- reconciliation
CREATE TABLE IF NOT EXISTS acc_bank_statements (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    account_id          INTEGER NOT NULL REFERENCES chart_of_accounts(id),
    filename            VARCHAR(200),
    period_from         DATE,
    period_to           DATE,
    closing_balance     NUMERIC(16,2),
    line_count          INTEGER NOT NULL DEFAULT 0,
    uploaded_by_user_id INTEGER REFERENCES users(id),
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS acc_bank_lines (
    id                  SERIAL PRIMARY KEY,
    statement_id        INTEGER NOT NULL REFERENCES acc_bank_statements(id) ON DELETE CASCADE,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    account_id          INTEGER NOT NULL REFERENCES chart_of_accounts(id),
    txn_date            DATE NOT NULL,
    description         VARCHAR(400),
    reference           VARCHAR(80),
    amount              NUMERIC(16,2) NOT NULL,
    fingerprint         VARCHAR(64) NOT NULL,
    status              VARCHAR(12) NOT NULL DEFAULT 'unmatched',
    journal_line_id     INTEGER REFERENCES acc_journal_lines(id),
    matched_by_user_id  INTEGER REFERENCES users(id),
    matched_at          TIMESTAMPTZ,
    note                TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_acc_bank_line ON acc_bank_lines (tenant_id, account_id, fingerprint);
CREATE INDEX IF NOT EXISTS ix_acc_bank_lines_status ON acc_bank_lines (tenant_id, account_id, status);

-- --------------------------------------------------------------------------- budgets
CREATE TABLE IF NOT EXISTS acc_budgets (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
    name                 VARCHAR(120) NOT NULL,
    fiscal_year          INTEGER NOT NULL,
    branch_id            INTEGER REFERENCES branches(id),
    status               VARCHAR(12) NOT NULL DEFAULT 'draft',
    created_by_user_id   INTEGER REFERENCES users(id),
    approved_by_user_id  INTEGER REFERENCES users(id),
    created_at           TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS acc_budget_lines (
    id          SERIAL PRIMARY KEY,
    budget_id   INTEGER NOT NULL REFERENCES acc_budgets(id) ON DELETE CASCADE,
    account_id  INTEGER NOT NULL REFERENCES chart_of_accounts(id),
    month       INTEGER NOT NULL,
    amount      NUMERIC(16,2) NOT NULL DEFAULT 0,
    CONSTRAINT uq_acc_budget_line UNIQUE (budget_id, account_id, month)
);

-- --------------------------------------------------------------------------- fixed assets
CREATE TABLE IF NOT EXISTS acc_asset_categories (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    name                VARCHAR(80) NOT NULL,
    asset_account_id    INTEGER NOT NULL REFERENCES chart_of_accounts(id),
    accum_account_id    INTEGER NOT NULL REFERENCES chart_of_accounts(id),
    expense_account_id  INTEGER NOT NULL REFERENCES chart_of_accounts(id),
    method              VARCHAR(20) NOT NULL DEFAULT 'straight_line',
    useful_life_months  INTEGER NOT NULL DEFAULT 36,
    rate_pct            NUMERIC(6,2) NOT NULL DEFAULT 0,
    CONSTRAINT uq_acc_asset_category UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS acc_assets (
    id                        SERIAL PRIMARY KEY,
    tenant_id                 INTEGER NOT NULL REFERENCES tenants(id),
    number                    VARCHAR(30) NOT NULL,
    name                      VARCHAR(160) NOT NULL,
    category_id               INTEGER NOT NULL REFERENCES acc_asset_categories(id),
    branch_id                 INTEGER REFERENCES branches(id),
    custodian                 VARCHAR(120),
    serial_no                 VARCHAR(80),
    purchase_date             DATE NOT NULL,
    in_service_date           DATE NOT NULL,
    cost                      NUMERIC(16,2) NOT NULL,
    residual_value            NUMERIC(16,2) NOT NULL DEFAULT 0,
    method                    VARCHAR(20) NOT NULL DEFAULT 'straight_line',
    useful_life_months        INTEGER NOT NULL DEFAULT 36,
    rate_pct                  NUMERIC(6,2) NOT NULL DEFAULT 0,
    accumulated_depreciation  NUMERIC(16,2) NOT NULL DEFAULT 0,
    status                    VARCHAR(20) NOT NULL DEFAULT 'active',
    funding                   VARCHAR(12) NOT NULL DEFAULT 'paid',
    supplier_id               INTEGER REFERENCES acc_suppliers(id),
    acquisition_journal_id    INTEGER REFERENCES acc_journals(id),
    disposed_on               DATE,
    disposal_proceeds         NUMERIC(16,2),
    disposal_journal_id       INTEGER REFERENCES acc_journals(id),
    notes                     TEXT,
    created_at                TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_acc_asset_number ON acc_assets (tenant_id, number);

CREATE TABLE IF NOT EXISTS acc_depreciation_runs (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    period              DATE NOT NULL,
    total               NUMERIC(16,2) NOT NULL DEFAULT 0,
    journal_id          INTEGER REFERENCES acc_journals(id),
    run_by_user_id      INTEGER REFERENCES users(id),
    created_at          TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_acc_depreciation_period UNIQUE (tenant_id, period)
);

CREATE TABLE IF NOT EXISTS acc_depreciation_lines (
    id        SERIAL PRIMARY KEY,
    run_id    INTEGER NOT NULL REFERENCES acc_depreciation_runs(id) ON DELETE CASCADE,
    asset_id  INTEGER NOT NULL REFERENCES acc_assets(id),
    amount    NUMERIC(16,2) NOT NULL
);

-- The accounting module is switched on per DCP like the others; existing DCPs
-- get it on, because they already used the GL export.
INSERT INTO tenant_modules (tenant_id, module_key, enabled)
SELECT t.id, 'accounting', TRUE FROM tenants t
ON CONFLICT (tenant_id, module_key) DO NOTHING;
