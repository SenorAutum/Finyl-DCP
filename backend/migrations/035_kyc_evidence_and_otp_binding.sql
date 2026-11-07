-- WP6 (F13-F22): KYC evidence, two-stage edit approval, staff OTP channels and
-- consent codes bound to what they were sent for. Additive only.

-- F21: staff need a phone number so the login code can actually be sent.
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(20);

-- F13: a primary-tier (identity) edit now needs two different approvers. The
-- first approval is recorded here; the request stays pending until the final one.
ALTER TABLE client_edit_requests ADD COLUMN IF NOT EXISTS first_approved_by INTEGER REFERENCES users(id);
ALTER TABLE client_edit_requests ADD COLUMN IF NOT EXISTS first_approved_at TIMESTAMPTZ;

-- F20: who captured the profile, so the same person cannot also approve it.
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS created_by_user_id INTEGER REFERENCES users(id);

-- F15: how the M-Pesa name was confirmed, for which number, by whom, and on
-- what evidence. "provider" = a real name-lookup service; "manual" = confirmed
-- by a second staff member who is not the onboarding officer.
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS mpesa_validation_method VARCHAR(20);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS mpesa_validation_phone VARCHAR(20);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS mpesa_validated_by INTEGER REFERENCES users(id);
ALTER TABLE borrowers ADD COLUMN IF NOT EXISTS mpesa_validation_evidence TEXT;

-- The previous check echoed the officer's own typing back as the "registered
-- name", so no stored M-Pesa validation is evidence of anything. Clear them;
-- each client is re-confirmed through the new path. Nothing else is touched.
UPDATE borrowers
   SET mpesa_validated = FALSE,
       mpesa_validation_name = NULL
 WHERE mpesa_validated = TRUE
   AND mpesa_validation_method IS NULL;

-- The same applies to alternate-phone validation (F16).
UPDATE borrowers SET alt_phone_validated = FALSE WHERE alt_phone_validated = TRUE;

-- F19: a DCP can make the external eKYC (ID registry) check mandatory.
ALTER TABLE tenant_validation_prefs ADD COLUMN IF NOT EXISTS ekyc_mandatory BOOLEAN NOT NULL DEFAULT FALSE;

-- Per-DCP choices for how strict the KYC process is. Defaults are the strict
-- settings; each DCP can relax them in Validation Preferences.
--   mpesa_confirmation_mode   second_person (a different staff member with
--                             approval rights) | single_person (any staff
--                             member who can edit clients)
--   identity_edit_approvals   approvals needed to change phone / ID / names /
--                             date of birth on a locked client: 2 or 1
--   allow_review_override     "needs manual review" can be approved with a
--                             written note (true) or must be fixed first (false)
--   crb_max_age_days          how long a CRB report stays current
--   min_credit_score          lowest acceptable CRB score (NULL = none)
ALTER TABLE tenant_validation_prefs ADD COLUMN IF NOT EXISTS mpesa_confirmation_mode VARCHAR(20) NOT NULL DEFAULT 'second_person';
ALTER TABLE tenant_validation_prefs ADD COLUMN IF NOT EXISTS identity_edit_approvals INTEGER NOT NULL DEFAULT 2;
ALTER TABLE tenant_validation_prefs ADD COLUMN IF NOT EXISTS allow_review_override BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE tenant_validation_prefs ADD COLUMN IF NOT EXISTS crb_max_age_days INTEGER NOT NULL DEFAULT 90;
ALTER TABLE tenant_validation_prefs ADD COLUMN IF NOT EXISTS min_credit_score INTEGER;

-- An edit request keeps the number of approvals that applied when it was raised,
-- so changing the setting never changes a request already in progress.
ALTER TABLE client_edit_requests ADD COLUMN IF NOT EXISTS approvals_required INTEGER;

-- F22: a one-time code is bound to the tenant, the number it was sent to and a
-- hash of what it was for (client or lead, consent text and version, purpose).
ALTER TABLE otp_tokens ADD COLUMN IF NOT EXISTS tenant_id INTEGER;
ALTER TABLE otp_tokens ADD COLUMN IF NOT EXISTS recipient VARCHAR(20);
ALTER TABLE otp_tokens ADD COLUMN IF NOT EXISTS context_hash VARCHAR(64);
ALTER TABLE otp_tokens ADD COLUMN IF NOT EXISTS context JSONB;

-- F22: the consent log records the version of the wording the client agreed to.
ALTER TABLE client_consent_logs ADD COLUMN IF NOT EXISTS consent_version VARCHAR(20);
