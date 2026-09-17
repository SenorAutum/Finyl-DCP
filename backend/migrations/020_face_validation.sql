-- Smile Identity face match + liveness validation log + enhanced OCR field mapping

DO $$ BEGIN
  CREATE TYPE face_validation_result AS ENUM ('pass', 'fail', 'manual_review');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS face_validation_logs (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id           INTEGER NOT NULL REFERENCES borrowers(id) ON DELETE CASCADE,
    validation_provider VARCHAR(100) NOT NULL DEFAULT 'smile_identity',
    match_score         NUMERIC(5,4),
    result              face_validation_result NOT NULL DEFAULT 'manual_review',
    liveness_pass       BOOLEAN,
    id_image_path       TEXT,
    selfie_image_path   TEXT,
    smile_job_id        VARCHAR(200),     -- Smile Identity job reference
    raw_response        JSONB,
    validated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    validated_by        INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS ix_face_val_tenant  ON face_validation_logs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_face_val_client  ON face_validation_logs(client_id);

-- Enhanced OCR metadata on client_documents
ALTER TABLE client_documents
    ADD COLUMN IF NOT EXISTS ocr_field_mapping JSONB,
    ADD COLUMN IF NOT EXISTS ocr_confidence    NUMERIC(5,4),
    ADD COLUMN IF NOT EXISTS ocr_version       VARCHAR(20);
