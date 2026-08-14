"""
Centralized SMS helper — LIVE via the Uwazii Mobile bulk-SMS gateway.

Dispatch goes to the Uwazii REST API:

    POST {UWAZII_BASE_URL}   (default https://restapi.uwaziimobile.com/v1/send)
    Authorization: Bearer <UWAZII_ACCESS_TOKEN>
    body: {"from": <UWAZII_SENDER_ID>, "messages": [{"to": "2547XXXXXXX", "text": "..."}]}

Credentials are injected into the environment from the secret store (never
hardcoded/committed). When the access token is present, SMS is dispatched live
and the real provider message id/status is recorded in sms_logs. When it is
absent the service degrades gracefully: the message is logged with status
"not_configured" and the triggering action is NEVER interrupted.
"""
import json
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import SmsLog

PROVIDER_NAME = "uwazii"


def is_configured() -> bool:
    """True when a real Uwazii access token is present."""
    return bool((settings.UWAZII_ACCESS_TOKEN or "").strip())


def integration_status() -> str:
    """LIVE when configured, otherwise NOT CONFIGURED (used by DCP Setup)."""
    return "LIVE" if is_configured() else "NOT CONFIGURED"


def normalise_phone(phone: str) -> str:
    """Normalise a Kenyan mobile number to 2547XXXXXXXX / 2541XXXXXXXX."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    elif digits.startswith("7") or digits.startswith("1"):
        digits = "254" + digits
    while digits.startswith("254254"):
        digits = digits[3:]
    return digits


def _dispatch_to_provider(phone: str, message: str) -> dict:
    """Send one SMS through Uwazii. Returns a normalized result dict:
        {status, provider, provider_ref, provider_response, error}
    Never raises — failures are captured so the caller's action continues.
    """
    to = normalise_phone(phone)
    if not to or len(to) != 12 or not (to.startswith("2547") or to.startswith("2541")):
        return {"status": "failed", "provider": PROVIDER_NAME, "provider_ref": None,
                "provider_response": None, "error": f"Invalid KE mobile number: {phone!r}"}

    if not is_configured():
        # Credential-gated: do not fake a send.
        return {"status": "not_configured", "provider": PROVIDER_NAME, "provider_ref": None,
                "provider_response": None,
                "error": "Uwazii access token not configured — SMS not dispatched."}

    body = {
        "from": settings.UWAZII_SENDER_ID or "",
        "messages": [{"to": to, "text": message}],
    }
    try:
        resp = httpx.post(
            settings.UWAZII_BASE_URL,
            headers={"Authorization": f"Bearer {settings.UWAZII_ACCESS_TOKEN}",
                     "Content-Type": "application/json", "Accept": "application/json"},
            json=body,
            timeout=30,
        )
    except Exception as exc:  # network/timeout — log & keep going
        return {"status": "failed", "provider": PROVIDER_NAME, "provider_ref": None,
                "provider_response": None, "error": f"Uwazii request error: {exc}"}

    raw = (resp.text or "")[:2000]
    try:
        data = resp.json()
    except Exception:
        data = {}

    if resp.status_code // 100 != 2:
        return {"status": "failed", "provider": PROVIDER_NAME, "provider_ref": None,
                "provider_response": raw,
                "error": f"Uwazii HTTP {resp.status_code}: {raw[:300]}"}

    # Extract a provider message id/status from a few common response shapes.
    ref = _extract_ref(data)
    return {"status": "sent", "provider": PROVIDER_NAME, "provider_ref": ref,
            "provider_response": raw, "error": None}


def _extract_ref(data) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("messageId", "message_id", "id", "MessageId", "smsId"):
        if data.get(key):
            return str(data[key])
    # Nested list shapes: {"messages": [{"messageId": ...}]} / {"data": [...]}
    for container in ("messages", "data", "results", "SMSMessageData"):
        val = data.get(container)
        if isinstance(val, dict):
            r = _extract_ref(val)
            if r:
                return r
        if isinstance(val, list) and val and isinstance(val[0], dict):
            r = _extract_ref(val[0])
            if r:
                return r
    return None


def send_sms(db: Session, tenant_id: int, phone: str, message: str,
             trigger_type: str = "manual") -> SmsLog:
    """Dispatch an SMS via Uwazii and record it in sms_logs.

    Always returns a persisted SmsLog (never raises), so trigger actions such as
    loan approval or ticket resolution are never rolled back by an SMS failure.
    """
    result = _dispatch_to_provider(phone, message)
    log = SmsLog(
        tenant_id=tenant_id,
        recipient_phone=normalise_phone(phone) or phone,
        message=message,
        trigger_type=trigger_type,
        status=result["status"],
        provider=result.get("provider"),
        provider_ref=result.get("provider_ref"),
        provider_response=result.get("provider_response"),
        error=result.get("error"),
        sent_at=datetime.utcnow(),
    )
    db.add(log)
    db.flush()
    return log


# --- Trigger message templates ------------------------------------------------

def sms_loan_approval(db, tenant_id, borrower, loan):
    return send_sms(
        db, tenant_id, borrower.phone,
        f"Dear {borrower.first_name}, your loan {loan.account_number} of KES "
        f"{float(loan.principal):,.0f} has been APPROVED and is being disbursed to "
        f"your M-Pesa. Repay by {loan.due_date}. Finyl-DCP.",
        "loan_approval",
    )


def sms_repayment_reminder(db, tenant_id, borrower, loan, days_left: int):
    return send_sms(
        db, tenant_id, borrower.phone,
        f"Hi {borrower.first_name}, a friendly reminder: loan {loan.account_number} "
        f"balance KES {float(loan.outstanding_balance):,.0f} is due in {days_left} day(s) "
        f"on {loan.due_date}. Pay via M-Pesa Paybill. Finyl-DCP.",
        "repayment_reminder",
    )


def sms_overdue_alert(db, tenant_id, borrower, loan):
    return send_sms(
        db, tenant_id, borrower.phone,
        f"Dear {borrower.first_name}, loan {loan.account_number} is OVERDUE. "
        f"Outstanding KES {float(loan.outstanding_balance):,.0f}. Penalties may apply. "
        f"Kindly settle to protect your credit score. Finyl-DCP.",
        "overdue_alert",
    )


def sms_payment_receipt(db, tenant_id, borrower, loan, amount, ref):
    return send_sms(
        db, tenant_id, borrower.phone,
        f"Payment received: KES {amount:,.0f} for loan {loan.account_number} "
        f"(ref {ref}). New balance KES {float(loan.outstanding_balance):,.0f}. Thank you! Finyl-DCP.",
        "manual",
    )


def sms_ticket_resolution(db, tenant_id, phone, ticket_id):
    return send_sms(
        db, tenant_id, phone,
        f"Your complaint {ticket_id} has been RESOLVED. Thank you for your patience. "
        f"Reply HELP for further assistance. Finyl-DCP.",
        "ticket_resolution",
    )
