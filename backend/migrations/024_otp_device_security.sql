-- Email+SMS OTP authentication, device binding, geo/time-fence tenant security config

DO $$ BEGIN
  CREATE TYPE otp_purpose AS ENUM ('login', 'password_reset', 'device_bind');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS otp_tokens (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    otp_hash    VARCHAR(200) NOT NULL,
    purpose     otp_purpose NOT NULL DEFAULT 'login',
    expires_at  TIMESTAMPTZ NOT NULL,
    consumed    BOOLEAN NOT NULL DEFAULT FALSE,
    attempts    INTEGER NOT NULL DEFAULT 0,
    ip          VARCHAR(45),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_otp_user    ON otp_tokens(user_id);
CREATE INDEX IF NOT EXISTS ix_otp_expiry  ON otp_tokens(expires_at);

CREATE TABLE IF NOT EXISTS user_devices (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_fingerprint  VARCHAR(500) NOT NULL,
    device_name         VARCHAR(200),
    platform            VARCHAR(50),
    registered_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    registered_by_ip    VARCHAR(45),
    UNIQUE (user_id, device_fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_user_devices_user ON user_devices(user_id);

CREATE TABLE IF NOT EXISTS tenant_security_config (
    id                      SERIAL PRIMARY KEY,
    tenant_id               INTEGER NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    require_otp             BOOLEAN NOT NULL DEFAULT FALSE,
    otp_channels            JSONB NOT NULL DEFAULT '["sms","email"]',
    device_binding_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
    geofence_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    geofence_radius_km      NUMERIC(8,3),
    geofence_center_lat     NUMERIC(10,8),
    geofence_center_lng     NUMERIC(11,8),
    time_fence_enabled      BOOLEAN NOT NULL DEFAULT FALSE,
    time_fence_start        TIME,
    time_fence_end          TIME,
    time_fence_timezone     VARCHAR(50) NOT NULL DEFAULT 'Africa/Nairobi',
    stipend_rate_kes_per_km NUMERIC(8,2),    -- configurable later, nullable now
    screenshot_on_action    BOOLEAN NOT NULL DEFAULT TRUE,
    activity_log_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by              INTEGER REFERENCES users(id)
);
