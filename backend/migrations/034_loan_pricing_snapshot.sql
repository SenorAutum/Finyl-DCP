-- WP5 (F23): a loan keeps the price agreed when it was created, so the quote,
-- the payout and the balance always match.
--   loans.pricing_snapshot  fees, excise duty, payout, balance (JSON)
--   loans.fees_in_balance   fees + excise added to the balance (products that
--                           add fees to the balance; 0 when fees are deducted
--                           from the payout)
-- Additive only. Loans created before this migration get a snapshot computed
-- from their product the first time they are paid out; loans already paid out
-- are not changed.
ALTER TABLE loans ADD COLUMN IF NOT EXISTS pricing_snapshot JSONB;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS fees_in_balance NUMERIC(12, 2) DEFAULT 0;

-- WP5 (F25): penalties added to a balance were not recorded anywhere, so the
-- integrity check could not tell a penalty from an error. Recorded from now on.
ALTER TABLE loans ADD COLUMN IF NOT EXISTS penalties_charged NUMERIC(12, 2) DEFAULT 0;
