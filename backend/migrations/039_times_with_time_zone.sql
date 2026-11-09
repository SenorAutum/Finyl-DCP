-- WP10: store every time with its time zone.
--
-- Most time columns were "timestamp without time zone" (a bare clock reading).
-- The application always wrote UTC into them, so each one is converted to
-- "timestamptz" reading the stored value as UTC. After this, a time means the
-- same moment whatever time zone the database server or the viewer uses.
--
-- Columns are found automatically, so tables added by earlier migrations
-- without a model are covered too. Running it again changes nothing.
SET search_path TO finyl_dcp, public;
SET TIME ZONE 'UTC';

DO $$
DECLARE
    col record;
BEGIN
    FOR col IN
        SELECT c.table_name, c.column_name
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON t.table_schema = c.table_schema AND t.table_name = c.table_name
         WHERE c.table_schema = 'finyl_dcp'
           AND t.table_type = 'BASE TABLE'
           AND c.data_type = 'timestamp without time zone'
         ORDER BY c.table_name, c.column_name
    LOOP
        EXECUTE format(
            'ALTER TABLE finyl_dcp.%I ALTER COLUMN %I TYPE timestamptz USING %I AT TIME ZONE ''UTC''',
            col.table_name, col.column_name, col.column_name);
    END LOOP;
END
$$;
