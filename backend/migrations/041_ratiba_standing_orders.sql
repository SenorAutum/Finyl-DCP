-- WP12: M-Pesa Ratiba standing orders go to Safaricom for real.
--
-- Safaricom's standing order needs how often and between which dates the
-- money is taken, and answers later (after the client confirms on their
-- phone) quoting a reference that has to be matched back to the request.
SET search_path TO finyl_dcp, public;

ALTER TABLE mpesa_ratiba_consents ADD COLUMN IF NOT EXISTS frequency   VARCHAR(2);
ALTER TABLE mpesa_ratiba_consents ADD COLUMN IF NOT EXISTS start_date  DATE;
ALTER TABLE mpesa_ratiba_consents ADD COLUMN IF NOT EXISTS end_date    DATE;
ALTER TABLE mpesa_ratiba_consents ADD COLUMN IF NOT EXISTS request_ref VARCHAR(80);
ALTER TABLE mpesa_ratiba_consents ADD COLUMN IF NOT EXISTS created_by  INTEGER REFERENCES users(id);
CREATE INDEX IF NOT EXISTS ix_ratiba_request_ref ON mpesa_ratiba_consents (request_ref);

-- Mandates created by the old simulated call never reached Safaricom.
UPDATE mpesa_ratiba_consents
   SET ratiba_status = 'simulated'
 WHERE ratiba_status = 'active' AND raw_response ->> 'mock' = 'true';
