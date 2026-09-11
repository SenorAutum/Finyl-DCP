-- System activity logging and event-triggered screenshot audit trail

DO $$ BEGIN
  CREATE TYPE capture_trigger_enum AS ENUM ('action', 'periodic', 'anomaly', 'manual');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS activity_logs (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id          UUID,
    event_type          VARCHAR(100) NOT NULL,   -- 'edit_submitted','loan_approved','disburse_triggered', etc.
    event_detail        JSONB,
    ip                  VARCHAR(45),
    device_fingerprint  VARCHAR(500),
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_activity_tenant  ON activity_logs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_activity_user    ON activity_logs(user_id);
CREATE INDEX IF NOT EXISTS ix_activity_time    ON activity_logs(recorded_at);
CREATE INDEX IF NOT EXISTS ix_activity_event   ON activity_logs(event_type);

CREATE TABLE IF NOT EXISTS screenshot_logs (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id          UUID,
    storage_path        TEXT NOT NULL,
    thumbnail_path      TEXT,
    capture_trigger     capture_trigger_enum NOT NULL DEFAULT 'action',
    event_type          VARCHAR(100),            -- the triggering event
    activity_log_id     INTEGER REFERENCES activity_logs(id),
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_screenshots_tenant ON screenshot_logs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_screenshots_user   ON screenshot_logs(user_id);
CREATE INDEX IF NOT EXISTS ix_screenshots_time   ON screenshot_logs(recorded_at);
