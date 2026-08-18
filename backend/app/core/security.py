"""JWT + password hashing helpers."""
import secrets
from datetime import datetime, timedelta

import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, role: str, tenant_id: int | None,
                        token_version: int = 0) -> str:
    """Mint a signed JWT.

    AUTH-04: every token carries a unique ``jti`` (for audit / future denylist)
    and a ``tv`` (token_version) claim. Bumping ``User.token_version`` on logout
    or password change invalidates every previously issued token for that user.
    """
    payload = {
        "sub": str(user_id),
        "role": role,
        "tenant_id": tenant_id,
        "tv": token_version,
        "jti": secrets.token_hex(16),
        "exp": datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
