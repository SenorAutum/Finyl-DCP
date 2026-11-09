"""Outgoing e-mail (WP6 F21; WP9 adds Amazon SES).

Used for staff sign-in codes and scheduled report e-mails. Two ways to send:

* ``MAIL_BACKEND=smtp`` (default): any SMTP relay, set with SMTP_HOST /
  SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD.
* ``MAIL_BACKEND=ses``: Amazon SES through the server's own AWS permissions (the
  ECS task role), so there is no SMTP user or password to create or rotate.

The sender is MAIL_FROM (or SMTP_FROM). Never raises: returns True only when the
relay or SES accepted the message.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def _backend() -> str:
    return (getattr(settings, "MAIL_BACKEND", "") or "smtp").strip().lower()


def _sender() -> str:
    return (getattr(settings, "MAIL_FROM", "") or settings.SMTP_FROM or "").strip()


def is_configured() -> bool:
    if not _sender():
        return False
    if _backend() == "ses":
        return True
    return bool(settings.SMTP_HOST)


def _ses_client():
    import boto3                                  # only needed for SES
    return boto3.client("sesv2")


def _recipients(to: str) -> list[str]:
    return [a.strip() for a in (to or "").split(",") if a.strip()]


def send_email(to: str, subject: str, body: str,
               attachments: list[tuple[str, bytes, str]] | None = None) -> bool:
    """``to`` may hold several addresses separated by commas. ``attachments`` is
    a list of (file name, content, MIME type such as "text/csv")."""
    recipients = _recipients(to)
    if not (is_configured() and recipients):
        return False
    msg = EmailMessage()
    msg["From"] = _sender()
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    for name, content, mime in attachments or []:
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=name)
    try:
        if _backend() == "ses":
            _ses_client().send_email(
                FromEmailAddress=_sender(),
                Destination={"ToAddresses": recipients},
                Content={"Raw": {"Data": msg.as_bytes()}},
            )
            return True
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
            if settings.SMTP_STARTTLS:
                smtp.starttls(context=ssl.create_default_context())
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception as exc:                       # never break the caller
        domains = ",".join(sorted({r.split("@")[-1] for r in recipients}))
        logger.error("email_send_failed backend=%s to_domains=%s: %s", _backend(), domains, exc)
        return False
