-- Third-party API client provisioning with hashed key storage and scoped access

CREATE TABLE IF NOT EXISTS third_party_api_clients (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_name     VARCHAR(200) NOT NULL,
    api_key_hash    VARCHAR(500) NOT NULL,           -- bcrypt hash
    api_key_prefix  VARCHAR(12) NOT NULL,            -- first 12 chars for display/lookup
    scopes          JSONB NOT NULL DEFAULT '[]',     -- ["statements.ingest","kyc.read"]
    rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at    TIMESTAMPTZ,
    created_by      INTEGER REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_api_clients_tenant ON third_party_api_clients(tenant_id);
CREATE INDEX IF NOT EXISTS ix_api_clients_prefix ON third_party_api_clients(api_key_prefix);
CREATE UNIQUE INDEX IF NOT EXISTS uix_api_clients_prefix ON third_party_api_clients(api_key_prefix);
