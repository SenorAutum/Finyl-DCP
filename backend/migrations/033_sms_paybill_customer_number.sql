-- WP4: tell borrowers WHERE to pay. The disbursement and reminder SMS used to
-- say only "Pay via M-Pesa Paybill" with no number and no account reference.
-- They now include {{paybill}} (the DCP's own paybill) and {{customer_number}}
-- (the borrower's paybill account number from migration 030).
--
-- Only templates still holding the ORIGINAL default wording are updated; any
-- template a DCP has edited is left exactly as they wrote it.
UPDATE sms_templates
   SET body = 'Dear {{first_name}}, your loan {{account_number}} of KES {{amount}} has been disbursed to your M-Pesa. Repay by {{due_date}} via Paybill {{paybill}}, Account {{customer_number}}. {{dcp_name}}.',
       updated_at = now()
 WHERE event_key = 'loan_disbursed'
   AND body = 'Dear {{first_name}}, your loan {{account_number}} of KES {{amount}} has been disbursed to your M-Pesa. Repay by {{due_date}}. {{dcp_name}}.';

UPDATE sms_templates
   SET body = 'Hi {{first_name}}, a friendly reminder: loan {{account_number}} balance KES {{balance}} is due in {{days_left}} day(s) on {{due_date}}. Pay via M-Pesa Paybill {{paybill}}, Account {{customer_number}}. {{dcp_name}}.',
       updated_at = now()
 WHERE event_key = 'repayment_reminder'
   AND body = 'Hi {{first_name}}, a friendly reminder: loan {{account_number}} balance KES {{balance}} is due in {{days_left}} day(s) on {{due_date}}. Pay via M-Pesa Paybill. {{dcp_name}}.';
