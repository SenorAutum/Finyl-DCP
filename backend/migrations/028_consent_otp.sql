-- SMS OTP client consent workflow.
-- Extends the otp_purpose enum with 'consent' and adds a consent audit log that
-- records every SMS-OTP consent a client gives back to their officer.

ALTER TYPE otp_purpose ADD VALUE IF NOT EXISTS 'consent';

CREATE TABLE IF NOT EXISTS client_consent_logs (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id     INTEGER REFERENCES borrowers(id) ON DELETE SET NULL,
    lead_id       INTEGER REFERENCES crm_leads(id) ON DELETE SET NULL,
    phone         VARCHAR(20) NOT NULL,
    otp_token_id  INTEGER REFERENCES otp_tokens(id) ON DELETE SET NULL,
    officer_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    consent_text  TEXT,
    ip            VARCHAR(45),
    consented_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_ccl_tenant ON client_consent_logs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_ccl_client ON client_consent_logs(client_id);
CREATE INDEX IF NOT EXISTS ix_ccl_lead   ON client_consent_logs(lead_id);
