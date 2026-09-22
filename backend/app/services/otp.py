"""OTP generation / verification (Phase 2).

6-digit codes, bcrypt-hashed at rest, 5-minute expiry, max 3 attempts. Delivery
reuses the existing SMS gateway (and can also emit e-mail via the notification
layer). Never store or log the plaintext code beyond the delivery call.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy.orm import Session

from app.models import OtpToken, User

OTP_TTL_MINUTES = 5
OTP_MAX_ATTEMPTS = 3
OTP_LENGTH = 6


def _hash(code: str) -> str:
    return bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()


def _check(code: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(code.encode(), hashed.encode())
    except Exception:
        return False


def generate(db: Session, user: User, *, purpose: str = "login", ip: str | None = None) -> str:
    """Create + persist a new OTP, invalidating any prior unconsumed ones. Returns the plaintext code."""
    # Invalidate outstanding codes for the same purpose.
    (db.query(OtpToken)
       .filter(OtpToken.user_id == user.id, OtpToken.purpose == purpose,
               OtpToken.consumed == False)  # noqa: E712
       .update({OtpToken.consumed: True}))
    code = "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))
    tok = OtpToken(
        user_id=user.id, otp_hash=_hash(code), purpose=purpose,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES),
        ip=ip,
    )
    db.add(tok)
    db.commit()
    return code


def deliver(db: Session, user: User, code: str, *, channels=None) -> dict:
    """Best-effort delivery via SMS (and log). Returns a delivery summary."""
    channels = channels or ["sms"]
    sent = {}
    msg = f"Your Finyl verification code is {code}. It expires in {OTP_TTL_MINUTES} minutes."
    if "sms" in channels and getattr(user, "phone", None):
        try:
            from app.services import sms
            res = sms.send_sms(db, user.tenant_id, user.phone, msg, trigger_type="otp")
            sent["sms"] = bool(res)
        except Exception:
            sent["sms"] = False
    # Email channel is delegated to the notification layer if configured.
    return {"channels": sent}


def verify(db: Session, user: User, code: str, *, purpose: str = "login") -> tuple[bool, str]:
    """Verify a code. Returns (ok, reason)."""
    tok = (db.query(OtpToken)
           .filter(OtpToken.user_id == user.id, OtpToken.purpose == purpose,
                   OtpToken.consumed == False)  # noqa: E712
           .order_by(OtpToken.created_at.desc())
           .first())
    if not tok:
        return False, "no_active_otp"
    exp = tok.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        tok.consumed = True
        db.commit()
        return False, "expired"
    if tok.attempts >= OTP_MAX_ATTEMPTS:
        tok.consumed = True
        db.commit()
        return False, "too_many_attempts"
    tok.attempts += 1
    if not _check(code, tok.otp_hash):
        db.commit()
        return False, "invalid"
    tok.consumed = True
    db.commit()
    return True, "ok"


def cleanup_expired(db: Session) -> int:
    """Mark expired unconsumed OTPs consumed. Returns count. (scheduler job)"""
    n = (db.query(OtpToken)
         .filter(OtpToken.consumed == False,  # noqa: E712
                 OtpToken.expires_at < datetime.now(timezone.utc))
         .update({OtpToken.consumed: True}))
    db.commit()
    return n
