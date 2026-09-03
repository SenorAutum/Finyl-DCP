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
#   4. Uploads the dump off-site to S3 and prunes S3 objects older than
#      S3_RETENTION_DAYS (default 30). The S3 target is resolved host-agnostically:
#        - On Abacus: auto-discovered from VM metadata (IMDSv2), preserving the
#          historical <storage-path>backups/finyl-dcp/ layout.
#        - Anywhere else: set BACKUP_S3_BUCKET (and optional BACKUP_S3_PREFIX) in
#          the environment; credentials come from the default AWS chain
#          (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, an instance/task role, or
#          ~/.aws/credentials). Explicit env overrides always win over metadata.
#        - If NEITHER metadata NOR BACKUP_S3_BUCKET is available, off-site upload
#          is SKIPPED with a clear WARNING and the local dump + local retention
#          still run (the backup does NOT hard-fail).
#   5. Logs success/failure. Exits non-zero only on a real backup failure
#      (dump/verify); a missing off-site target is a warning, not a failure.
#
# Runs non-interactively (designed for a systemd timer). No secrets are
# ever echoed: the DB password lives inside DATABASE_URL and is passed to
# pg_dump only via the environment; AWS credentials are never printed.
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
# pg_dump/pg_restore are always required. The aws CLI is required ONLY when an
# off-site S3 target is resolved (checked after target resolution below).
command -v pg_dump    >/dev/null 2>&1 || fail "pg_dump not found on PATH"
command -v pg_restore >/dev/null 2>&1 || fail "pg_restore not found on PATH"

# --- Resolve the off-site S3 target (host-agnostic) -------------------------
# Precedence:
#   1. Explicit env overrides (BACKUP_S3_BUCKET / BACKUP_S3_PREFIX) — any host.
#   2. Abacus VM metadata (IMDSv2) — best-effort auto-detect, never aborts.
#   3. Neither -> S3 disabled; local-only backup with a WARNING.
# S3_BUCKET / S3_KEY_PREFIX end up set (S3 enabled) or empty (S3 disabled).
S3_BUCKET=""
S3_KEY_PREFIX=""   # full key prefix (no bucket, no leading slash, no trailing slash)

# Best-effort Abacus metadata read. Guarded so a non-Abacus host (where
# 169.254.169.254 is unreachable) can never abort the run under `set -e`.
discover_storage() {
    local token
    token="$(curl -s --max-time 3 -X PUT 'http://169.254.169.254/latest/api/token' \
        -H 'X-abacus-vm-metadata-token-ttl-seconds: 300' 2>/dev/null)" || return 1
    [ -n "${token}" ] || return 1
    curl -s --max-time 3 -H "X-abacus-vm-metadata-token: ${token}" \
        http://169.254.169.254/latest/user-data 2>/dev/null || return 1
}

if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
    # --- Mode 1: explicit env configuration (portable / any host) -----------
    S3_BUCKET="${BACKUP_S3_BUCKET}"
    _prefix="${BACKUP_S3_PREFIX:-${S3_SUBPREFIX}}"
    _prefix="${_prefix#/}"; _prefix="${_prefix%/}"   # trim leading/trailing slash
    S3_KEY_PREFIX="${_prefix}"
    log "Off-site target from env: s3://${S3_BUCKET}/${S3_KEY_PREFIX}"
else
    # --- Mode 2: try Abacus VM metadata (best-effort) -----------------------
    STORAGE_JSON="$(discover_storage || true)"
    if [ -n "${STORAGE_JSON}" ]; then
        _meta_bucket="$(echo "${STORAGE_JSON}" | python3 -c 'import sys,json;
d=json.load(sys.stdin); print(d.get("storage",{}).get("bucket_name",""))' 2>/dev/null || true)"
        _meta_path="$(echo "${STORAGE_JSON}" | python3 -c 'import sys,json;
d=json.load(sys.stdin); print(d.get("storage",{}).get("path",""))' 2>/dev/null || true)"
        if [ -n "${_meta_bucket}" ] && [ -n "${_meta_path}" ]; then
            _meta_path="${_meta_path#/}"
            [ "${_meta_path: -1}" = "/" ] || _meta_path="${_meta_path}/"
            S3_BUCKET="${_meta_bucket}"
            S3_KEY_PREFIX="${_meta_path}${S3_SUBPREFIX}"
            log "Off-site target from Abacus metadata: s3://${S3_BUCKET}/${S3_KEY_PREFIX}"
        fi
    fi
fi

# If an S3 target was resolved, the aws CLI is now mandatory.
S3_ENABLED=0
if [ -n "${S3_BUCKET}" ]; then
    if command -v aws >/dev/null 2>&1; then
        S3_ENABLED=1
        S3_PREFIX="s3://${S3_BUCKET}/${S3_KEY_PREFIX}"
    else
        log "WARNING: an S3 target is configured but the aws CLI is not on PATH — off-site upload SKIPPED (local backup only)."
    fi
else
    log "WARNING: no off-site S3 target (set BACKUP_S3_BUCKET, or run on Abacus) — off-site upload SKIPPED (local backup only)."
fi

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

# --- Upload to S3 (only when an off-site target is enabled) -----------------
if [ "${S3_ENABLED}" = "1" ]; then
    log "Uploading to ${S3_PREFIX}/${DUMP_NAME} ..."
    aws s3 cp "${DUMP_PATH}" "${S3_PREFIX}/${DUMP_NAME}" --only-show-errors
    log "Upload complete."
else
    log "Off-site upload skipped (no S3 target) — dump retained locally at ${DUMP_PATH}"
fi

# --- Prune local copies older than LOCAL_RETENTION_DAYS ---------------------
log "Pruning local dumps older than ${LOCAL_RETENTION_DAYS} day(s) ..."
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'finyl_dcp_*.dump' \
    -mtime "+${LOCAL_RETENTION_DAYS}" -print -delete | while read -r f; do
        log "  removed local ${f}"
    done || true

# --- Prune S3 objects older than S3_RETENTION_DAYS (only when enabled) ------
if [ "${S3_ENABLED}" = "1" ]; then
    log "Pruning S3 dumps older than ${S3_RETENTION_DAYS} day(s) ..."
    CUTOFF_EPOCH="$(date -u -d "-${S3_RETENTION_DAYS} days" '+%s')"
    # List objects under the prefix; each line: "<LastModified> <Key>"
    aws s3api list-objects-v2 \
        --bucket "${S3_BUCKET}" \
        --prefix "${S3_KEY_PREFIX}/" \
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
fi

log "=== Backup run finished OK: ${DUMP_NAME} ==="
exit 0
