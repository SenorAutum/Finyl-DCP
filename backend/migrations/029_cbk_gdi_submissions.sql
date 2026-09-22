-- CBK GDI monthly submission log.
-- Records every dataset submission to the Central Bank of Kenya Granular Data
-- Interface (GDI): one row per (tenant, reporting_month, dataset) attempt,
-- tracking request ids, status transitions and the raw CBK response.

CREATE TABLE IF NOT EXISTS cbk_submission_logs (
    id                SERIAL PRIMARY KEY,
    tenant_id         INTEGER NOT NULL,
    reporting_month   DATE NOT NULL,
    dataset_name      VARCHAR(100) NOT NULL,
    request_id        UUID,
    cbk_request_id    VARCHAR(200),
    status            VARCHAR(50) NOT NULL DEFAULT 'pending',
    rows_submitted    INTEGER NOT NULL DEFAULT 0,
    response_body     JSONB,
    error_detail      TEXT,
    submitted_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    submitted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status_checked_at TIMESTAMPTZ,
    accepted_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_cbk_log_tenant ON cbk_submission_logs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_cbk_log_month  ON cbk_submission_logs(tenant_id, reporting_month);
CREATE UNIQUE INDEX IF NOT EXISTS ix_cbk_log_req ON cbk_submission_logs(request_id);
