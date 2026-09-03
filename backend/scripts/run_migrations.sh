#!/usr/bin/env bash
#
# Finyl-DCP — thin wrapper around scripts/run_migrations.py.
#
# Applies all pending backend/migrations/*.sql to the database in DATABASE_URL.
# Uses the project virtualenv python if present, otherwise the system python3.
# Loads backend/.env if present (so DATABASE_URL / DB_SCHEMA are picked up),
# but any already-exported environment variables win.
#
# Usage:
#   cd backend && ./scripts/run_migrations.sh            # apply pending
#   cd backend && ./scripts/run_migrations.sh --dry-run  # preview only
#   DATABASE_URL=postgresql://... ./scripts/run_migrations.sh   # explicit target
#
set -euo pipefail

# Resolve the backend directory (parent of this scripts/ dir) and cd into it so
# relative paths and the app package import resolve consistently.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${BACKEND_DIR}"

if [ -x "venv/bin/python" ]; then
    PY="venv/bin/python"
else
    PY="python3"
fi

exec "${PY}" scripts/run_migrations.py "$@"
