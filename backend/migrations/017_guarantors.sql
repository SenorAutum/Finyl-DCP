-- Guarantor module: personal + business guarantors, their documents, and product-level loan category flags

-- ENUM types (idempotent guard)
DO $$ BEGIN
  CREATE TYPE guarantor_type_enum AS ENUM ('personal', 'business');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE loan_category_enum AS ENUM ('personal', 'business', 'secured', 'unsecured');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Guarantors table
CREATE TABLE IF NOT EXISTS guarantors (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id           INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    full_name           VARCHAR(300) NOT NULL,
    national_id         TEXT,                          -- EncryptedText (application-layer Fernet)
    national_id_hash    VARCHAR(200),                  -- blind index for lookups
    phone               VARCHAR(20),
    relationship        VARCHAR(100),
    address             TEXT,
    occupation          VARCHAR(200),
    monthly_income      NUMERIC(14,2),
    guarantor_type      guarantor_type_enum NOT NULL DEFAULT 'personal',
    mpesa_validated     BOOLEAN NOT NULL DEFAULT FALSE,
    mpesa_validation_name VARCHAR(300),
    mpesa_validated_at  TIMESTAMPTZ,
    kyc_status          VARCHAR(50) NOT NULL DEFAULT 'pending',
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_by          INTEGER REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_guarantors_tenant        ON guarantors(tenant_id);
CREATE INDEX IF NOT EXISTS ix_guarantors_client        ON guarantors(client_id);
CREATE INDEX IF NOT EXISTS ix_guarantors_national_hash ON guarantors(national_id_hash);

-- Guarantor business details (for business-type guarantors)
CREATE TABLE IF NOT EXISTS guarantor_business (
    id                      SERIAL PRIMARY KEY,
    tenant_id               INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    guarantor_id            INTEGER NOT NULL REFERENCES guarantors(id) ON DELETE CASCADE,
    business_name           VARCHAR(300) NOT NULL,
    business_reg_no         VARCHAR(100),
    operational_age_months  INTEGER,
    sector                  VARCHAR(100),
    directors               JSONB,
    monthly_turnover        NUMERIC(14,2),
    business_address        TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_guarantor_business_gid ON guarantor_business(guarantor_id);

-- Guarantor supporting documents
CREATE TABLE IF NOT EXISTS guarantor_documents (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    guarantor_id    INTEGER NOT NULL REFERENCES guarantors(id) ON DELETE CASCADE,
    doc_type        VARCHAR(100) NOT NULL,
    file_name       VARCHAR(500) NOT NULL,
    original_name   VARCHAR(500),
    mime_type       VARCHAR(100),
    size_bytes      INTEGER,
    storage_path    TEXT NOT NULL,
    ocr_applied     BOOLEAN NOT NULL DEFAULT FALSE,
    ocr_text        TEXT,                              -- EncryptedText application-layer
    ocr_field_mapping JSONB,
    uploaded_by     INTEGER REFERENCES users(id),
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_guarantor_docs_gid ON guarantor_documents(guarantor_id);

-- Extend products table
ALTER TABLE products ADD COLUMN IF NOT EXISTS loan_category loan_category_enum;
ALTER TABLE products ADD COLUMN IF NOT EXISTS requires_guarantor BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE products ADD COLUMN IF NOT EXISTS requires_collateral BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE products ADD COLUMN IF NOT EXISTS requires_next_of_kin BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE products ADD COLUMN IF NOT EXISTS business_op_age_required BOOLEAN NOT NULL DEFAULT FALSE;

-- Extend borrowers table
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS business_operational_age_months INTEGER;
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS is_business_client BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS date_of_birth_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS age_verified_at TIMESTAMPTZ;
