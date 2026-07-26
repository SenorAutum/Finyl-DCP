-- Finyl-DCP migration 002 — Clients & KYC onboarding
-- ADDITIVE ONLY: no column or table is dropped or renamed. The `borrowers`
-- table keeps its name (every analytics view / FK references it); the API and
-- UI expose it as "client".
-- Safe to re-run (IF NOT EXISTS everywhere).

SET search_path TO finyl_dcp, public;

-- 1. National-ID / KYC detail columns on the client record ------------------
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS serial_number        VARCHAR(30);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS middle_name          VARCHAR(60);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS district_of_birth    VARCHAR(60);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS place_of_issue       VARCHAR(60);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS date_of_issue        DATE;
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS district             VARCHAR(60);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS division             VARCHAR(60);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS location             VARCHAR(60);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS sub_location         VARCHAR(60);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS current_credit_rating VARCHAR(20);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS is_active            BOOLEAN DEFAULT TRUE;
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS onboarded_by         VARCHAR(120);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS approved_by_user_id  INTEGER REFERENCES users(id);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS mpesa_validated      BOOLEAN DEFAULT FALSE;
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS mpesa_validation_name VARCHAR(120);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS mpesa_validated_at   TIMESTAMP;
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS ekyc_status          VARCHAR(20);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS ekyc_reference       VARCHAR(60);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS ekyc_checked_at      TIMESTAMP;

UPDATE borrowers SET is_active = TRUE WHERE is_active IS NULL;
UPDATE borrowers SET mpesa_validated = FALSE WHERE mpesa_validated IS NULL;

-- 2. Mobile wallets ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS client_mobile_wallets (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    client_id     INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    mobile_number VARCHAR(20),
    wallet_number VARCHAR(30),
    operator      VARCHAR(30),
    active        BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_client_mobile_wallets_client ON client_mobile_wallets (client_id);
CREATE INDEX IF NOT EXISTS ix_client_mobile_wallets_tenant ON client_mobile_wallets (tenant_id);

-- 3. Next of kin ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS client_next_of_kin (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    client_id     INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    full_name     VARCHAR(120),
    relationship  VARCHAR(30),
    mobile_number VARCHAR(20),
    national_id   VARCHAR(20),
    address       VARCHAR(160),
    active        BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_client_next_of_kin_client ON client_next_of_kin (client_id);
CREATE INDEX IF NOT EXISTS ix_client_next_of_kin_tenant ON client_next_of_kin (tenant_id);

-- 4. Documents --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS client_documents (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    client_id     INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    file_name     VARCHAR(200),
    original_name VARCHAR(200),
    mime_type     VARCHAR(120),
    size_bytes    INTEGER DEFAULT 0,
    doc_type      VARCHAR(40) DEFAULT 'other',
    storage_path  TEXT,
    ocr_applied   BOOLEAN DEFAULT FALSE,
    ocr_text      TEXT,
    uploaded_at   TIMESTAMP DEFAULT NOW(),
    uploaded_by   VARCHAR(120)
);
CREATE INDEX IF NOT EXISTS ix_client_documents_client ON client_documents (client_id);
CREATE INDEX IF NOT EXISTS ix_client_documents_tenant ON client_documents (tenant_id);
