-- ============================================================================
-- Migration 011 — Authentication hardening (Phase 2)
--
-- AUTH-02: brute-force lockout bookkeeping on the users table.
-- AUTH-04: per-user token_version for stateless JWT revocation.
--
-- Idempotent: safe to re-run. Adds columns only if they are missing and keeps
-- the existing is_locked boolean (admin lock) untouched.
-- ============================================================================
SET search_path TO finyl_dcp, public;

ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_attempts INT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INT NOT NULL DEFAULT 0;
