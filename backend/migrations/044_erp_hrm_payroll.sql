-- WP15: ERP human resources and Kenyan payroll, for every DCP and for Finyl.
--   * HR settings per DCP: statutory rates (PAYE bands and reliefs, NSSF, SHIF,
--     Housing Levy, NITA), numbering, how unpaid days are counted
--   * employees (ID, KRA PIN and bank account stored encrypted), recurring pay items
--   * leave types, balances and requests; daily attendance; documents; appraisals
--   * payroll runs and payslips, posted to the books
SET search_path TO finyl_dcp, public;

CREATE TABLE IF NOT EXISTS hr_settings (
    tenant_id            INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    statutory            JSONB NOT NULL DEFAULT '{}'::jsonb,
    deduct_absence       BOOLEAN NOT NULL DEFAULT FALSE,
    payroll_approval     BOOLEAN NOT NULL DEFAULT TRUE,
    next_employee_no     INTEGER NOT NULL DEFAULT 1,
    employee_prefix      VARCHAR(10) NOT NULL DEFAULT 'EMP',
    updated_by_user_id   INTEGER REFERENCES users(id),
    updated_at           TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hr_employees (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
    employee_no          VARCHAR(20) NOT NULL,
    user_id              INTEGER REFERENCES users(id),
    staff_id             INTEGER REFERENCES staff(id),
    first_name           VARCHAR(80) NOT NULL,
    last_name            VARCHAR(80) NOT NULL,
    other_names          VARCHAR(80),
    national_id          TEXT,
    kra_pin              TEXT,
    nssf_no              VARCHAR(30),
    shif_no              VARCHAR(30),
    phone                VARCHAR(20),
    email                VARCHAR(160),
    date_of_birth        DATE,
    gender               VARCHAR(10),
    branch_id            INTEGER REFERENCES branches(id),
    department           VARCHAR(80),
    job_title            VARCHAR(120),
    employment_type      VARCHAR(15) NOT NULL DEFAULT 'permanent',
    hire_date            DATE NOT NULL,
    probation_end        DATE,
    contract_end         DATE,
    status               VARCHAR(15) NOT NULL DEFAULT 'active',
    termination_date     DATE,
    termination_reason   TEXT,
    basic_salary         NUMERIC(14,2) NOT NULL DEFAULT 0,
    pay_method           VARCHAR(10) NOT NULL DEFAULT 'bank',
    bank_name            VARCHAR(80),
    bank_branch          VARCHAR(80),
    bank_account         TEXT,
    mpesa_phone          VARCHAR(20),
    manager_employee_id  INTEGER REFERENCES hr_employees(id),
    next_of_kin_name     VARCHAR(120),
    next_of_kin_phone    VARCHAR(20),
    disability_exempt    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_hr_employee_no ON hr_employees (tenant_id, employee_no);
CREATE UNIQUE INDEX IF NOT EXISTS uq_hr_employee_user ON hr_employees (tenant_id, user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_hr_employees_status ON hr_employees (tenant_id, status);

CREATE TABLE IF NOT EXISTS hr_pay_items (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    employee_id   INTEGER NOT NULL REFERENCES hr_employees(id) ON DELETE CASCADE,
    kind          VARCHAR(12) NOT NULL,
    name          VARCHAR(80) NOT NULL,
    amount        NUMERIC(14,2) NOT NULL,
    taxable       BOOLEAN NOT NULL DEFAULT TRUE,
    pre_tax       BOOLEAN NOT NULL DEFAULT FALSE,
    insurance     BOOLEAN NOT NULL DEFAULT FALSE,
    account_id    INTEGER REFERENCES chart_of_accounts(id),
    start_month   DATE,
    end_month     DATE,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hr_pay_items_employee ON hr_pay_items (employee_id);

CREATE TABLE IF NOT EXISTS hr_leave_types (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    name               VARCHAR(60) NOT NULL,
    days_per_year      NUMERIC(6,2) NOT NULL DEFAULT 0,
    paid               BOOLEAN NOT NULL DEFAULT TRUE,
    accrues            BOOLEAN NOT NULL DEFAULT TRUE,
    carry_forward_max  NUMERIC(6,2) NOT NULL DEFAULT 0,
    gender             VARCHAR(10),
    needs_document     BOOLEAN NOT NULL DEFAULT FALSE,
    calendar_days      BOOLEAN NOT NULL DEFAULT FALSE,
    active             BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_hr_leave_type UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS hr_leave_balances (
    id             SERIAL PRIMARY KEY,
    tenant_id      INTEGER NOT NULL REFERENCES tenants(id),
    employee_id    INTEGER NOT NULL REFERENCES hr_employees(id) ON DELETE CASCADE,
    leave_type_id  INTEGER NOT NULL REFERENCES hr_leave_types(id),
    year           INTEGER NOT NULL,
    carried        NUMERIC(6,2) NOT NULL DEFAULT 0,
    adjustment     NUMERIC(6,2) NOT NULL DEFAULT 0,
    note           TEXT,
    CONSTRAINT uq_hr_leave_balance UNIQUE (employee_id, leave_type_id, year)
);

CREATE TABLE IF NOT EXISTS hr_leave_requests (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    employee_id         INTEGER NOT NULL REFERENCES hr_employees(id) ON DELETE CASCADE,
    leave_type_id       INTEGER NOT NULL REFERENCES hr_leave_types(id),
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    days                NUMERIC(6,2) NOT NULL,
    reason              TEXT,
    status              VARCHAR(12) NOT NULL DEFAULT 'pending',
    requested_by_user_id INTEGER REFERENCES users(id),
    decided_by_user_id  INTEGER REFERENCES users(id),
    decided_at          TIMESTAMPTZ,
    decision_note       TEXT,
    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hr_leave_requests ON hr_leave_requests (tenant_id, status);

CREATE TABLE IF NOT EXISTS hr_attendance (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    employee_id        INTEGER NOT NULL REFERENCES hr_employees(id) ON DELETE CASCADE,
    day                DATE NOT NULL,
    status             VARCHAR(10) NOT NULL,
    clock_in           VARCHAR(5),
    clock_out          VARCHAR(5),
    note               VARCHAR(200),
    marked_by_user_id  INTEGER REFERENCES users(id),
    CONSTRAINT uq_hr_attendance UNIQUE (employee_id, day)
);
CREATE INDEX IF NOT EXISTS ix_hr_attendance_day ON hr_attendance (tenant_id, day);

CREATE TABLE IF NOT EXISTS hr_documents (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    employee_id         INTEGER NOT NULL REFERENCES hr_employees(id) ON DELETE CASCADE,
    doc_type            VARCHAR(30) NOT NULL,
    filename            VARCHAR(200) NOT NULL,
    storage_path        VARCHAR(400) NOT NULL,
    expires_on          DATE,
    uploaded_by_user_id INTEGER REFERENCES users(id),
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hr_appraisals (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    employee_id         INTEGER NOT NULL REFERENCES hr_employees(id) ON DELETE CASCADE,
    period              VARCHAR(30) NOT NULL,
    reviewer_user_id    INTEGER REFERENCES users(id),
    goals               JSONB NOT NULL DEFAULT '[]'::jsonb,
    score               NUMERIC(4,2),
    strengths           TEXT,
    improvements        TEXT,
    status              VARCHAR(15) NOT NULL DEFAULT 'draft',
    employee_comment    TEXT,
    acknowledged_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hr_payroll_runs (
    id                     SERIAL PRIMARY KEY,
    tenant_id              INTEGER NOT NULL REFERENCES tenants(id),
    period                 DATE NOT NULL,
    status                 VARCHAR(12) NOT NULL DEFAULT 'draft',
    employees              INTEGER NOT NULL DEFAULT 0,
    totals                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id     INTEGER REFERENCES users(id),
    approved_by_user_id    INTEGER REFERENCES users(id),
    approved_at            TIMESTAMPTZ,
    journal_id             INTEGER REFERENCES acc_journals(id),
    paid_on                DATE,
    paid_from_account_id   INTEGER REFERENCES chart_of_accounts(id),
    payment_journal_id     INTEGER REFERENCES acc_journals(id),
    note                   TEXT,
    created_at             TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_hr_payroll_period ON hr_payroll_runs (tenant_id, period)
    WHERE status <> 'cancelled';

CREATE TABLE IF NOT EXISTS hr_payslips (
    id                  SERIAL PRIMARY KEY,
    run_id              INTEGER NOT NULL REFERENCES hr_payroll_runs(id) ON DELETE CASCADE,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    employee_id         INTEGER NOT NULL REFERENCES hr_employees(id),
    days_paid           NUMERIC(5,2) NOT NULL,
    days_in_month       INTEGER NOT NULL,
    unpaid_days         NUMERIC(5,2) NOT NULL DEFAULT 0,
    basic               NUMERIC(14,2) NOT NULL DEFAULT 0,
    items               JSONB NOT NULL DEFAULT '[]'::jsonb,
    gross               NUMERIC(14,2) NOT NULL DEFAULT 0,
    taxable_pay         NUMERIC(14,2) NOT NULL DEFAULT 0,
    nssf_employee       NUMERIC(14,2) NOT NULL DEFAULT 0,
    nssf_employer       NUMERIC(14,2) NOT NULL DEFAULT 0,
    shif                NUMERIC(14,2) NOT NULL DEFAULT 0,
    housing_employee    NUMERIC(14,2) NOT NULL DEFAULT 0,
    housing_employer    NUMERIC(14,2) NOT NULL DEFAULT 0,
    pension             NUMERIC(14,2) NOT NULL DEFAULT 0,
    income_tax          NUMERIC(14,2) NOT NULL DEFAULT 0,
    personal_relief     NUMERIC(14,2) NOT NULL DEFAULT 0,
    insurance_relief    NUMERIC(14,2) NOT NULL DEFAULT 0,
    paye                NUMERIC(14,2) NOT NULL DEFAULT 0,
    other_deductions    NUMERIC(14,2) NOT NULL DEFAULT 0,
    nita                NUMERIC(14,2) NOT NULL DEFAULT 0,
    net                 NUMERIC(14,2) NOT NULL DEFAULT 0,
    branch_id           INTEGER REFERENCES branches(id),
    CONSTRAINT uq_hr_payslip UNIQUE (run_id, employee_id)
);

INSERT INTO tenant_modules (tenant_id, module_key, enabled)
SELECT t.id, 'hrm', TRUE FROM tenants t
ON CONFLICT (tenant_id, module_key) DO NOTHING;
