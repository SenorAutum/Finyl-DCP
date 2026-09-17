-- Wallet auto-lock after M-Pesa validation + alternate phone/operator fields

ALTER TABLE client_mobile_wallets
    ADD COLUMN IF NOT EXISTS wallet_locked   BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS locked_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS locked_by       VARCHAR(100);   -- 'system' or user email

ALTER TABLE borrowers
    ADD COLUMN IF NOT EXISTS alt_phone              VARCHAR(20),
    ADD COLUMN IF NOT EXISTS alt_phone_operator     VARCHAR(50),
    ADD COLUMN IF NOT EXISTS alt_phone_validated    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS alt_phone_validated_at TIMESTAMPTZ;
