"""
Centralized SMS helper — LIVE via the Uwazii Mobile bulk-SMS gateway.

Uwazii uses a TWO-STEP token exchange (not a static Bearer token):

    STEP 1  POST {UWAZII_AUTH_URL}   {"username","password"}
            -> {"status":true,"data":{"authorization_code","expires_at"}}
    STEP 2  POST {UWAZII_TOKEN_URL}  {"authorization_code"}
            -> {"status":true,"data":{"access_token","expires_at"}}
    STEP 3  POST {UWAZII_BASE_URL}   header X-Access-Token: <access_token>
            body: JSON ARRAY of message objects (fasms shape)
            -> {"status":true,"data":{"2547...":[{"id_state":<id>}]}}

A static UWAZII_ACCESS_TOKEN may be supplied to bypass the exchange. The access
token is cached in-process and refreshed just before expiry (or on an
"Invalid Access token" rejection). Credentials are injected from the secret
store (never hardcoded/committed). When neither username/password nor a static
token is present the service degrades gracefully: the message is logged with
status "not_configured" and the triggering action is NEVER interrupted.
"""
import time
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import SmsLog

PROVIDER_NAME = "uwazii"

# Module-level in-memory access-token cache.
_token_cache: dict = {"token": None, "expires_at": 0}
_REFRESH_SKEW = 120  # refresh when within this many seconds of expiry


def is_configured() -> bool:
    """True when Uwazii can authenticate: username+password OR a static token."""
    if (settings.UWAZII_ACCESS_TOKEN or "").strip():
        return True
    return bool((settings.UWAZII_USERNAME or "").strip()
                and (settings.UWAZII_PASSWORD or "").strip())


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


def _get_access_token(force_refresh: bool = False) -> str | None:
    """Return a usable Uwazii access token, running the two-step exchange when
    needed. Uses a static override if configured. Caches the token with its
    expiry. Returns None on failure (never raises)."""
    static = (settings.UWAZII_ACCESS_TOKEN or "").strip()
    if static:
        return static

    now = int(time.time())
    if (not force_refresh and _token_cache.get("token")
            and now < (_token_cache.get("expires_at", 0) - _REFRESH_SKEW)):
        return _token_cache["token"]

    username = (settings.UWAZII_USERNAME or "").strip()
    password = (settings.UWAZII_PASSWORD or "").strip()
    if not (username and password):
        return None

    try:
        # STEP 1 — authorization_code
        r1 = httpx.post(
            settings.UWAZII_AUTH_URL,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={"username": username, "password": password},
            timeout=30,
        )
        d1 = r1.json() if r1.content else {}
        if not (isinstance(d1, dict) and d1.get("status")):
            return None
        auth_code = (d1.get("data") or {}).get("authorization_code")
        if not auth_code:
            return None

        # STEP 2 — access_token
        r2 = httpx.post(
            settings.UWAZII_TOKEN_URL,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={"authorization_code": auth_code},
            timeout=30,
        )
        d2 = r2.json() if r2.content else {}
        if not (isinstance(d2, dict) and d2.get("status")):
            return None
        data2 = d2.get("data") or {}
        token = data2.get("access_token")
        if not token:
            return None
        exp = data2.get("expires_at")
        try:
            exp = int(exp)
        except (TypeError, ValueError):
            exp = now + 3600
        _token_cache["token"] = token
        _token_cache["expires_at"] = exp
        return token
    except Exception:
        return None


def _send_request(token: str, payload: list) -> httpx.Response:
    return httpx.post(
        settings.UWAZII_BASE_URL,
        headers={"X-Access-Token": token,
                 "Content-Type": "application/json", "Accept": "application/json"},
        json=payload,
        timeout=30,
    )


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
                "error": "Uwazii credentials not configured — SMS not dispatched."}

    token = _get_access_token()
    if not token:
        return {"status": "failed", "provider": PROVIDER_NAME, "provider_ref": None,
                "provider_response": None,
                "error": "Unable to obtain Uwazii access token (auth exchange failed)."}

    now = datetime.now()
    payload = [{
        "number": to,
        "senderID": settings.UWAZII_SENDER_ID or "",
        "text": message,
        "type": "sms",
        "beginDate": now.strftime("%Y-%m-%d"),
        "beginTime": now.strftime("%H:%M"),
        "lifetime": 86400,
        "delivery": False,
    }]

    def _do(tok: str):
        try:
            resp = _send_request(tok, payload)
        except Exception as exc:  # network/timeout
            return None, f"Uwazii request error: {exc}", None
        raw = (resp.text or "")[:2000]
        try:
            data = resp.json()
        except Exception:
            data = {}
        return resp, raw, data

    resp, raw, data = _do(token)
    if resp is None:
        return {"status": "failed", "provider": PROVIDER_NAME, "provider_ref": None,
                "provider_response": None, "error": raw}

    # Uwazii returns status:false with errors on failure (may be HTTP 200 or 400).
    status_ok = isinstance(data, dict) and data.get("status") is True
    errors = (data.get("errors") if isinstance(data, dict) else None) or ""

    # Invalid token -> refresh once and retry.
    if (not status_ok) and "invalid access token" in str(errors).lower():
        _token_cache["token"] = None
        new_token = _get_access_token(force_refresh=True)
        if new_token:
            resp, raw, data = _do(new_token)
            if resp is not None:
                status_ok = isinstance(data, dict) and data.get("status") is True
                errors = (data.get("errors") if isinstance(data, dict) else None) or ""

    if status_ok:
        ref = _extract_ref(data)
        return {"status": "sent", "provider": PROVIDER_NAME, "provider_ref": ref,
                "provider_response": raw, "error": None}

    if resp is not None and resp.status_code // 100 != 2 and not errors:
        errors = f"Uwazii HTTP {resp.status_code}: {raw[:300]}"
    return {"status": "failed", "provider": PROVIDER_NAME, "provider_ref": None,
            "provider_response": raw, "error": str(errors) or "Uwazii send failed."}


def _extract_ref(data) -> str | None:
    if not isinstance(data, dict):
        return None
    # Uwazii shape: {"data": {"2547...": [{"id_state": <id>}]}}
    payload = data.get("data")
    if isinstance(payload, dict):
        for val in payload.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                ids = val[0].get("id_state")
                if ids is not None:
                    return str(ids)
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
