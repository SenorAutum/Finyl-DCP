-- WP10: guarantor M-Pesa confirmation, and the payoff amount in reminder SMS.
--
-- Before WP10 the guarantor "validation" called a name lookup that does not
-- exist, so no guarantor could ever be validated. A guarantor is now validated
-- when a staff member confirms the name M-Pesa shows for the guarantor's
-- number, with evidence. These columns record who, for which number, and how.
SET search_path TO finyl_dcp, public;

ALTER TABLE guarantors ADD COLUMN IF NOT EXISTS mpesa_validation_method   VARCHAR(20);
ALTER TABLE guarantors ADD COLUMN IF NOT EXISTS mpesa_validation_phone    VARCHAR(20);
ALTER TABLE guarantors ADD COLUMN IF NOT EXISTS mpesa_validated_by        INTEGER REFERENCES users(id);
ALTER TABLE guarantors ADD COLUMN IF NOT EXISTS mpesa_validation_evidence TEXT;

-- Any guarantor marked validated without that evidence goes back to pending
-- (none should exist, because the old check never succeeded).
UPDATE guarantors
   SET kyc_status = 'pending', mpesa_validated = FALSE
 WHERE mpesa_validation_method IS NULL
   AND (kyc_status = 'validated' OR mpesa_validated);

-- Old document rows pointed at paths typed by the caller, not at uploaded
-- files. They are kept for the record; nothing reads those paths any more.

-- WP10: reminder SMS quote the amount that clears the loan today (with the
-- early-repayment rebate, CBK LN 191/2026 reg. 56(4)). Only a reminder still
-- holding the previous default wording is updated; edited ones are left alone.
UPDATE sms_templates
   SET body = 'Hi {{first_name}}, a friendly reminder: loan {{account_number}} balance KES {{balance}} is due in {{days_left}} day(s) on {{due_date}}. To clear it today pay KES {{payoff_amount}}. Pay via M-Pesa Paybill {{paybill}}, Account {{customer_number}}. {{dcp_name}}.',
       updated_at = now()
 WHERE event_key = 'repayment_reminder'
   AND body = 'Hi {{first_name}}, a friendly reminder: loan {{account_number}} balance KES {{balance}} is due in {{days_left}} day(s) on {{due_date}}. Pay via M-Pesa Paybill {{paybill}}, Account {{customer_number}}. {{dcp_name}}.';
