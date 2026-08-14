-- ============================================================================
-- Finyl-DCP migration 005 — Integrations module (SMS billing, DLR, test logs).
--
-- Adds:
--   * sms_rate_cards         — configurable per-message SMS pricing (sell/cost).
--   * sms_logs billing/DLR   — delivery_status, delivered_at, billable + price
--                              snapshot columns (sell/cost/margin).
--   * integration_test_logs  — auditable history of 'Test connection' runs.
--
-- ADDITIVE ONLY & idempotent (IF NOT EXISTS / guarded). Safe to re-run.
-- Applied against schema finyl_dcp.
-- ============================================================================
SET search_path TO finyl_dcp, public;

-- --- SMS rate card (configurable pricing) -----------------------------------
CREATE TABLE IF NOT EXISTS sms_rate_cards (
    id              serial PRIMARY KEY,
    sell_price_kes  numeric(10,4) NOT NULL,
    cost_price_kes  numeric(10,4) NOT NULL,
    currency        varchar(3) DEFAULT 'KES',
    effective_from  timestamptz DEFAULT now(),
    active          boolean DEFAULT true,
    note            varchar(200),
    created_at      timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sms_rate_cards_active ON sms_rate_cards(active);

-- Seed exactly ONE active rate (sell 0.80 / cost 0.50) if none exists yet.
INSERT INTO sms_rate_cards (sell_price_kes, cost_price_kes, currency, active, note)
SELECT 0.80, 0.50, 'KES', true, 'Initial platform SMS rate'
WHERE NOT EXISTS (SELECT 1 FROM sms_rate_cards WHERE active = true);

-- --- sms_logs: delivery reporting + per-message billing ---------------------
ALTER TABLE sms_logs ADD COLUMN IF NOT EXISTS delivery_status varchar(15) DEFAULT 'unknown';
ALTER TABLE sms_logs ADD COLUMN IF NOT EXISTS delivered_at    timestamp;
ALTER TABLE sms_logs ADD COLUMN IF NOT EXISTS billable        boolean DEFAULT false;
ALTER TABLE sms_logs ADD COLUMN IF NOT EXISTS sell_price_kes  numeric(10,4);
ALTER TABLE sms_logs ADD COLUMN IF NOT EXISTS cost_price_kes  numeric(10,4);
ALTER TABLE sms_logs ADD COLUMN IF NOT EXISTS margin_kes      numeric(10,4);

-- Backfill: sent messages become billable at the active rate; others not billed.
UPDATE sms_logs s
SET billable = true,
    sell_price_kes = r.sell_price_kes,
    cost_price_kes = r.cost_price_kes,
    margin_kes     = r.sell_price_kes - r.cost_price_kes
FROM (SELECT sell_price_kes, cost_price_kes FROM sms_rate_cards
      WHERE active = true ORDER BY effective_from DESC LIMIT 1) r
WHERE s.status = 'sent' AND s.billable IS NOT TRUE;

UPDATE sms_logs
SET billable = false, sell_price_kes = NULL, cost_price_kes = NULL, margin_kes = NULL
WHERE status <> 'sent' AND (billable IS TRUE OR sell_price_kes IS NOT NULL);

-- --- Integration test-run audit log -----------------------------------------
CREATE TABLE IF NOT EXISTS integration_test_logs (
    id               serial PRIMARY KEY,
    integration_key  varchar(40) NOT NULL,
    ok               boolean DEFAULT false,
    detail           text,
    run_by_user_id   integer REFERENCES users(id),
    run_by_email     varchar(120),
    created_at       timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_integration_test_logs_key ON integration_test_logs(integration_key);
CREATE INDEX IF NOT EXISTS ix_integration_test_logs_created ON integration_test_logs(created_at);
