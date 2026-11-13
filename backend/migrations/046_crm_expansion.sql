-- WP17: CRM expansion.
--   * interactions with clients and leads (calls, visits, SMS, WhatsApp, meetings, notes)
--   * follow-up tasks assigned to staff
--   * client segments (saved filters) and SMS campaigns to them, approved by a
--     second person, respecting opt-outs and marketing consent
SET search_path TO finyl_dcp, public;

CREATE TABLE IF NOT EXISTS crm_interactions (
    id               SERIAL PRIMARY KEY,
    tenant_id        INTEGER NOT NULL REFERENCES tenants(id),
    borrower_id      INTEGER REFERENCES borrowers(id),
    lead_id          INTEGER REFERENCES crm_leads(id),
    kind             VARCHAR(12) NOT NULL,
    direction        VARCHAR(8) NOT NULL DEFAULT 'out',
    subject          VARCHAR(200),
    notes            TEXT,
    outcome          VARCHAR(30),
    happened_at      TIMESTAMPTZ NOT NULL,
    user_id          INTEGER REFERENCES users(id),
    created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_crm_interactions_borrower ON crm_interactions (borrower_id);
CREATE INDEX IF NOT EXISTS ix_crm_interactions_lead ON crm_interactions (lead_id);
CREATE INDEX IF NOT EXISTS ix_crm_interactions_tenant ON crm_interactions (tenant_id, happened_at);

CREATE TABLE IF NOT EXISTS crm_tasks (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    title               VARCHAR(200) NOT NULL,
    notes               TEXT,
    due_date            DATE NOT NULL,
    priority            VARCHAR(8) NOT NULL DEFAULT 'normal',
    status              VARCHAR(10) NOT NULL DEFAULT 'open',
    assigned_user_id    INTEGER NOT NULL REFERENCES users(id),
    borrower_id         INTEGER REFERENCES borrowers(id),
    lead_id             INTEGER REFERENCES crm_leads(id),
    loan_id             INTEGER REFERENCES loans(id),
    interaction_id      INTEGER REFERENCES crm_interactions(id),
    created_by_user_id  INTEGER REFERENCES users(id),
    completed_at        TIMESTAMPTZ,
    completion_note     TEXT,
    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_crm_tasks_assigned ON crm_tasks (tenant_id, assigned_user_id, status);

CREATE TABLE IF NOT EXISTS crm_segments (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    name                VARCHAR(120) NOT NULL,
    description         TEXT,
    rules               JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id  INTEGER REFERENCES users(id),
    created_at          TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_crm_segment_name UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS crm_campaigns (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
    name                 VARCHAR(120) NOT NULL,
    segment_id           INTEGER NOT NULL REFERENCES crm_segments(id),
    purpose              VARCHAR(10) NOT NULL DEFAULT 'marketing',
    message              TEXT NOT NULL,
    status               VARCHAR(12) NOT NULL DEFAULT 'draft',
    send_at              TIMESTAMPTZ,
    created_by_user_id   INTEGER REFERENCES users(id),
    approved_by_user_id  INTEGER REFERENCES users(id),
    approved_at          TIMESTAMPTZ,
    reject_reason        TEXT,
    recipients           INTEGER NOT NULL DEFAULT 0,
    sent                 INTEGER NOT NULL DEFAULT 0,
    failed               INTEGER NOT NULL DEFAULT 0,
    skipped              INTEGER NOT NULL DEFAULT 0,
    started_at           TIMESTAMPTZ,
    finished_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_crm_campaigns_status ON crm_campaigns (tenant_id, status);

CREATE TABLE IF NOT EXISTS crm_campaign_recipients (
    id           SERIAL PRIMARY KEY,
    campaign_id  INTEGER NOT NULL REFERENCES crm_campaigns(id) ON DELETE CASCADE,
    borrower_id  INTEGER NOT NULL REFERENCES borrowers(id),
    phone        VARCHAR(20),
    status       VARCHAR(12) NOT NULL,
    reason       VARCHAR(60),
    sms_log_id   INTEGER REFERENCES sms_logs(id),
    CONSTRAINT uq_crm_campaign_recipient UNIQUE (campaign_id, borrower_id)
);
