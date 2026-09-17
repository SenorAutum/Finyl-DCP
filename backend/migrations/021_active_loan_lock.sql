-- Active loan row-lock on borrowers, dual-tier edit request workflow, immutable audit log rule

DO $$ BEGIN
  CREATE TYPE edit_tier_enum AS ENUM ('primary', 'secondary');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE edit_request_status AS ENUM ('pending', 'approved', 'rejected', 'cancelled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS client_edit_requests (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id       INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    edit_tier       edit_tier_enum NOT NULL,
    requested_by    INTEGER NOT NULL REFERENCES users(id),
    approved_by     INTEGER REFERENCES users(id),
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at     TIMESTAMPTZ,
    rejected_at     TIMESTAMPTZ,
    field_changes   JSONB NOT NULL,   -- {field_name: {old_value, new_value}}
    supporting_docs JSONB,            -- [{doc_type, storage_path, file_name}]
    status          edit_request_status NOT NULL DEFAULT 'pending',
    rejection_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_edit_req_tenant  ON client_edit_requests(tenant_id);
CREATE INDEX IF NOT EXISTS ix_edit_req_client  ON client_edit_requests(client_id);
CREATE INDEX IF NOT EXISTS ix_edit_req_status  ON client_edit_requests(status);

CREATE TABLE IF NOT EXISTS loan_active_lock_log (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    loan_id         INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    locked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_by       INTEGER REFERENCES users(id),
    unlock_at       TIMESTAMPTZ,
    unlock_reason   TEXT
);
CREATE INDEX IF NOT EXISTS ix_lock_log_loan ON loan_active_lock_log(loan_id);

-- Borrower edit lock columns
ALTER TABLE borrowers
    ADD COLUMN IF NOT EXISTS edit_locked        BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS edit_locked_reason VARCHAR(200);

-- Loan active lock column
ALTER TABLE loans
    ADD COLUMN IF NOT EXISTS active_lock BOOLEAN NOT NULL DEFAULT FALSE;

-- Extend audit_logs for CBK compliance (data_before/after, category)
ALTER TABLE audit_logs
    ADD COLUMN IF NOT EXISTS action_category VARCHAR(100),
    ADD COLUMN IF NOT EXISTS data_before     JSONB,
    ADD COLUMN IF NOT EXISTS data_after      JSONB;

-- Immutable audit log: block UPDATE and DELETE at DB level
CREATE OR REPLACE RULE audit_log_no_update AS
    ON UPDATE TO audit_logs DO INSTEAD NOTHING;
CREATE OR REPLACE RULE audit_log_no_delete AS
    ON DELETE TO audit_logs DO INSTEAD NOTHING;
