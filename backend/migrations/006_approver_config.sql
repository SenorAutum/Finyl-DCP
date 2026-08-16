-- ============================================================================
-- Finyl-DCP migration 006 — Per-DCP configurable approver model.
--
-- Adds:
--   * approver_settings  — per-tenant, per-approval-type toggle of WHICH roles
--                          act as approvers. Absence of a row == fall back to
--                          the permission-derived default (backward compatible),
--                          so existing tenants keep working until customised.
--
-- ADDITIVE ONLY & idempotent (IF NOT EXISTS). Safe to re-run.
-- Applied against schema finyl_dcp.
-- ============================================================================
SET search_path TO finyl_dcp, public;

CREATE TABLE IF NOT EXISTS approver_settings (
    id                  serial PRIMARY KEY,
    tenant_id           integer NOT NULL REFERENCES tenants(id),
    approval_type       varchar(20) NOT NULL,   -- loan | client | disbursement | refund
    role                varchar(40) NOT NULL,   -- approver role key
    enabled             boolean NOT NULL DEFAULT true,
    updated_by_user_id  integer REFERENCES users(id),
    updated_at          timestamptz DEFAULT now()
);

-- One row per (tenant, approval_type, role) — the upsert key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_approver_settings_tenant_type_role
    ON approver_settings (tenant_id, approval_type, role);

CREATE INDEX IF NOT EXISTS ix_approver_settings_tenant
    ON approver_settings (tenant_id);
