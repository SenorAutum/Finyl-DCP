-- ============================================================================
-- Migration 015 — PII encryption at rest (national_id) + blind index
--
-- Purely ADDITIVE and backward-compatible. No column is dropped or renamed and
-- no money-movement / state-machine path is touched. Two changes:
--
--   1. Widen the `national_id` TEXT storage on `borrowers` and
--      `client_next_of_kin` from VARCHAR(20) to TEXT. Encrypted values
--      (`enc:v1:` Fernet tokens, ~130+ chars) do not fit in VARCHAR(20).
--      Widening a varchar to text preserves every existing value, keeps the
--      NOT NULL constraint, and is a metadata-only change in PostgreSQL
--      (no table rewrite). Existing plaintext rows are untouched and are
--      encrypted in place by seeds/backfill_pii_encryption.py.
--
--   2. Add `borrowers.national_id_hash` (blind index — HMAC-SHA256 hex) plus a
--      btree index, so exact-match lookup / dedup on national_id keeps working
--      while the value itself is encrypted. Nullable with no default; populated
--      by the application on write and by the one-off backfill for existing rows.
--
-- Idempotent & safe to re-run:
--   * ALTER COLUMN ... TYPE text is guarded so it is a no-op once already text.
--   * ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--
-- Applied against schema finyl_dcp.
-- ============================================================================
SET search_path TO finyl_dcp, public;

-- ---------------------------------------------------------------------------
-- 1. Widen national_id columns to TEXT to hold Fernet ciphertext tokens.
--    Guarded so re-runs (or an already-text column) do nothing.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'finyl_dcp' AND table_name = 'borrowers'
          AND column_name = 'national_id' AND data_type <> 'text'
    ) THEN
        ALTER TABLE borrowers ALTER COLUMN national_id TYPE text;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'finyl_dcp' AND table_name = 'client_next_of_kin'
          AND column_name = 'national_id' AND data_type <> 'text'
    ) THEN
        ALTER TABLE client_next_of_kin ALTER COLUMN national_id TYPE text;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Blind-index column + index for encrypted-but-searchable national_id.
--    HMAC-SHA256 hex digest is 64 chars.
-- ---------------------------------------------------------------------------
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS national_id_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_borrowers_national_id_hash
    ON borrowers (national_id_hash);
