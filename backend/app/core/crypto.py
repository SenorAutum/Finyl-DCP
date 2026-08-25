"""
PII-01 — application-level field encryption for sensitive PII at rest.

Provides a Fernet (AES-128-CBC + HMAC-SHA256) based encrypt/decrypt pair and a
SQLAlchemy ``EncryptedText`` column type. Values are stored as a self-describing
token prefixed with ``enc:v1:`` so decryption is *backward compatible*: any legacy
plaintext already in the column (no prefix) is returned unchanged on read, and
only newly written values are encrypted. This lets encryption roll out on a live
table with existing rows and no migration/backfill required for correctness.

Key management (PII-02)
-----------------------
Keys are combined into a ``MultiFernet``:

  * PRIMARY (encrypts new data)   — ``FIELD_ENCRYPTION_KEY`` when set: a dedicated
    urlsafe-b64 32-byte Fernet key, INDEPENDENT of ``JWT_SECRET``.
  * SECONDARY (decrypt-only)      — any ``PII_ENCRYPTION_KEY``, then the key
    *derived* from ``JWT_SECRET`` via SHA-256 (the historical default).

MultiFernet encrypts with the first key and decrypts against every key in order,
so ciphertext written under the old JWT-derived key (e.g. existing ``ocr_text``)
still decrypts, and rotating to a new ``FIELD_ENCRYPTION_KEY`` needs only a
re-encrypt (see seeds/backfill_pii_encryption.py) with zero downtime. When no
dedicated key is provisioned the JWT-derived key is used for both encrypt and
decrypt, so the module works out of the box.

Blind index
-----------
``pii_hash`` gives a deterministic HMAC-SHA256 of a value, keyed by a SEPARATE
index key derived (domain-separated) from the primary key material. It lets an
encrypted column keep exact-match lookup / uniqueness (store the hash in a
sibling ``*_hash`` column) without ever exposing the plaintext or the ability to
decrypt from the index.

Secrets and PII values are never logged.
"""
import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.config import settings

_PREFIX = "enc:v1:"
# Domain-separation label so the blind-index HMAC key can never coincide with the
# encryption key material even though both are derived from the same secret.
_INDEX_INFO = b"finyl-dcp/pii-blind-index/v1"


def _jwt_derived_key() -> bytes:
    """Stable 32-byte urlsafe-b64 Fernet key derived from JWT_SECRET (the historical
    default key). Retained as a decrypt-only secondary so legacy ciphertext reads."""
    digest = hashlib.sha256((settings.JWT_SECRET or "").encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _primary_key_material() -> bytes:
    """The raw bytes of the PRIMARY Fernet key (the one used to encrypt new data).

    Used both by MultiFernet and as the seed for the blind-index HMAC key so the
    index rotates together with the encryption key.
    """
    field_key = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip()
    if field_key:
        return field_key.encode("utf-8")
    pii_key = (getattr(settings, "PII_ENCRYPTION_KEY", "") or "").strip()
    if pii_key:
        return pii_key.encode("utf-8")
    return _jwt_derived_key()


def _multifernet() -> MultiFernet:
    """Build the MultiFernet keyring: primary first (encrypts), the rest decrypt-only.

    Order: FIELD_ENCRYPTION_KEY -> PII_ENCRYPTION_KEY -> JWT-derived. Duplicate
    keys are de-duplicated so the same key is never listed twice.
    """
    keys: list[bytes] = []

    def _add(raw: bytes | None):
        if raw and raw not in keys:
            keys.append(raw)

    field_key = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip()
    if field_key:
        _add(field_key.encode("utf-8"))
    pii_key = (getattr(settings, "PII_ENCRYPTION_KEY", "") or "").strip()
    if pii_key:
        _add(pii_key.encode("utf-8"))
    _add(_jwt_derived_key())
    return MultiFernet([Fernet(k) for k in keys])


def _index_key() -> bytes:
    """32-byte HMAC key for the blind index, derived (domain-separated) from the
    primary key material so it is distinct from the encryption key."""
    return hashlib.sha256(_primary_key_material() + b"|" + _INDEX_INFO).digest()


def pii_hash(value: str | None) -> str | None:
    """Deterministic blind index: HMAC-SHA256(index_key, value) as hex.

    None/empty pass through. The value is normalised (stripped) before hashing so
    that equality lookups are insensitive to surrounding whitespace. Not reversible
    — it exists only for exact-match lookup / uniqueness on an encrypted column.
    """
    if value is None or value == "":
        return value
    normalised = value.strip()
    return hmac.new(_index_key(), normalised.encode("utf-8"), hashlib.sha256).hexdigest()


def encrypt_pii(plaintext: str | None) -> str | None:
    """Encrypt a string, returning an ``enc:v1:`` token. None/empty pass through.
    Already-encrypted input (``enc:v1:`` prefix) is returned unchanged so the call
    is idempotent and safe on mixed/partially-migrated data."""
    if plaintext is None or plaintext == "":
        return plaintext
    if plaintext.startswith(_PREFIX):
        return plaintext  # already ciphertext — do not double-encrypt
    token = _multifernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
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
        return _multifernet().decrypt(token).decode("utf-8")
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
