-- Enhanced CRM lead pipeline: Cold/Warm/Hot sub-stages, BM approval gate, conversion tracking

DO $$ BEGIN
  CREATE TYPE bm_approval_status AS ENUM ('pending', 'approved', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE lead_stage_detail AS ENUM ('cold', 'warm', 'hot');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE crm_leads
    ADD COLUMN IF NOT EXISTS stage_detail               lead_stage_detail NOT NULL DEFAULT 'cold',
    ADD COLUMN IF NOT EXISTS bm_approval_status         bm_approval_status,
    ADD COLUMN IF NOT EXISTS bm_approved_by             INTEGER REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS bm_approved_at             TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS bm_rejection_reason        TEXT,
    ADD COLUMN IF NOT EXISTS converted_to_client_id     INTEGER REFERENCES borrowers(id),
    ADD COLUMN IF NOT EXISTS conversion_triggered_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS conversion_validations     JSONB;  -- {mpesa: 'pass', id: 'pass', kyc: 'pending'}

CREATE INDEX IF NOT EXISTS ix_crm_leads_bm_status ON crm_leads(bm_approval_status);
CREATE INDEX IF NOT EXISTS ix_crm_leads_stage_det  ON crm_leads(stage_detail);
