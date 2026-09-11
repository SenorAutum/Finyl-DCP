-- M-Pesa Ratiba standing-order consents, bank statement analysis, Promise-to-Pay workflow, collection efficiency

DO $$ BEGIN
  CREATE TYPE ptp_contact_method AS ENUM ('call', 'site_visit');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE ptp_status AS ENUM ('pending', 'honored', 'broken', 'partial', 'rescheduled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS mpesa_ratiba_consents (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    loan_id             INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    client_id           INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    phone               VARCHAR(20) NOT NULL,
    consent_given       BOOLEAN NOT NULL DEFAULT FALSE,
    consent_at          TIMESTAMPTZ,
    consent_ip          VARCHAR(45),
    ratiba_ref          VARCHAR(200),
    ratiba_status       VARCHAR(50),               -- 'active','cancelled','pending','failed'
    deduction_amount    NUMERIC(14,2),
    deduction_day       INTEGER,                   -- day of month (1-28)
    raw_response        JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ratiba_loan   ON mpesa_ratiba_consents(loan_id);
CREATE INDEX IF NOT EXISTS ix_ratiba_client ON mpesa_ratiba_consents(client_id);

CREATE TABLE IF NOT EXISTS bank_statements (
    id                          SERIAL PRIMARY KEY,
    tenant_id                   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id                   INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    bank_name                   VARCHAR(100) NOT NULL,
    account_number_hash         VARCHAR(200),       -- hashed, never stored plain
    period_start                DATE,
    period_end                  DATE,
    months_covered              INTEGER,
    transactions_count          INTEGER,
    avg_monthly_credit          NUMERIC(14,2),
    avg_monthly_debit           NUMERIC(14,2),
    net_monthly_cashflow        NUMERIC(14,2),
    affordability_score         NUMERIC(5,2),
    comfortable_installment     NUMERIC(14,2),
    summary                     JSONB,              -- parsed transaction categories
    detected_lenders            JSONB,              -- detected competing loan deductions
    tampering_suspected         BOOLEAN NOT NULL DEFAULT FALSE,
    integrity_flags             JSONB,              -- specific anomalies found
    source_filename             TEXT,
    created_by                  INTEGER REFERENCES users(id),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_bank_stmt_client ON bank_statements(client_id);
CREATE INDEX IF NOT EXISTS ix_bank_stmt_tenant ON bank_statements(tenant_id);

CREATE TABLE IF NOT EXISTS promise_to_pay (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    loan_id             INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    client_id           INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    logged_by           INTEGER NOT NULL REFERENCES users(id),
    ptp_date            DATE NOT NULL,
    amount              NUMERIC(14,2) NOT NULL,
    contact_method      ptp_contact_method NOT NULL DEFAULT 'call',
    status              ptp_status NOT NULL DEFAULT 'pending',
    reminder_sent_at    JSONB,                      -- [{channel:'sms'|'email', sent_at:'...'}]
    honored_at          TIMESTAMPTZ,
    honored_amount      NUMERIC(14,2),
    broken_at           TIMESTAMPTZ,
    rescheduled_ptp_id  INTEGER REFERENCES promise_to_pay(id),
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ptp_loan    ON promise_to_pay(loan_id);
CREATE INDEX IF NOT EXISTS ix_ptp_officer ON promise_to_pay(logged_by);
CREATE INDEX IF NOT EXISTS ix_ptp_date    ON promise_to_pay(ptp_date);
CREATE INDEX IF NOT EXISTS ix_ptp_status  ON promise_to_pay(status);

CREATE TABLE IF NOT EXISTS collection_efficiency (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    officer_user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_month        DATE NOT NULL,              -- first day of month
    ptps_logged         INTEGER NOT NULL DEFAULT 0,
    ptps_honored        INTEGER NOT NULL DEFAULT 0,
    ptps_broken         INTEGER NOT NULL DEFAULT 0,
    ptps_partial        INTEGER NOT NULL DEFAULT 0,
    amount_promised     NUMERIC(14,2) NOT NULL DEFAULT 0,
    amount_collected    NUMERIC(14,2) NOT NULL DEFAULT 0,
    efficiency_pct      NUMERIC(5,2),
    calls_made          INTEGER NOT NULL DEFAULT 0,
    visits_made         INTEGER NOT NULL DEFAULT 0,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, officer_user_id, period_month)
);
CREATE INDEX IF NOT EXISTS ix_collect_eff_tenant  ON collection_efficiency(tenant_id);
CREATE INDEX IF NOT EXISTS ix_collect_eff_officer ON collection_efficiency(officer_user_id);
CREATE INDEX IF NOT EXISTS ix_collect_eff_month   ON collection_efficiency(period_month);
