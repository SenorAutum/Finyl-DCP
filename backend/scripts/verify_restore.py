#!/usr/bin/env python3
"""Check that a restored database is usable (WP9, review 7.12 and step 6).

Run by deploy/aws/restore_drill.sh as a one-off task against the COPY that was
restored from a backup, never against the live database. It reads the same
settings as the app (DATABASE_URL or DB_HOST/DB_PASSWORD..., DB_SCHEMA), then:

* confirms every migration file in this image is recorded as applied;
* counts the main records (DCPs, users, clients, loans, repayments, M-Pesa);
* shows the newest repayment / audit time, i.e. how recent the backup is.

Prints one line per check and "RESTORE CHECK PASSED" or "RESTORE CHECK FAILED".
Exit code 0 only when it passed. It changes nothing in the database.
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COUNTS = [
    ("DCPs", "tenants"),
    ("staff users", "users"),
    ("clients", "borrowers"),
    ("loans", "loans"),
    ("repayments", "repayments"),
    ("M-Pesa transactions", "payment_transactions"),
    ("audit entries", "audit_logs"),
]
NEWEST = [
    ("newest repayment", "repayments", "payment_date"),
    ("newest audit entry", "audit_logs", "created_at"),
]


def main() -> int:
    from app.core.config import settings
    import psycopg2

    schema = settings.DB_SCHEMA
    expected = sorted(os.path.basename(p) for p in glob.glob(str(ROOT / "migrations" / "*.sql")))
    failures: list[str] = []
    try:
        conn = psycopg2.connect(settings.DATABASE_URL, connect_timeout=15)
    except Exception as exc:
        print(f"connect: FAILED ({type(exc).__name__})")
        print("RESTORE CHECK FAILED")
        return 1
    conn.set_session(readonly=True, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')

            def exists(table: str) -> bool:
                cur.execute("SELECT to_regclass(%s)", (f'"{schema}".{table}',))
                return cur.fetchone()[0] is not None

            if not exists("schema_migrations"):
                failures.append("schema_migrations table missing")
                applied: set[str] = set()
            else:
                cur.execute("SELECT filename FROM schema_migrations")
                applied = {r[0] for r in cur.fetchall()}
            missing = [f for f in expected if f not in applied]
            print(f"migrations: {len(applied)} recorded, {len(expected)} in this version"
                  + (f", MISSING {', '.join(missing[:5])}" if missing else ""))
            if missing:
                failures.append(f"{len(missing)} migration(s) not applied")

            for label, table in COUNTS:
                if not exists(table):
                    print(f"{label}: table {table} not found")
                    failures.append(f"table {table} missing")
                    continue
                cur.execute(f'SELECT count(*) FROM "{table}"')
                print(f"{label}: {cur.fetchone()[0]}")

            if exists("tenants"):
                cur.execute('SELECT count(*) FROM "tenants"')
                if cur.fetchone()[0] == 0:
                    failures.append("no DCPs in the restored copy")

            for label, table, col in NEWEST:
                if exists(table):
                    cur.execute(f'SELECT max("{col}") FROM "{table}"')
                    print(f"{label}: {cur.fetchone()[0]}")
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        conn.close()

    if failures:
        for f in failures:
            print(f"problem: {f}")
        print("RESTORE CHECK FAILED")
        return 1
    print("RESTORE CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
