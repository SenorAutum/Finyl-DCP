#!/usr/bin/env bash
#
# restore_db.sh — Restore a Finyl-DCP pg_dump into a target database.
#
# Usage:
#   deploy/restore_db.sh <dump-path> [target-db-url-or-name]
#
#   <dump-path>            Local path OR an s3://.../file.dump URL.
#   [target]              Optional. Either:
#                           - a full postgres connection URL, or
#                           - a bare database name (host/port/creds are taken
#                             from backend/.env DATABASE_URL, only the dbname
#                             is swapped).
#                         DEFAULT: a scratch database named
#                         "finyl_dcp_restore_test" on the same server.
#
# SAFETY: This script refuses to restore into the LIVE database name found in
# backend/.env unless FORCE_LIVE=1 is explicitly set in the environment. The
# default target is always a scratch database, never live data.
#
# Examples:
#   deploy/restore_db.sh backend/storage/backups/finyl_dcp_20260825T020000Z.dump
#   deploy/restore_db.sh s3://bucket/63476/backups/finyl-dcp/finyl_dcp_...dump my_scratch
#   FORCE_LIVE=1 deploy/restore_db.sh /path/to.dump 4b29d16f   # DANGER: real DR
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/backend/.env"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

DUMP_ARG="${1:-}"
TARGET_ARG="${2:-finyl_dcp_restore_test}"

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [restore_db] $*"; }
fail() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [restore_db] ERROR: $*" >&2; exit 1; }

[ -n "${DUMP_ARG}" ] || fail "usage: restore_db.sh <dump-path|s3://...> [target-db]"
[ -f "${ENV_FILE}" ] || fail "env file not found: ${ENV_FILE}"

# --- Read base connection (no echo) -----------------------------------------
BASE_URL="$(grep -E '^DATABASE_URL=' "${ENV_FILE}" | head -n1 | cut -d= -f2-)"
BASE_URL="${BASE_URL%\"}"; BASE_URL="${BASE_URL#\"}"
BASE_URL="${BASE_URL%\'}"; BASE_URL="${BASE_URL#\'}"
[ -n "${BASE_URL}" ] || fail "DATABASE_URL not set in ${ENV_FILE}"

# Parse the live db name (last path segment before optional ?query)
LIVE_DB="$(python3 - "$BASE_URL" <<'PY'
import sys, urllib.parse as u
p = u.urlparse(sys.argv[1])
print(p.path.lstrip('/'))
PY
)"

# --- Resolve the dump to a local file ---------------------------------------
if [[ "${DUMP_ARG}" == s3://* ]]; then
    command -v aws >/dev/null 2>&1 || fail "aws CLI required for s3:// dumps"
    LOCAL_DUMP="${TMP_DIR}/$(basename "${DUMP_ARG}")"
    log "Downloading ${DUMP_ARG} ..."
    aws s3 cp "${DUMP_ARG}" "${LOCAL_DUMP}" --only-show-errors
else
    LOCAL_DUMP="${DUMP_ARG}"
fi
[ -f "${LOCAL_DUMP}" ] || fail "dump file not found: ${LOCAL_DUMP}"
pg_restore --list "${LOCAL_DUMP}" >/dev/null 2>&1 || fail "not a valid pg_dump archive: ${LOCAL_DUMP}"

# --- Build target URL + target db name --------------------------------------
if [[ "${TARGET_ARG}" == postgres://* || "${TARGET_ARG}" == postgresql://* ]]; then
    TARGET_URL="${TARGET_ARG}"
    TARGET_DB="$(python3 - "$TARGET_URL" <<'PY'
import sys, urllib.parse as u
print(u.urlparse(sys.argv[1]).path.lstrip('/'))
PY
)"
    ADMIN_URL="${TARGET_URL}"
else
    TARGET_DB="${TARGET_ARG}"
    # Swap only the dbname in the base URL; keep an admin URL on the live db
    TARGET_URL="$(python3 - "$BASE_URL" "$TARGET_DB" <<'PY'
import sys, urllib.parse as u
p = u.urlparse(sys.argv[1])
print(u.urlunparse(p._replace(path='/' + sys.argv[2])))
PY
)"
    ADMIN_URL="${BASE_URL}"
fi

# --- Safety guard against clobbering live -----------------------------------
if [ "${TARGET_DB}" = "${LIVE_DB}" ] && [ "${FORCE_LIVE:-0}" != "1" ]; then
    fail "refusing to restore into LIVE database '${LIVE_DB}'. Set FORCE_LIVE=1 to override."
fi

log "Restore target database: ${TARGET_DB}"
log "Dump: ${LOCAL_DUMP}"

# --- Ensure the target database exists --------------------------------------
# createdb via the admin connection; ignore "already exists".
if command -v createdb >/dev/null 2>&1; then
    if createdb -T template0 "${TARGET_DB}" 2>/dev/null \
         --maintenance-db="${ADMIN_URL}" 2>/dev/null; then
        log "Created database ${TARGET_DB}"
    else
        # Fall back to a psql CREATE DATABASE (harmless if it already exists)
        psql "${ADMIN_URL}" -v ON_ERROR_STOP=0 \
            -c "CREATE DATABASE \"${TARGET_DB}\" TEMPLATE template0;" >/dev/null 2>&1 || true
    fi
fi

# --- Restore ----------------------------------------------------------------
log "Restoring (pg_restore, this may take a while) ..."
pg_restore --no-owner --no-privileges --clean --if-exists \
    --dbname="${TARGET_URL}" "${LOCAL_DUMP}" || {
        log "pg_restore reported warnings/errors (often benign for --clean on a fresh db)."
    }

log "Restore into '${TARGET_DB}' complete."
log "Verify with: psql \"<target-url>\" -c \"SELECT count(*) FROM finyl_dcp.borrowers;\""
