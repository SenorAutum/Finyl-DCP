-- WP2: per-DCP paybill matching, customer numbers, manual payments.
--
-- Additive only: new nullable columns + one partial unique index. No existing
-- column is dropped or narrowed (suspense reason/status are WIDENED).
--
--   borrowers.customer_number   7-digit paybill reference (6 random + Luhn check
--                               digit), unique per DCP. Backfilled below for
--                               every existing borrower.
--   repayments.fee_component    share of a payment applied to penalties/fees.
--   suspense_entries.*          bill_ref (what the payer typed), payer_name,
--                               paid_at (M-Pesa transaction time) and a
--                               suggested_loan_id hint for the reviewer.

ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS customer_number VARCHAR(12);
CREATE UNIQUE INDEX IF NOT EXISTS uq_borrowers_tenant_customer_number
    ON borrowers (tenant_id, customer_number)
    WHERE customer_number IS NOT NULL;

ALTER TABLE repayments ADD COLUMN IF NOT EXISTS fee_component NUMERIC(12, 2) DEFAULT 0;

ALTER TABLE suspense_entries ALTER COLUMN reason TYPE VARCHAR(30);
ALTER TABLE suspense_entries ALTER COLUMN status TYPE VARCHAR(20);
ALTER TABLE suspense_entries ADD COLUMN IF NOT EXISTS bill_ref VARCHAR(60);
ALTER TABLE suspense_entries ADD COLUMN IF NOT EXISTS payer_name VARCHAR(120);
ALTER TABLE suspense_entries ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP;
ALTER TABLE suspense_entries ADD COLUMN IF NOT EXISTS suggested_loan_id INTEGER REFERENCES loans(id);

-- Backfill a customer number for every existing borrower that lacks one.
-- Same scheme as app/core/customer_number.py: 6 random digits + Luhn digit,
-- retried until unique within the borrower's DCP.
DO $$
DECLARE
    r       RECORD;
    payload TEXT;
    cand    TEXT;
    total   INT;
    d       INT;
    i       INT;
    dbl     BOOLEAN;
BEGIN
    FOR r IN SELECT id, tenant_id FROM borrowers WHERE customer_number IS NULL ORDER BY id LOOP
        LOOP
            payload := (100000 + floor(random() * 900000))::INT::TEXT;
            total := 0;
            dbl := TRUE;
            FOR i IN REVERSE length(payload)..1 LOOP
                d := substr(payload, i, 1)::INT;
                IF dbl THEN
                    d := d * 2;
                    IF d > 9 THEN d := d - 9; END IF;
                END IF;
                total := total + d;
                dbl := NOT dbl;
            END LOOP;
            cand := payload || ((10 - total % 10) % 10)::TEXT;
            EXIT WHEN NOT EXISTS (
                SELECT 1 FROM borrowers
                WHERE tenant_id = r.tenant_id AND customer_number = cand);
        END LOOP;
        UPDATE borrowers SET customer_number = cand WHERE id = r.id;
    END LOOP;
END $$;
