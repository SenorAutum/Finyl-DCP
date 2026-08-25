#!/usr/bin/env bash
#
# backup_db.sh — Automated PostgreSQL backup for Finyl-DCP.
#
# What it does:
#   1. Reads DATABASE_URL from backend/.env (never printed/logged).
#   2. Produces a custom-format compressed dump (pg_dump -Fc) named
#      finyl_dcp_<UTC timestamp>.dump.
#   3. Keeps a local copy under backend/storage/backups/ (gitignored) and
#      prunes local copies older than LOCAL_RETENTION_DAYS (default 7).
#   4. Uploads the dump to the Abacus S3 bucket under
#      <storage-path>backups/finyl-dcp/ and prunes S3 objects older than
#      S3_RETENTION_DAYS (default 30).
#   5. Logs success/failure and exits non-zero on any failure.
#
# Runs non-interactively (designed for a systemd timer). No secrets are
# ever echoed: the DB password lives inside DATABASE_URL and is passed to
# pg_dump only via the environment.
#
set -euo pipefail

# --- Paths ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/backend/.env"
BACKUP_DIR="${PROJECT_ROOT}/backend/storage/backups"
LOG_FILE="${BACKUP_DIR}/backup.log"

LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-7}"
S3_RETENTION_DAYS="${S3_RETENTION_DAYS:-30}"
S3_SUBPREFIX="backups/finyl-dcp"

mkdir -p "${BACKUP_DIR}"

# --- Logging ----------------------------------------------------------------
log() {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [backup_db] $*" | tee -a "${LOG_FILE}"
}
fail() {
    log "ERROR: $*"
    exit 1
}
trap 'fail "aborted at line ${LINENO}"' ERR

log "=== Backup run starting ==="

# --- Load DATABASE_URL without echoing it -----------------------------------
[ -f "${ENV_FILE}" ] || fail "env file not found: ${ENV_FILE}"
DATABASE_URL="$(grep -E '^DATABASE_URL=' "${ENV_FILE}" | head -n1 | cut -d= -f2-)"
# Strip optional surrounding quotes
DATABASE_URL="${DATABASE_URL%\"}"; DATABASE_URL="${DATABASE_URL#\"}"
DATABASE_URL="${DATABASE_URL%\'}"; DATABASE_URL="${DATABASE_URL#\'}"
[ -n "${DATABASE_URL}" ] || fail "DATABASE_URL not set in ${ENV_FILE}"
export DATABASE_URL

# --- Ensure required tools --------------------------------------------------
command -v pg_dump    >/dev/null 2>&1 || fail "pg_dump not found on PATH"
command -v pg_restore >/dev/null 2>&1 || fail "pg_restore not found on PATH"
command -v aws        >/dev/null 2>&1 || fail "aws CLI not found on PATH"

# --- Discover S3 bucket + path from VM metadata (IMDSv2) --------------------
discover_storage() {
    local token
    token="$(curl -s -X PUT 'http://169.254.169.254/latest/api/token' \
        -H 'X-abacus-vm-metadata-token-ttl-seconds: 300')" || return 1
    curl -s -H "X-abacus-vm-metadata-token: ${token}" \
        http://169.254.169.254/latest/user-data
}

STORAGE_JSON="$(discover_storage)" || fail "could not read VM metadata"
S3_BUCKET="$(echo "${STORAGE_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["storage"]["bucket_name"])')"
S3_BASEPATH="$(echo "${STORAGE_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["storage"]["path"])')"
[ -n "${S3_BUCKET}" ]   || fail "could not resolve S3 bucket name"
[ -n "${S3_BASEPATH}" ] || fail "could not resolve S3 base path"
# Normalise: ensure trailing slash on base path, no leading slash
S3_BASEPATH="${S3_BASEPATH#/}"
[ "${S3_BASEPATH: -1}" = "/" ] || S3_BASEPATH="${S3_BASEPATH}/"
S3_PREFIX="s3://${S3_BUCKET}/${S3_BASEPATH}${S3_SUBPREFIX}"

# --- Create the dump --------------------------------------------------------
TS="$(date -u '+%Y%m%dT%H%M%SZ')"
DUMP_NAME="finyl_dcp_${TS}.dump"
DUMP_PATH="${BACKUP_DIR}/${DUMP_NAME}"

log "Creating dump ${DUMP_NAME} ..."
pg_dump -Fc --no-owner --no-privileges "${DATABASE_URL}" -f "${DUMP_PATH}"

# Verify the dump is readable/valid before trusting it
if ! pg_restore --list "${DUMP_PATH}" >/dev/null 2>&1; then
    fail "dump verification failed (pg_restore --list): ${DUMP_PATH}"
fi
DUMP_SIZE="$(du -h "${DUMP_PATH}" | cut -f1)"
log "Dump created and verified (${DUMP_SIZE}): ${DUMP_PATH}"

# --- Upload to S3 -----------------------------------------------------------
log "Uploading to ${S3_PREFIX}/${DUMP_NAME} ..."
aws s3 cp "${DUMP_PATH}" "${S3_PREFIX}/${DUMP_NAME}" --only-show-errors
log "Upload complete."

# --- Prune local copies older than LOCAL_RETENTION_DAYS ---------------------
log "Pruning local dumps older than ${LOCAL_RETENTION_DAYS} day(s) ..."
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'finyl_dcp_*.dump' \
    -mtime "+${LOCAL_RETENTION_DAYS}" -print -delete | while read -r f; do
        log "  removed local ${f}"
    done || true

# --- Prune S3 objects older than S3_RETENTION_DAYS --------------------------
log "Pruning S3 dumps older than ${S3_RETENTION_DAYS} day(s) ..."
CUTOFF_EPOCH="$(date -u -d "-${S3_RETENTION_DAYS} days" '+%s')"
# List objects under the prefix; each line: "<LastModified> <Key>"
aws s3api list-objects-v2 \
    --bucket "${S3_BUCKET}" \
    --prefix "${S3_BASEPATH}${S3_SUBPREFIX}/" \
    --query 'Contents[].[LastModified,Key]' \
    --output text 2>/dev/null | while read -r lastmod key; do
        [ -n "${key}" ] || continue
        case "${key}" in *.dump) ;; *) continue ;; esac
        obj_epoch="$(date -u -d "${lastmod}" '+%s' 2>/dev/null || echo 0)"
        if [ "${obj_epoch}" -gt 0 ] && [ "${obj_epoch}" -lt "${CUTOFF_EPOCH}" ]; then
            aws s3 rm "s3://${S3_BUCKET}/${key}" --only-show-errors && \
                log "  removed s3://${S3_BUCKET}/${key}"
        fi
    done || true

log "=== Backup run finished OK: ${DUMP_NAME} ==="
exit 0
