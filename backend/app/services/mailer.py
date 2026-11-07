"""Minimal outgoing e-mail over SMTP (WP6, F21).

Used for staff sign-in codes. Configure SMTP_HOST / SMTP_PORT / SMTP_USERNAME /
SMTP_PASSWORD / SMTP_FROM (on AWS: Amazon SES SMTP credentials). Never raises:
returns True only when the relay accepted the message.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM)


def send_email(to: str, subject: str, body: str) -> bool:
    if not (is_configured() and to):
        return False
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
            if settings.SMTP_STARTTLS:
                smtp.starttls(context=ssl.create_default_context())
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception as exc:                       # never break the caller
        logger.error("email_send_failed to=%s: %s", to.split("@")[-1], exc)
        return False
