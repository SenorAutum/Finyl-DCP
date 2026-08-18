-- ============================================================================
-- Finyl-DCP migration 008 — Per-DCP SMS automation configuration.
--
-- Adds:
--   * sms_automation_settings — per-tenant switch for whether lifecycle SMS
--     reminders/alerts are dispatched automatically (the daily batch), and the
--     hour of day the batch runs. Absence of automation config means a DCP is
--     handled manually via the "Send now" action.
--
-- send_hour is an INTEGER hour (0-23) in the PLATFORM/SERVER timezone — the
-- all-tenants runner fires a DCP's batch when the current server hour matches
-- send_hour. (No per-tenant timezone: the tenants model carries none.)
--
-- Seeds one row per existing tenant with automation_enabled=true, send_hour=7,
-- preserving the current behaviour (a 07:00 daily batch for everyone).
--
-- ADDITIVE ONLY & idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING). Safe to
-- re-run. Applied against schema finyl_dcp.
-- ============================================================================
SET search_path TO finyl_dcp, public;

CREATE TABLE IF NOT EXISTS sms_automation_settings (
    id                  serial PRIMARY KEY,
    tenant_id           integer NOT NULL REFERENCES tenants(id),
    automation_enabled  boolean NOT NULL DEFAULT true,
    send_hour           integer NOT NULL DEFAULT 7,   -- 0-23, server-local hour
    updated_by_user_id  integer REFERENCES users(id),
    updated_at          timestamptz DEFAULT now(),
    CONSTRAINT ck_sms_automation_send_hour CHECK (send_hour >= 0 AND send_hour <= 23)
);

-- One row per tenant — the upsert key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_sms_automation_settings_tenant
    ON sms_automation_settings (tenant_id);

-- Seed defaults for every existing tenant (preserve current 07:00 behaviour).
INSERT INTO sms_automation_settings (tenant_id, automation_enabled, send_hour)
SELECT t.id, true, 7 FROM tenants t
ON CONFLICT (tenant_id) DO NOTHING;
