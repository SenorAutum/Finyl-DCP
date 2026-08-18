-- ============================================================================
-- Finyl-DCP migration 009 — restore CRM pipeline module flag for Mular.
--
-- The CRM & Field Sales pipeline (/api/v1/crm/board) is gated by the tenant
-- feature flag `crm` via require_module("crm"). The Mular Credit tenant (the
-- licensed / primary pilot DCP) is seeded with EVERY module enabled — only
-- Jenga Micro deliberately ships with `crm` OFF to demo enforcement. Live data
-- had drifted so that Mular's `crm` flag was disabled, which made
-- require_module("crm") return HTTP 403 ("Module 'crm' is not enabled for this
-- tenant") for the front-line operational roles (Relationship Officers, Branch
-- & Regional Managers) that use the pipeline — i.e. the pipeline "wasn't
-- working".
--
-- This migration re-enables the `crm` module for the Mular tenant, inserting
-- the row if it is missing. DATA-ONLY, idempotent, safe to re-run. It does NOT
-- touch any other tenant (Jenga's `crm` stays OFF by design).
-- Applied against schema finyl_dcp.
-- ============================================================================
SET search_path TO finyl_dcp, public;

-- Insert the flag row if it does not exist yet (enabled), otherwise leave the
-- row in place; the UPDATE below then guarantees it is ON.
INSERT INTO tenant_modules (tenant_id, module_key, enabled)
SELECT t.id, 'crm', TRUE
FROM tenants t
WHERE t.code = 'MULAR'
  AND NOT EXISTS (
    SELECT 1 FROM tenant_modules tm
    WHERE tm.tenant_id = t.id AND tm.module_key = 'crm'
  );

-- Ensure the flag is ON for Mular (covers the drifted / disabled row).
UPDATE tenant_modules
SET enabled = TRUE
WHERE module_key = 'crm'
  AND tenant_id = (SELECT id FROM tenants WHERE code = 'MULAR');
