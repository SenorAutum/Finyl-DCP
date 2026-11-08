-- WP7: DCP administration, product approval and device requests.

-- A sign-in from an unregistered device (when the DCP requires registered
-- devices) leaves a request for an administrator to approve, instead of a
-- dead end. pending = requested, not yet approved.
ALTER TABLE user_devices ADD COLUMN IF NOT EXISTS pending BOOLEAN NOT NULL DEFAULT FALSE;

-- A DCP proposes a new loan product or a change; it goes live only when a
-- Finyl super admin approves it (pricing changes need CBK approval).
CREATE TABLE IF NOT EXISTS product_change_requests (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
    product_id      INTEGER REFERENCES products(id),
    action          VARCHAR(10) NOT NULL,              -- create | update
    proposed        JSONB NOT NULL,                    -- only the fields that change
    current_values  JSONB,                             -- those fields as they were
    reason          TEXT,
    status          VARCHAR(12) NOT NULL DEFAULT 'pending',   -- pending | approved | rejected | withdrawn
    requested_by    INTEGER NOT NULL REFERENCES users(id),
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by      INTEGER REFERENCES users(id),
    decided_at      TIMESTAMPTZ,
    decision_note   TEXT
);
CREATE INDEX IF NOT EXISTS ix_product_change_requests_tenant_status
    ON product_change_requests (tenant_id, status);
-- One open proposal per product at a time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_product_change_requests_open
    ON product_change_requests (product_id) WHERE status = 'pending' AND product_id IS NOT NULL;
