-- WP4: loan account numbers must be unique within a DCP — borrowers type them
-- at the paybill, so a repeated number could credit the wrong loan.
--
-- Safe on existing data: the unique index is created only when no duplicates
-- exist. If some do, the migration does NOT fail (so a deploy is not blocked)
-- and does NOT renumber anything (borrowers may already be paying with those
-- numbers); it raises a WARNING listing them. Fix those loans by hand, then
-- run this statement yourself:
--   CREATE UNIQUE INDEX uq_loans_tenant_account_number ON loans (tenant_id, account_number);
DO $$
DECLARE
    dupes TEXT;
BEGIN
    SELECT string_agg(format('tenant %s: %s (x%s)', tenant_id, account_number, n), '; ')
      INTO dupes
      FROM (SELECT tenant_id, account_number, count(*) AS n
              FROM loans GROUP BY tenant_id, account_number HAVING count(*) > 1) d;
    IF dupes IS NULL THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_loans_tenant_account_number
            ON loans (tenant_id, account_number);
    ELSE
        RAISE WARNING 'Duplicate loan account numbers found; unique index NOT created: %', dupes;
    END IF;
END $$;
