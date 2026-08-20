-- ============================================================================
-- Migration 012 — Editable RBAC + per-DCP configuration surface
--
-- PART B (editable RBAC): two tenant-scoped tables that layer on top of the
-- static permission catalog in app/core/permissions.py WITHOUT mutating it:
--   * role_permission_overrides — per (tenant, role, permission) grant/revoke.
--     effective_permissions(role) = static base  +  granted overrides
--                                                -  revoked overrides.
--     super_admin is never affected (wildcard, resolved in code, never stored).
--   * custom_roles — tenant-defined roles (base permission set = empty) AND
--     optional label overrides for built-in roles. A row whose role_key matches
--     a built-in role only relabels it; a brand-new role_key defines a custom
--     role whose permissions come entirely from role_permission_overrides.
--
-- PART A (per-DCP Daraja credentials) reuses the EXISTING
-- tenant_integration_config table (created in migration 004) with
-- integration='daraja'; secret values are stored ENCRYPTED (Fernet enc:v1:
-- tokens) inside the JSON `secrets` column by the application. No schema change
-- is required for Daraja, so none is made here.
--
-- Idempotent: safe to re-run. Creates tables/indexes only if missing.
-- ============================================================================
SET search_path TO finyl_dcp, public;

-- --- Per-(tenant, role, permission) override -------------------------------
CREATE TABLE IF NOT EXISTS role_permission_overrides (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    role                VARCHAR(40) NOT NULL,
    permission_key      VARCHAR(60) NOT NULL,
    granted             BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by_user_id  INTEGER REFERENCES users(id),
    updated_at          TIMESTAMP DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_role_perm_override
    ON role_permission_overrides (tenant_id, role, permission_key);

CREATE INDEX IF NOT EXISTS ix_role_perm_override_tenant
    ON role_permission_overrides (tenant_id);

-- --- Tenant-defined roles / built-in role relabelling ----------------------
CREATE TABLE IF NOT EXISTS custom_roles (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    role_key            VARCHAR(40) NOT NULL,
    label               VARCHAR(120) NOT NULL,
    updated_by_user_id  INTEGER REFERENCES users(id),
    created_at          TIMESTAMP DEFAULT now(),
    updated_at          TIMESTAMP DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_custom_role_key
    ON custom_roles (tenant_id, role_key);

CREATE INDEX IF NOT EXISTS ix_custom_role_tenant
    ON custom_roles (tenant_id);
