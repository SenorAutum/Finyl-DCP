-- ============================================================================
-- Finyl-DCP migration 007 — Per-DCP customizable SMS message templates.
--
-- Adds:
--   * sms_templates  — per-tenant, per-lifecycle-event editable SMS body with an
--                      active toggle. Each event is rendered from a template that
--                      supports {{placeholder}} tokens (first_name, amount, ...).
--                      Absence of a row for an event == fall back to the built-in
--                      DEFAULT_TEMPLATES in app/services/sms.py (backward safe).
--
-- Seeds a default row per event_key for EVERY existing tenant, using the wording
-- previously hardcoded in app/services/sms.py, converted to {{placeholder}}
-- tokens. Idempotent: ON CONFLICT DO NOTHING + IF NOT EXISTS everywhere.
-- Applied against schema finyl_dcp.
-- ============================================================================
SET search_path TO finyl_dcp;

CREATE TABLE IF NOT EXISTS sms_templates (
    id          serial PRIMARY KEY,
    tenant_id   integer NOT NULL REFERENCES tenants(id),
    event_key   varchar(40) NOT NULL,   -- loan_qualified | loan_disbursed | repayment_reminder | overdue_alert | defaulted | payment_receipt
    body        text NOT NULL,
    active      boolean NOT NULL DEFAULT true,
    updated_at  timestamptz DEFAULT now()
);

-- One row per (tenant, event_key) — the upsert key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_sms_templates_tenant_event
    ON sms_templates (tenant_id, event_key);

CREATE INDEX IF NOT EXISTS ix_sms_templates_tenant
    ON sms_templates (tenant_id);

-- Seed one default template per event for every existing tenant.
INSERT INTO sms_templates (tenant_id, event_key, body)
SELECT t.id, d.event_key, d.body
FROM tenants t
CROSS JOIN (VALUES
    ('loan_qualified',
     'Dear {{first_name}}, good news! Your loan {{account_number}} of KES {{amount}} has been APPROVED. It will be disbursed to your M-Pesa shortly. {{dcp_name}}.'),
    ('loan_disbursed',
     'Dear {{first_name}}, your loan {{account_number}} of KES {{amount}} has been disbursed to your M-Pesa. Repay by {{due_date}}. {{dcp_name}}.'),
    ('repayment_reminder',
     'Hi {{first_name}}, a friendly reminder: loan {{account_number}} balance KES {{balance}} is due in {{days_left}} day(s) on {{due_date}}. Pay via M-Pesa Paybill. {{dcp_name}}.'),
    ('overdue_alert',
     'Dear {{first_name}}, loan {{account_number}} is OVERDUE. Outstanding KES {{balance}}. Penalties may apply. Kindly settle to protect your credit score. {{dcp_name}}.'),
    ('defaulted',
     'Dear {{first_name}}, loan {{account_number}} has been marked DEFAULTED. Outstanding KES {{balance}}. Please contact us urgently to settle and protect your credit score. {{dcp_name}}.'),
    ('payment_receipt',
     'Payment received: KES {{amount}} for loan {{account_number}} (ref {{loan_ref}}). New balance KES {{balance}}. Thank you! {{dcp_name}}.')
) AS d(event_key, body)
ON CONFLICT (tenant_id, event_key) DO NOTHING;
