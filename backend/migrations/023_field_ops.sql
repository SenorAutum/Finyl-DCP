-- GPS staff tracking, daily task planning, client home geo-referencing, business site photos, location alerts

DO $$ BEGIN
  CREATE TYPE location_alert_type AS ENUM ('disabled', 'geofence_breach', 'time_breach');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE daily_task_status AS ENUM ('planned', 'active', 'completed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS staff_gps_logs (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    latitude            NUMERIC(10,8) NOT NULL,
    longitude           NUMERIC(11,8) NOT NULL,
    accuracy_meters     NUMERIC(8,2),
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    task_type           VARCHAR(50),     -- 'site_visit', 'client_visit', 'office', 'transit'
    task_ref_id         INTEGER,
    device_id           VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS ix_gps_logs_tenant    ON staff_gps_logs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_gps_logs_user      ON staff_gps_logs(user_id);
CREATE INDEX IF NOT EXISTS ix_gps_logs_timestamp ON staff_gps_logs(timestamp);

CREATE TABLE IF NOT EXISTS staff_daily_tasks (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_date       DATE NOT NULL,
    planned_visits  JSONB,            -- [{client_id, address, lat, lng, purpose}]
    actual_visits   JSONB,            -- [{client_id, arrived_at, left_at, lat, lng, outcome}]
    distance_km     NUMERIC(8,3),
    stipend_kes     NUMERIC(12,2),
    status          daily_task_status NOT NULL DEFAULT 'planned',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, task_date)
);
CREATE INDEX IF NOT EXISTS ix_daily_tasks_tenant ON staff_daily_tasks(tenant_id);
CREATE INDEX IF NOT EXISTS ix_daily_tasks_user   ON staff_daily_tasks(user_id);

CREATE TABLE IF NOT EXISTS location_alerts (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    alert_type      location_alert_type NOT NULL,
    detail          TEXT,
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_by INTEGER REFERENCES users(id),
    acknowledged_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_loc_alerts_tenant ON location_alerts(tenant_id);
CREATE INDEX IF NOT EXISTS ix_loc_alerts_user   ON location_alerts(user_id);

CREATE TABLE IF NOT EXISTS client_home_geo (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id       INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    latitude        NUMERIC(10,8) NOT NULL,
    longitude       NUMERIC(11,8) NOT NULL,
    accuracy_meters NUMERIC(8,2),
    captured_by     INTEGER REFERENCES users(id),
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    photo_path      TEXT
);
CREATE INDEX IF NOT EXISTS ix_client_home_geo_client ON client_home_geo(client_id);

CREATE TABLE IF NOT EXISTS business_site_photos (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id   INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    visit_id    INTEGER REFERENCES site_visits(id),
    photo_path  TEXT NOT NULL,
    caption     VARCHAR(200),
    uploaded_by INTEGER REFERENCES users(id),
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_biz_photos_client ON business_site_photos(client_id);
