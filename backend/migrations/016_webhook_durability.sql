-- ============================================================================
-- Finyl-DCP migration 016 — Durable Daraja webhook ingestion + dead-letter queue
--
-- Purely ADDITIVE and backward-compatible. No existing column/table/index is
-- dropped, renamed or altered, and no money-movement / state-machine path is
-- touched. It creates ONE new audit/durability table used by the M-Pesa
-- callback pipeline so a webhook delivery is never lost:
--
--   Every incoming Daraja webhook (/mpesa/{token}/b2c-result, b2c-timeout,
--   stk-callback, c2b-callback) is persisted here as `received` BEFORE the
--   existing processing runs. On success the row flips to `processed`; on an
--   internal error it flips to `failed` with the error + a scheduled
--   next_retry_at (exponential backoff). The in-process APScheduler retries due
--   `failed` rows idempotently and escalates to `dead` after the configured max
--   attempts (WEBHOOK_MAX_ATTEMPTS), at which point an alert is emitted. The
--   endpoint ALWAYS returns Safaricom's expected acknowledgement (HTTP 200) even
--   on internal failure, so Safaricom does not hammer us with its own retries
--   while our durable queue owns the retry.
--
-- PII / DATA MINIMISATION (Kenya ODPC):
--   `raw_payload` is the verbatim webhook body and MAY CONTAIN PII (payer phone
--   number / MSISDN, names). A scheduled purge (WEBHOOK_RAW_RETENTION_HOURS,
--   default 168h = 7 days) NULLs the raw_payload of successfully-`processed`
--   rows older than the window, retaining only non-PII metadata (status,
--   attempts, timestamps, shortcode) for audit. Failed/dead rows are retained
--   with their body until resolved so operators can diagnose.
--
-- MULTI-PAYBILL:
--   `tenant_id` is NULLABLE — it is resolved from the matched disbursement
--   transaction (B2C) or from BusinessShortCode -> tenant (C2B/B2C) during
--   processing. A row that cannot be resolved is kept with tenant_id = NULL and
--   status = failed and is alerted, never silently dropped.
--
-- Idempotent (CREATE ... IF NOT EXISTS), safe to re-run. Applied against schema
-- finyl_dcp.
-- ============================================================================
SET search_path TO finyl_dcp, public;

CREATE TABLE IF NOT EXISTS mpesa_webhook_events (
    id              BIGSERIAL PRIMARY KEY,
    -- Resolved lazily; NULL until the tenant is determined (or unresolved).
    tenant_id       INTEGER REFERENCES tenants(id),
    -- Which callback received this: b2c-result | b2c-timeout | stk-callback | c2b-callback
    endpoint        VARCHAR(40) NOT NULL,
    -- BusinessShortCode extracted from the payload when available (multi-paybill routing).
    shortcode       VARCHAR(20),
    received_at     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    processed_at    TIMESTAMP,
    -- Verbatim webhook body. MAY CONTAIN PII -> purged after retention window
    -- for successfully-processed rows (see WEBHOOK_RAW_RETENTION_HOURS).
    raw_payload     JSONB,
    -- received | processed | failed | dead
    processing_status VARCHAR(20) NOT NULL DEFAULT 'received',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_retry_at   TIMESTAMP
);

-- Retry worker hot path: find due failed events quickly.
CREATE INDEX IF NOT EXISTS ix_mpesa_webhook_events_status_retry
    ON mpesa_webhook_events (processing_status, next_retry_at);

-- Purge / audit lookups by age.
CREATE INDEX IF NOT EXISTS ix_mpesa_webhook_events_received_at
    ON mpesa_webhook_events (received_at);

-- Per-tenant audit browsing.
CREATE INDEX IF NOT EXISTS ix_mpesa_webhook_events_tenant
    ON mpesa_webhook_events (tenant_id);

COMMENT ON TABLE mpesa_webhook_events IS
    'Durable ingestion log + dead-letter queue for Daraja M-Pesa webhooks. '
    'raw_payload may contain PII and is purged for processed rows after '
    'WEBHOOK_RAW_RETENTION_HOURS (ODPC data minimisation).';
COMMENT ON COLUMN mpesa_webhook_events.raw_payload IS
    'Verbatim webhook body — MAY CONTAIN PII (payer MSISDN/names). Purged for '
    'processed rows older than the configured retention window.';
