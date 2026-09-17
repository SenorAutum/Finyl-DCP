-- KYC escalation matrix and per-tenant validation preferences

DO $$ BEGIN
  CREATE TYPE kyc_escalation_status AS ENUM ('open', 'resolved', 'overridden');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS kyc_mismatch_escalations (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id       INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    mismatch_type   VARCHAR(100) NOT NULL,   -- e.g. 'name_mismatch', 'dob_mismatch', 'phone_mismatch'
    mismatch_detail TEXT,
    logged_by       INTEGER REFERENCES users(id),
    escalated_to    INTEGER REFERENCES users(id),
    status          kyc_escalation_status NOT NULL DEFAULT 'open',
    resolution_note TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_kyc_esc_tenant   ON kyc_mismatch_escalations(tenant_id);
CREATE INDEX IF NOT EXISTS ix_kyc_esc_client   ON kyc_mismatch_escalations(client_id);
CREATE INDEX IF NOT EXISTS ix_kyc_esc_status   ON kyc_mismatch_escalations(status);

CREATE TABLE IF NOT EXISTS tenant_validation_prefs (
    id                              SERIAL PRIMARY KEY,
    tenant_id                       INTEGER NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    age_check_mandatory             BOOLEAN NOT NULL DEFAULT TRUE,
    mpesa_validation_mandatory      BOOLEAN NOT NULL DEFAULT TRUE,
    face_validation_mandatory       BOOLEAN NOT NULL DEFAULT FALSE,
    alt_phone_validation_mandatory  BOOLEAN NOT NULL DEFAULT FALSE,
    crb_check_mandatory             BOOLEAN NOT NULL DEFAULT FALSE,
    guarantor_validation_mandatory  BOOLEAN NOT NULL DEFAULT FALSE,
    ocr_mandatory                   BOOLEAN NOT NULL DEFAULT TRUE,
    custom_rules                    JSONB,
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by                      INTEGER REFERENCES users(id)
);
