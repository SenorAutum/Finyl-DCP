#!/bin/sh
# Finyl-DCP backend startup launcher.
#
# SAFE BY DEFAULT — designed for Render free tier, which spins the service down
# after idle and restarts it on the next request. The default path is IDEMPOTENT
# and NEVER destroys data:
#
#   (no env var)          -> idempotent seed: seeds ONLY if the DB is empty,
#                            no-ops if data already exists. Safe on every boot,
#                            so a fresh DB is auto-seeded and password changes,
#                            new records, etc. are preserved across restarts.
#
#   RESEED_ON_START=true  -> DESTRUCTIVE: wipes ALL data and reseeds demo data
#                            (python -m app.seeds.seed --force). Use ONLY for an
#                            intentional reset. WARNING: because Render restarts
#                            the service after every idle period, leaving this set
#                            wipes the database on EVERY restart. Set it, redeploy
#                            once, confirm, then REMOVE it immediately.
#
# For a controlled demo password on a fresh seed, set SEED_DEFAULT_PASSWORD
# (e.g. FINYL@2026) BEFORE the first seed runs; otherwise random per-user
# passwords are generated and written to a gitignored file you cannot read on
# Render. Seeded users have force_password_reset=True (change on first login).
set -e

if [ "${RESEED_ON_START}" = "true" ]; then
  echo "[startup] !!! RESEED_ON_START=true — DESTRUCTIVE wipe & reseed !!!"
  echo "[startup] !!! Remove this env var after this deploy or data is wiped on every restart !!!"
  python -m app.seeds.seed --force
  echo "[startup] Reseed complete."
else
  # Idempotent: the seed self-aborts (exit 0) if tenants already exist, so this
  # is safe to run unconditionally on every boot. `|| true` guards against a
  # transient failure (e.g. DB not ready yet) killing the whole container.
  echo "[startup] Ensuring database is seeded (idempotent, non-destructive)..."
  python -m app.seeds.seed || echo "[startup] Seed step skipped/failed (continuing to start API)."
fi

echo "[startup] Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
