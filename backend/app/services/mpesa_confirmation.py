"""Confirming who owns an M-Pesa number (WP6, F15).

Daraja offers ordinary partners no registered-name lookup, so the platform can
only check a number's format. Ownership is confirmed by a staff member - by
default a SECOND one: someone with ``clients.approve`` who did not capture the
client and does not own the client's portfolio (a DCP can relax this to any
staff member who edits clients, in Validation Preferences). They record the name M-Pesa showed (for example on
the confirmation screen of a KES 1 send-money test, or on the client's own
phone) and the evidence for it. The name must match the client's names.

When a real name-lookup provider is contracted, it records
``mpesa_validation_method = "provider"`` through the same fields.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

from app.services.alt_phone_validation import names_match
from app.services.sms import normalise_phone

MIN_EVIDENCE_CHARS = 10


def check_confirmer(user, *, created_by_user_id: int | None,
                    officer_staff_id: int | None) -> None:
    """The person confirming must not be the person who captured the record,
    nor the officer who owns it."""
    if created_by_user_id and user.id == created_by_user_id:
        raise HTTPException(403, "You captured this record, so someone else must "
                                 "confirm the M-Pesa name.")
    if officer_staff_id and getattr(user, "staff_id", None) == officer_staff_id:
        raise HTTPException(403, "You are this client's relationship officer, so someone "
                                 "else must confirm the M-Pesa name.")


def check_name_and_evidence(*, expected_name: str, registered_name: str,
                            evidence: str) -> tuple[str, str]:
    reg = " ".join((registered_name or "").split()).upper()
    ev = (evidence or "").strip()
    if not reg:
        raise HTTPException(422, "Enter the name M-Pesa showed for this number.")
    if len(ev) < MIN_EVIDENCE_CHARS:
        raise HTTPException(422, "Describe how the name was seen (for example: "
                                 "'KES 1 send-money test on 28 Oct, M-Pesa showed ...').")
    if not names_match(expected_name, reg):
        raise HTTPException(
            422, f"The M-Pesa name '{reg}' does not match the client's names "
                 f"('{expected_name}'). Do not confirm; log a KYC escalation instead.")
    return reg, ev


def apply_to_client(client, *, user, registered_name: str, evidence: str) -> None:
    client.mpesa_validated = True
    client.mpesa_validation_method = "manual"
    client.mpesa_validation_name = registered_name
    client.mpesa_validation_phone = normalise_phone(client.phone)
    client.mpesa_validated_by = user.id
    client.mpesa_validation_evidence = evidence[:1000]
    client.mpesa_validated_at = datetime.utcnow()
