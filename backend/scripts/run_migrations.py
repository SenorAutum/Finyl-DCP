#!/usr/bin/env python3
"""
Finyl-DCP — database migration runner.

Applies every SQL file in ``backend/migrations/*.sql`` to the database pointed at
by ``DATABASE_URL``, in ascending numeric order, exactly once.

Why this exists
---------------
Historically the numbered migrations were applied by hand with ``psql``. That is
error-prone and impossible to automate for a new environment (e.g. bootstrapping
a fresh AWS RDS instance). This runner makes migration application:

  * repeatable  — safe to run any number of times (idempotent);
  * ordered     — files are sorted by their leading numeric prefix;
  * tracked     — a ``schema_migrations`` table records which files ran and when,
                  so already-applied files are skipped on subsequent runs;
  * transactional — each file runs inside its own transaction; on error the
                  whole file is rolled back, the failing filename is printed and
                  the process exits non-zero;
  * secret-safe — the DATABASE_URL (and its embedded password) is NEVER printed.

Configuration (environment, same as the app — see .env.example):
  DATABASE_URL   e.g. postgresql://USER:PASSWORD@HOST:5432/finyl_dcp?sslmode=require
  DB_SCHEMA      target schema (default "finyl_dcp"; use "public" for a dedicated DB)

Usage:
  cd backend
  ./venv/bin/python scripts/run_migrations.py            # apply pending migrations
  ./venv/bin/python scripts/run_migrations.py --dry-run  # list what WOULD apply

Exit codes: 0 = success (nothing to do counts as success), 1 = a migration failed
or configuration/connection error.
"""
from __future__ import annotations

import glob
import os
import re
import sys
from pathlib import Path

# --- Make the backend package importable no matter the CWD -------------------
# This script lives at backend/scripts/run_migrations.py; the backend package
# root (containing "app/") is its parent's parent.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MIGRATIONS_DIR = BACKEND_DIR / "migrations"

# Leading integer prefix, e.g. "016" in "016_webhook_durability.sql".
_NUM_RE = re.compile(r"^(\d+)")


def _numeric_key(path: str) -> tuple:
    """Sort key: (leading number, filename) so 002 < 010 < 016 numerically."""
    name = os.path.basename(path)
    m = _NUM_RE.match(name)
    return (int(m.group(1)) if m else 1_000_000, name)


def _redact(msg: str, url: str) -> str:
    """Best-effort scrub of a connection URL out of an error message."""
    if url and url in msg:
        msg = msg.replace(url, "<DATABASE_URL>")
    return msg


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]

    # Import settings lazily so we can emit a clean message if config is invalid
    # (e.g. a weak JWT_SECRET boot guard) rather than a raw traceback.
    try:
        from app.core.config import settings
    except Exception as exc:  # pragma: no cover - config/import failure
        print(f"ERROR: could not load application settings: {exc}", file=sys.stderr)
        return 1

    database_url = settings.DATABASE_URL
    schema = settings.DB_SCHEMA

    # psycopg2 is already a dependency of the app (see requirements.txt).
    try:
        import psycopg2
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: psycopg2 is not importable: {exc}", file=sys.stderr)
        return 1

    files = sorted(glob.glob(str(MIGRATIONS_DIR / "*.sql")), key=_numeric_key)
    if not files:
        print(f"No migration files found in {MIGRATIONS_DIR}", file=sys.stderr)
        return 1

    print(f"Migration runner: {len(files)} file(s) found in {MIGRATIONS_DIR}")
    print(f"Target schema: {schema}")

    try:
        conn = psycopg2.connect(database_url)
    except Exception as exc:
        print(f"ERROR: could not connect to the database: "
              f"{_redact(str(exc), database_url)}", file=sys.stderr)
        return 1

    applied_count = 0
    skipped_count = 0
    try:
        conn.autocommit = False

        # 1) Ensure the target schema exists and pin the search_path so the
        #    tracking table (and every migration) lands in the right schema.
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(f'SET search_path TO "{schema}", public')
            # 2) Ensure the tracking table exists.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename   text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            # 3) Load the set of already-applied filenames.
            cur.execute("SELECT filename FROM schema_migrations")
            already = {row[0] for row in cur.fetchall()}
        conn.commit()

        for path in files:
            name = os.path.basename(path)
            if name in already:
                skipped_count += 1
                print(f"  SKIP    {name} (already applied)")
                continue

            if dry_run:
                applied_count += 1
                print(f"  PENDING {name} (dry-run, not applied)")
                continue

            sql = Path(path).read_text(encoding="utf-8")
            try:
                with conn.cursor() as cur:
                    # Re-pin search_path per transaction (transaction poolers).
                    cur.execute(f'SET LOCAL search_path TO "{schema}", public')
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s) "
                        "ON CONFLICT (filename) DO NOTHING",
                        (name,),
                    )
                conn.commit()
                applied_count += 1
                print(f"  APPLIED {name}")
            except Exception as exc:
                conn.rollback()
                print(f"\nERROR: migration failed: {name}", file=sys.stderr)
                print(f"       {_redact(str(exc), database_url)}", file=sys.stderr)
                return 1
    finally:
        conn.close()

    verb = "would apply" if dry_run else "applied"
    print(f"\nDone: {verb} {applied_count}, skipped {skipped_count}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
