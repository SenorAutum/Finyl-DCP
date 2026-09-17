"""Third-party API-key generation, verification and rate limiting (Phase 2).

Keys are 32 random bytes, url-safe base64. Only a bcrypt hash + a 12-char prefix
are persisted; the full key is returned exactly once at creation. The prefix is a
unique index used to locate the candidate row for constant-work verification.
"""
from __future__ import annotations

import secrets
import time as _time
from collections import defaultdict, deque
from datetime import datetime, timezone

import bcrypt
from sqlalchemy.orm import Session

from app.models import ThirdPartyApiClient

PREFIX_LEN = 12

# In-process sliding-window rate limiter (per api_key_prefix). Adequate for a
# single-worker deployment; swap for Redis when horizontally scaled.
_HITS: dict[str, deque] = defaultdict(deque)


def generate_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, bcrypt_hash)."""
    raw = secrets.token_urlsafe(32)
    full = f"fdcp_{raw}"
    prefix = full[:PREFIX_LEN]
    h = bcrypt.hashpw(full.encode(), bcrypt.gensalt()).decode()
    return full, prefix, h


def create_client(db: Session, *, tenant_id: int, client_name: str, scopes: list[str],
                  rate_limit_per_min: int = 60, created_by: int | None = None):
    full, prefix, h = generate_key()
    row = ThirdPartyApiClient(
        tenant_id=tenant_id, client_name=client_name,
        api_key_hash=h, api_key_prefix=prefix, scopes=scopes or [],
        rate_limit_per_min=rate_limit_per_min, created_by=created_by,
    )
    db.add(row)
    db.commit()
    return row, full


def authenticate(db: Session, api_key: str | None) -> ThirdPartyApiClient | None:
    """Resolve + verify an inbound API key. Returns the active client or None."""
    if not api_key or len(api_key) < PREFIX_LEN:
        return None
    prefix = api_key[:PREFIX_LEN]
    row = (db.query(ThirdPartyApiClient)
           .filter(ThirdPartyApiClient.api_key_prefix == prefix,
                   ThirdPartyApiClient.active == True).first())  # noqa: E712
    if not row:
        return None
    try:
        if not bcrypt.checkpw(api_key.encode(), row.api_key_hash.encode()):
            return None
    except Exception:
        return None
    row.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return row


def check_rate_limit(client: ThirdPartyApiClient) -> bool:
    """Sliding 60-second window. Returns True when the request is allowed."""
    limit = int(client.rate_limit_per_min or 60)
    now = _time.time()
    dq = _HITS[client.api_key_prefix]
    while dq and dq[0] < now - 60:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    return True


def has_scope(client: ThirdPartyApiClient, scope: str) -> bool:
    return scope in (client.scopes or [])
