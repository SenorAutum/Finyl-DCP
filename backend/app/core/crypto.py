"""
PII-01 — application-level field encryption for sensitive PII at rest.

Provides a Fernet (AES-128-CBC + HMAC-SHA256) based encrypt/decrypt pair and a
SQLAlchemy ``EncryptedText`` column type. Values are stored as a self-describing
token prefixed with ``enc:v1:`` so decryption is *backward compatible*: any legacy
plaintext already in the column (no prefix) is returned unchanged on read, and
only newly written values are encrypted. This lets encryption roll out on a live
table with existing rows and no migration/backfill required for correctness.

Key management
--------------
The key is taken from the ``PII_ENCRYPTION_KEY`` env var when set (a urlsafe
base64 32-byte Fernet key). When it is not set the key is *derived* from the
already-strong ``JWT_SECRET`` via SHA-256 so encryption works out of the box on
this deployment without provisioning an extra secret. For key rotation set an
explicit ``PII_ENCRYPTION_KEY`` and re-encrypt.

Secrets are never logged.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.config import settings

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    explicit = (getattr(settings, "PII_ENCRYPTION_KEY", "") or "").strip()
    if explicit:
        key = explicit.encode("utf-8")
    else:
        # Derive a stable 32-byte key from JWT_SECRET (which AUTH-01 guarantees is
        # strong and >=32 chars). urlsafe-b64 encode to the Fernet key format.
        digest = hashlib.sha256((settings.JWT_SECRET or "").encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_pii(plaintext: str | None) -> str | None:
    """Encrypt a string, returning an ``enc:v1:`` token. None/empty pass through."""
    if plaintext is None or plaintext == "":
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt_pii(stored: str | None) -> str | None:
    """Decrypt an ``enc:v1:`` token. Legacy plaintext (no prefix) is returned as-is
    for backward compatibility; an undecryptable token is returned unchanged."""
    if stored is None or stored == "":
        return stored
    if not stored.startswith(_PREFIX):
        return stored  # legacy plaintext row — leave untouched
    token = stored[len(_PREFIX):].encode("ascii")
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except (InvalidToken, ValueError):
        # Wrong key / corrupt value — never crash a read path over PII.
        return stored


class EncryptedText(TypeDecorator):
    """Transparent at-rest encryption for a TEXT column. Encrypts on write,
    decrypts on read. Ciphertext is longer than plaintext, so only use on TEXT
    (unbounded) columns — never on a length-limited VARCHAR."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_pii(value)

    def process_result_value(self, value, dialect):
        return decrypt_pii(value)
