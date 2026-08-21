-- ============================================================================
-- Migration 013 — Remove residual float from currency-adjacent rate columns
--
-- MPESA-07 follow-up: interest/penalty rates were stored as double precision,
-- which re-introduces binary-float drift into currency computations that derive
-- from them (total_due, interest/principal split, penalty accrual). Convert the
-- three rate columns to NUMERIC(6,3) so all money-adjacent arithmetic is exact.
--
-- Idempotent & backward-compatible: each ALTER only runs when the column is
-- still 'double precision', preserves existing defaults, and never drops/renames.
-- ============================================================================
SET search_path TO finyl_dcp, public;

DO $$
BEGIN
    -- products.interest_rate
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'finyl_dcp' AND table_name = 'products'
                 AND column_name = 'interest_rate' AND data_type = 'double precision') THEN
        ALTER TABLE products
            ALTER COLUMN interest_rate TYPE NUMERIC(6, 3) USING interest_rate::numeric,
            ALTER COLUMN interest_rate SET DEFAULT 10;
    END IF;

    -- products.penalty_rate
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'finyl_dcp' AND table_name = 'products'
                 AND column_name = 'penalty_rate' AND data_type = 'double precision') THEN
        ALTER TABLE products
            ALTER COLUMN penalty_rate TYPE NUMERIC(6, 3) USING penalty_rate::numeric,
            ALTER COLUMN penalty_rate SET DEFAULT 1;
    END IF;

    -- loans.interest_rate
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'finyl_dcp' AND table_name = 'loans'
                 AND column_name = 'interest_rate' AND data_type = 'double precision') THEN
        ALTER TABLE loans
            ALTER COLUMN interest_rate TYPE NUMERIC(6, 3) USING interest_rate::numeric;
    END IF;
END $$;
