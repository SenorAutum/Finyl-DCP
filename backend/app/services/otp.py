"""OTP generation / verification (Phase 2).

6-digit codes, bcrypt-hashed at rest, 5-minute expiry, max 3 attempts. Delivery
uses the SMS gateway (staff phone) and, when SMTP is configured, e-mail. A code
can be bound to its recipient and context so it only verifies for what it was
sent for (WP6). Never store or log the plaintext code beyond the delivery call.
"""
from __future__ import annotations

import hashlib
import json
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


def context_hash(context: dict | None) -> str | None:
    """Stable hash of what a code was issued for (WP6, F22)."""
    if not context:
        return None
    blob = json.dumps(context, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def generate(db: Session, user: User, *, purpose: str = "login", ip: str | None = None,
             recipient: str | None = None, context: dict | None = None,
             tenant_id: int | None = None) -> str:
    """Create + persist a new OTP, invalidating any prior unconsumed ones for the
    same purpose. Returns the plaintext code.

    WP6 (F22): ``recipient`` and ``context`` bind the code to what it was sent
    for; :func:`verify` then only accepts it for the same recipient and context."""
    (db.query(OtpToken)
       .filter(OtpToken.user_id == user.id, OtpToken.purpose == purpose,
               OtpToken.consumed == False)  # noqa: E712
       .update({OtpToken.consumed: True}))
    code = "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))
    tok = OtpToken(
        user_id=user.id, otp_hash=_hash(code), purpose=purpose,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES),
        ip=ip, tenant_id=tenant_id if tenant_id is not None else user.tenant_id,
        recipient=_norm(recipient) if recipient else None,
        context_hash=context_hash(context), context=context,
    )
    db.add(tok)
    db.commit()
    return code


def _norm(phone: str | None) -> str:
    from app.services.sms import normalise_phone
    return normalise_phone(phone or "")


def deliver(db: Session, user: User, code: str, *, channels=None) -> dict:
    """Send a sign-in code to the user's own contact channels.

    WP6 (F21): SMS goes to ``user.phone`` (set in User & Access Management);
    e-mail goes to ``user.email`` when SMTP is configured. Returns
    ``{"channels": {name: bool}, "delivered": bool}``. The code itself is never
    written to the SMS log."""
    channels = channels or ["sms"]
    sent: dict = {}
    msg = f"Your Finyl verification code is {code}. It expires in {OTP_TTL_MINUTES} minutes."
    if "sms" in channels:
        if getattr(user, "phone", None):
            try:
                from app.services import sms
                log = sms.send_sms(db, user.tenant_id, user.phone, msg, trigger_type="otp",
                                   log_message="Your Finyl verification code is ******. "
                                               f"It expires in {OTP_TTL_MINUTES} minutes.")
                sent["sms"] = bool(log is not None and getattr(log, "status", None) == "sent")
                db.commit()
            except Exception:
                db.rollback()
                sent["sms"] = False
        else:
            sent["sms"] = False
    if "email" in channels:
        from app.services import mailer
        sent["email"] = mailer.send_email(
            user.email, "Your Finyl sign-in code",
            f"{msg}\n\nIf you did not try to sign in, tell your administrator.")
    return {"channels": sent, "delivered": any(sent.values())}


def verify(db: Session, user: User, code: str, *, purpose: str = "login",
           recipient: str | None = None, context: dict | None = None,
           require_bound: bool = False) -> tuple[bool, str]:
    """Verify a code. Returns (ok, reason).

    A code issued with a recipient/context (WP6, F22) only verifies for the same
    recipient and context; a mismatch counts as a failed attempt. With
    ``require_bound`` an unbound code (issued without recipient/context) is
    refused outright."""
    tok = (db.query(OtpToken)
           .filter(OtpToken.user_id == user.id, OtpToken.purpose == purpose,
                   OtpToken.consumed == False)  # noqa: E712
           .order_by(OtpToken.created_at.desc(), OtpToken.id.desc())
           .with_for_update()
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
    bound = bool(tok.recipient or tok.context_hash)
    if require_bound and not bound:
        tok.consumed = True
        db.commit()
        return False, "unbound_code"
    if bound and (_norm(recipient) != (tok.recipient or _norm(recipient))
                  or context_hash(context) != tok.context_hash):
        db.commit()
        return False, "context_mismatch"
    if not _check(code, tok.otp_hash):
        db.commit()
        return False, "invalid"
    tok.consumed = True
    db.commit()
    return True, "ok"


def active_token(db: Session, user: User, purpose: str) -> OtpToken | None:
    return (db.query(OtpToken)
            .filter(OtpToken.user_id == user.id, OtpToken.purpose == purpose,
                    OtpToken.consumed == False)  # noqa: E712
            .order_by(OtpToken.created_at.desc(), OtpToken.id.desc()).first())


def last_consumed(db: Session, user: User, purpose: str) -> OtpToken | None:
    return (db.query(OtpToken)
            .filter(OtpToken.user_id == user.id, OtpToken.purpose == purpose,
                    OtpToken.consumed == True)  # noqa: E712
            .order_by(OtpToken.created_at.desc(), OtpToken.id.desc()).first())


def cleanup_expired(db: Session) -> int:
    """Mark expired unconsumed OTPs consumed. Returns count. (scheduler job)"""
    n = (db.query(OtpToken)
         .filter(OtpToken.consumed == False,  # noqa: E712
                 OtpToken.expires_at < datetime.now(timezone.utc))
         .update({OtpToken.consumed: True}))
    db.commit()
    return n
