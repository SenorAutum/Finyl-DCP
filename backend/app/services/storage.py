"""
Client document storage — local filesystem implementation.

Layout:  <STORAGE_DIR>/clients/<tenant_id>/<client_id>/<uuid>_<sanitised name>

This is the only module that knows where bytes live. To move to S3/GCS/Azure,
re-implement save_bytes/read_bytes/delete_file with the same signatures — the
routers stay unchanged.
"""
import os
import re
import uuid

from app.core.config import settings

# STORAGE_DIR may be relative — resolve against the backend package root so the
# path is stable no matter which directory uvicorn was launched from.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = (settings.STORAGE_DIR if os.path.isabs(settings.STORAGE_DIR)
        else os.path.join(_BACKEND_ROOT, settings.STORAGE_DIR))

MAX_BYTES = settings.MAX_UPLOAD_MB * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitise(name: str) -> str:
    """Strip directory components and unsafe characters from an uploaded filename."""
    base = os.path.basename(name or "file")
    cleaned = _SAFE.sub("_", base).strip("._") or "file"
    return cleaned[:120]


def client_dir(tenant_id: int, client_id: int) -> str:
    path = os.path.join(ROOT, "clients", str(tenant_id), str(client_id))
    os.makedirs(path, exist_ok=True)
    return path


def save_bytes(tenant_id: int, client_id: int, original_name: str, data: bytes) -> tuple[str, str]:
    """Persist bytes; returns (stored_file_name, absolute_path)."""
    stored = f"{uuid.uuid4().hex[:12]}_{sanitise(original_name)}"
    path = os.path.join(client_dir(tenant_id, client_id), stored)
    with open(path, "wb") as fh:
        fh.write(data)
    return stored, path


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def delete_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass  # already gone — the DB row is still removed by the caller
