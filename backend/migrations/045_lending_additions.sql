-- WP16: lending additions.
--   * write-off (and recoveries afterwards), waivers of interest or fees,
--     rescheduling, and top-ups — each asked for by one person and approved
--     by another (loan_actions)
--   * collateral register (logbooks, title deeds, equipment, savings ...)
--   * group lending: groups, members and roles, meetings; loans tagged to a group
SET search_path TO finyl_dcp, public;

ALTER TABLE loans ADD COLUMN IF NOT EXISTS written_off_at      TIMESTAMPTZ;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS written_off_amount  NUMERIC(12,2) NOT NULL DEFAULT 0;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS interest_waived     NUMERIC(12,2) NOT NULL DEFAULT 0;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS fees_waived         NUMERIC(12,2) NOT NULL DEFAULT 0;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS restructure_count   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS restructured_at     TIMESTAMPTZ;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS topup_of_loan_id    INTEGER REFERENCES loans(id);
ALTER TABLE loans ADD COLUMN IF NOT EXISTS group_id            INTEGER;

CREATE TABLE IF NOT EXISTS loan_actions (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
    loan_id              INTEGER NOT NULL REFERENCES loans(id),
    kind                 VARCHAR(15) NOT NULL,
    status               VARCHAR(12) NOT NULL DEFAULT 'pending',
    details              JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason               TEXT NOT NULL,
    requested_by_user_id INTEGER REFERENCES users(id),
    decided_by_user_id   INTEGER REFERENCES users(id),
    decided_at           TIMESTAMPTZ,
    decision_note        TEXT,
    journal_id           INTEGER REFERENCES acc_journals(id),
    created_at           TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_loan_actions_status ON loan_actions (tenant_id, status);
CREATE INDEX IF NOT EXISTS ix_loan_actions_loan ON loan_actions (loan_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_loan_action_pending ON loan_actions (loan_id, kind) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS loan_collateral (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
    borrower_id          INTEGER NOT NULL REFERENCES borrowers(id),
    loan_id              INTEGER REFERENCES loans(id),
    kind                 VARCHAR(20) NOT NULL,
    description          VARCHAR(300) NOT NULL,
    identifier           VARCHAR(80),
    estimated_value      NUMERIC(14,2) NOT NULL DEFAULT 0,
    forced_sale_value    NUMERIC(14,2),
    valued_by            VARCHAR(120),
    valued_on            DATE,
    location             VARCHAR(200),
    insured              BOOLEAN NOT NULL DEFAULT FALSE,
    insurer              VARCHAR(120),
    policy_no            VARCHAR(60),
    insurance_expiry     DATE,
    status               VARCHAR(12) NOT NULL DEFAULT 'held',
    received_on          DATE NOT NULL,
    released_on          DATE,
    status_note          TEXT,
    created_by_user_id   INTEGER REFERENCES users(id),
    created_at           TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_loan_collateral_loan ON loan_collateral (loan_id);
CREATE INDEX IF NOT EXISTS ix_loan_collateral_borrower ON loan_collateral (tenant_id, borrower_id);

CREATE TABLE IF NOT EXISTS lending_groups (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    code               VARCHAR(20) NOT NULL,
    name               VARCHAR(120) NOT NULL,
    branch_id          INTEGER REFERENCES branches(id),
    officer_staff_id   INTEGER REFERENCES staff(id),
    registration_no    VARCHAR(60),
    meeting_day        VARCHAR(10),
    meeting_frequency  VARCHAR(12) NOT NULL DEFAULT 'weekly',
    meeting_place      VARCHAR(200),
    status             VARCHAR(12) NOT NULL DEFAULT 'active',
    formed_on          DATE,
    created_at         TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_lending_group_code ON lending_groups (tenant_id, code);

CREATE TABLE IF NOT EXISTS lending_group_members (
    id           SERIAL PRIMARY KEY,
    tenant_id    INTEGER NOT NULL REFERENCES tenants(id),
    group_id     INTEGER NOT NULL REFERENCES lending_groups(id) ON DELETE CASCADE,
    borrower_id  INTEGER NOT NULL REFERENCES borrowers(id),
    role         VARCHAR(12) NOT NULL DEFAULT 'member',
    joined_on    DATE NOT NULL,
    left_on      DATE,
    active       BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_group_member_active ON lending_group_members (group_id, borrower_id) WHERE active;

CREATE TABLE IF NOT EXISTS lending_group_meetings (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    group_id           INTEGER NOT NULL REFERENCES lending_groups(id) ON DELETE CASCADE,
    held_on            DATE NOT NULL,
    present            JSONB NOT NULL DEFAULT '[]'::jsonb,
    collected          NUMERIC(14,2) NOT NULL DEFAULT 0,
    notes              TEXT,
    recorded_by_user_id INTEGER REFERENCES users(id),
    created_at         TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_group_meeting UNIQUE (group_id, held_on)
);
