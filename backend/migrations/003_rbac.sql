-- ============================================================================
-- Finyl-DCP migration 003 — RBAC overhaul (permission-driven roles, scoping,
-- approval thresholds, maker-checker, audit trail, HQ reporting artefacts).
--
-- ADDITIVE ONLY. No existing column is dropped or renamed; every new column is
-- nullable / defaulted so existing analytics and seeded data are untouched.
-- Idempotent: safe to re-run.
-- ============================================================================
SET search_path TO finyl_dcp, public;

-- --- users: account-state + scoping links -----------------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS branch_id            integer REFERENCES branches(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS region_id            integer REFERENCES regions(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_locked            boolean DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS force_password_reset boolean DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deactivated_at       timestamp;

-- --- borrowers (clients): portfolio owner + profile approval state ----------
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS officer_staff_id integer REFERENCES staff(id);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS profile_status   varchar(20) DEFAULT 'approved';

-- --- loans: approval / maker-checker / escalation ---------------------------
ALTER TABLE loans ADD COLUMN IF NOT EXISTS approved_by_user_id   integer REFERENCES users(id);
ALTER TABLE loans ADD COLUMN IF NOT EXISTS escalation_level      varchar(10);
ALTER TABLE loans ADD COLUMN IF NOT EXISTS decision_note         text;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS disbursed_by_user_id  integer REFERENCES users(id);

-- --- approval_thresholds ----------------------------------------------------
CREATE TABLE IF NOT EXISTS approval_thresholds (
    id             serial PRIMARY KEY,
    tenant_id      integer NOT NULL REFERENCES tenants(id),
    scope_type     varchar(10)  NOT NULL,            -- role | branch | region
    scope_key      varchar(60)  NOT NULL,            -- role name OR branch/region id (text)
    threshold_type varchar(20)  NOT NULL,            -- loan_approval | disbursement | refund
    amount         numeric(14,2) NOT NULL DEFAULT 0,
    created_at     timestamp DEFAULT now(),
    updated_at     timestamp DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_thresholds_tenant ON approval_thresholds(tenant_id);

-- --- audit_logs -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id          serial PRIMARY KEY,
    tenant_id   integer REFERENCES tenants(id),
    user_id     integer REFERENCES users(id),
    user_email  varchar(160),
    action      varchar(60) NOT NULL,
    entity_type varchar(40),
    entity_id   varchar(40),
    details     jsonb DEFAULT '{}'::jsonb,
    ip          varchar(50),
    created_at  timestamp DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_tenant  ON audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_audit_action  ON audit_logs(action);
CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_logs(created_at);

-- --- pending_approvals (maker-checker) --------------------------------------
CREATE TABLE IF NOT EXISTS pending_approvals (
    id              serial PRIMARY KEY,
    tenant_id       integer NOT NULL REFERENCES tenants(id),
    action_type     varchar(20) NOT NULL,            -- disbursement | refund
    loan_id         integer REFERENCES loans(id),
    amount          numeric(14,2) NOT NULL,
    phone           varchar(20),
    reason          varchar(200),
    status          varchar(20) DEFAULT 'pending_approval',
    maker_user_id   integer NOT NULL REFERENCES users(id),
    maker_at        timestamp DEFAULT now(),
    checker_user_id integer REFERENCES users(id),
    checker_at      timestamp,
    details         jsonb DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_pending_tenant ON pending_approvals(tenant_id);
CREATE INDEX IF NOT EXISTS ix_pending_status ON pending_approvals(status);

-- --- report_schedules -------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_schedules (
    id          serial PRIMARY KEY,
    tenant_id   integer NOT NULL REFERENCES tenants(id),
    user_id     integer REFERENCES users(id),
    name        varchar(120) NOT NULL,
    report_type varchar(40)  NOT NULL,
    frequency   varchar(20)  DEFAULT 'weekly',
    recipients  varchar(400),
    active      boolean DEFAULT true,
    last_run_at timestamp,
    created_at  timestamp DEFAULT now()
);

-- --- report_templates -------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_templates (
    id         serial PRIMARY KEY,
    tenant_id  integer NOT NULL REFERENCES tenants(id),
    user_id    integer REFERENCES users(id),
    name       varchar(120) NOT NULL,
    definition jsonb DEFAULT '{}'::jsonb,
    created_at timestamp DEFAULT now()
);

-- --- anomaly_flags ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS anomaly_flags (
    id          serial PRIMARY KEY,
    tenant_id   integer NOT NULL REFERENCES tenants(id),
    user_id     integer REFERENCES users(id),
    entity_type varchar(40),
    entity_id   varchar(40),
    note        text NOT NULL,
    status      varchar(20) DEFAULT 'open',
    created_at  timestamp DEFAULT now()
);
