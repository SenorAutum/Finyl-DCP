-- ============================================================================
-- Finyl-DCP migration 010 — M-Pesa idempotency guards (MPESA-03).
--
-- The system moves real money via Daraja B2C/C2B/STK. Callbacks and manual
-- reconciliations can be delivered more than once (Safaricom retries, operator
-- double-clicks), so the same M-Pesa reference must never post twice. Application
-- code now checks for an existing row before inserting, but a DATABASE-level
-- guarantee is the backstop that makes double-posting impossible even under a
-- race between two concurrent callback deliveries.
--
-- This adds a PARTIAL UNIQUE index on (tenant_id, mpesa_ref) for both money
-- ledgers — payment_transactions and repayments — scoped `WHERE mpesa_ref IS NOT
-- NULL` so the many legitimate NULL refs (pending STK pushes, manual entries with
-- no ref) are unaffected. Unique per tenant (not globally) because references are
-- only guaranteed unique within a single DCP's shortcode.
--
-- SCHEMA-ONLY, idempotent (IF NOT EXISTS), safe to re-run. Applied against
-- schema finyl_dcp.
-- ============================================================================
SET search_path TO finyl_dcp, public;

-- Partial unique index: one payment transaction per (tenant, M-Pesa ref).
CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_transactions_tenant_ref
    ON payment_transactions (tenant_id, mpesa_ref)
    WHERE mpesa_ref IS NOT NULL;

-- Partial unique index: one repayment per (tenant, M-Pesa ref).
CREATE UNIQUE INDEX IF NOT EXISTS uq_repayments_tenant_ref
    ON repayments (tenant_id, mpesa_ref)
    WHERE mpesa_ref IS NOT NULL;
