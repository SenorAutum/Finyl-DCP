-- Early full repayment (CBK Non-Deposit Taking Credit Providers Regulations,
-- 2026 — Legal Notice 191, regulation 56(4)): a borrower who repays a loan in
-- full before maturity does not pay interest for the remaining period.
--
-- Additive only. Records, per loan, the unearned interest written off at an
-- early settlement and when it happened, so statements, the GL export and CBK
-- reporting can explain why the balance went to zero.

ALTER TABLE loans ADD COLUMN IF NOT EXISTS interest_rebate NUMERIC(12, 2) DEFAULT 0;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS early_settled_at TIMESTAMP;
