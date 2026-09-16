#!/bin/sh
# Finyl-DCP backend startup launcher.
#
# Env-driven, free-tier friendly (no shell access needed on the host):
#   RESEED_ON_START=true  -> wipe & reseed demo data (python -m app.seeds.seed --force)
#   SEED_ON_START=true    -> first-time seed only (python -m app.seeds.seed)
#   (neither set)         -> just start the API server
#
# For a controlled demo password, also set SEED_DEFAULT_PASSWORD (e.g. FINYL@2026)
# BEFORE the seed runs, otherwise random per-user passwords are generated.
#
# After a successful (re)seed, remove RESEED_ON_START / SEED_ON_START from the
# environment so the next restart does not reseed again.
set -e

if [ "${RESEED_ON_START}" = "true" ]; then
  echo "[startup] RESEED_ON_START=true — running seed with --force..."
  python -m app.seeds.seed --force
  echo "[startup] Reseed complete."
elif [ "${SEED_ON_START}" = "true" ]; then
  echo "[startup] SEED_ON_START=true — running first-time seed..."
  python -m app.seeds.seed
  echo "[startup] Seed complete."
fi

echo "[startup] Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
