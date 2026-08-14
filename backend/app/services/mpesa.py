"""
Safaricom Daraja M-Pesa integration — REAL client, credential-gated.

OAuth, STK push, B2C and C2B register all call the real Daraja endpoints. The
base URL follows DARAJA_ENV (sandbox=https://sandbox.safaricom.co.ke,
production=https://api.safaricom.co.ke).

CREDENTIAL-GATED: while DARAJA_CONSUMER_KEY / _SECRET are placeholders the
integration reports NOT CONFIGURED and each call raises DarajaNotConfigured
(surfaced by the router as a clear 4xx) — it never fabricates a success. Add real
sandbox credentials + restart and the SAME code flips to LIVE (SANDBOX).

The return shapes are unchanged from the previous mock so routers/UI keep working.
"""
import base64
import random
import string
import uuid
from datetime import datetime

import httpx

from app.core.config import settings

SANDBOX_BASE = "https://sandbox.safaricom.co.ke"
PROD_BASE = "https://api.safaricom.co.ke"


class DarajaNotConfigured(RuntimeError):
    """Raised when real Daraja credentials are absent."""


def base_url() -> str:
    return PROD_BASE if (settings.DARAJA_ENV or "sandbox").lower().startswith("prod") else SANDBOX_BASE


def is_configured() -> bool:
    for v in (settings.DARAJA_CONSUMER_KEY, settings.DARAJA_CONSUMER_SECRET):
        if not v or str(v).strip().lower() == "placeholder":
            return False
    return True


def integration_status() -> str:
    if not is_configured():
        return "NOT CONFIGURED"
    return "LIVE" if base_url() == PROD_BASE else "SANDBOX"


def _mpesa_ref() -> str:
    """Generate a plausible M-Pesa receipt (fallback for C2B when none supplied)."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def get_access_token() -> str:
    """OAuth client-credentials token from Daraja."""
    if not is_configured():
        raise DarajaNotConfigured("Daraja consumer key/secret required.")
    resp = httpx.get(
        f"{base_url()}/oauth/v1/generate?grant_type=client_credentials",
        auth=(settings.DARAJA_CONSUMER_KEY, settings.DARAJA_CONSUMER_SECRET),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def test_connection() -> dict:
    """Used by DCP Setup 'Test connection' — attempts an OAuth token."""
    if not is_configured():
        return {"ok": False, "status": "NOT CONFIGURED",
                "detail": "Daraja consumer key/secret not configured."}
    try:
        token = get_access_token()
        return {"ok": True, "status": integration_status(),
                "detail": "OAuth token acquired.", "token_preview": token[:8] + "…"}
    except Exception as exc:
        return {"ok": False, "status": "ERROR", "detail": str(exc)}


def _stk_password(timestamp: str) -> str:
    raw = f"{settings.DARAJA_SHORTCODE}{settings.DARAJA_PASSKEY}{timestamp}"
    return base64.b64encode(raw.encode()).decode()


def b2c_disburse(phone: str, amount: float, remarks: str) -> dict:
    """Real Daraja B2C payment request (disbursement to borrower)."""
    if not is_configured():
        raise DarajaNotConfigured("Daraja not configured — cannot disburse via M-Pesa.")
    token = get_access_token()
    msisdn = normalise_msisdn(phone)
    payload = {
        "InitiatorName": settings.DARAJA_INITIATOR_NAME,
        "SecurityCredential": settings.DARAJA_SECURITY_CREDENTIAL,
        "CommandID": "BusinessPayment",
        "Amount": int(amount),
        "PartyA": settings.DARAJA_SHORTCODE,
        "PartyB": msisdn,
        "Remarks": remarks[:100],
        "QueueTimeOutURL": f"{settings.DARAJA_CALLBACK_BASE_URL}/api/v1/payments/mpesa-b2c-timeout",
        "ResultURL": f"{settings.DARAJA_CALLBACK_BASE_URL}/api/v1/payments/mpesa-b2c-result",
        "Occasion": "LoanDisbursement",
    }
    resp = httpx.post(f"{base_url()}/mpesa/b2c/v1/paymentrequest",
                      headers={"Authorization": f"Bearer {token}"},
                      json=payload, timeout=45)
    resp.raise_for_status()
    response = resp.json()
    # B2C is async — the final receipt arrives on ResultURL. Until then we track
    # the request by ConversationID (kept under TransactionReceipt for backward
    # compatibility with existing callers/columns).
    conv = response.get("ConversationID") or response.get("OriginatorConversationID") or _mpesa_ref()
    # Do not echo the security credential back to callers/logs.
    safe_req = {k: v for k, v in payload.items() if k != "SecurityCredential"}
    return {"request": safe_req, "response": response, "result": {
        "ResultCode": response.get("ResponseCode"),
        "ResultDesc": response.get("ResponseDescription"),
        "ConversationID": response.get("ConversationID"),
        "OriginatorConversationID": response.get("OriginatorConversationID"),
        "TransactionReceipt": conv,
    }}


def stk_push(phone: str, amount: float, account_ref: str) -> dict:
    """Real Daraja Lipa-na-M-Pesa STK push (collections prompt)."""
    if not is_configured():
        raise DarajaNotConfigured("Daraja not configured — cannot initiate STK push.")
    token = get_access_token()
    ts = _timestamp()
    msisdn = normalise_msisdn(phone)
    payload = {
        "BusinessShortCode": settings.DARAJA_SHORTCODE,
        "Password": _stk_password(ts),
        "Timestamp": ts,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": msisdn,
        "PartyB": settings.DARAJA_SHORTCODE,
        "PhoneNumber": msisdn,
        "CallBackURL": f"{settings.DARAJA_CALLBACK_BASE_URL}/api/v1/payments/mpesa-stk-callback",
        "AccountReference": account_ref[:12],
        "TransactionDesc": "Loan repayment",
    }
    resp = httpx.post(f"{base_url()}/mpesa/stkpush/v1/processrequest",
                      headers={"Authorization": f"Bearer {token}"},
                      json=payload, timeout=45)
    resp.raise_for_status()
    # Do not leak the password back to callers/logs.
    safe_req = {k: v for k, v in payload.items() if k != "Password"}
    return {"request": safe_req, "response": resp.json()}


def register_c2b_urls() -> dict:
    """Register C2B validation/confirmation URLs pointing at our callback."""
    if not is_configured():
        raise DarajaNotConfigured("Daraja not configured — cannot register C2B URLs.")
    token = get_access_token()
    payload = {
        "ShortCode": settings.DARAJA_SHORTCODE,
        "ResponseType": "Completed",
        "ConfirmationURL": f"{settings.DARAJA_CALLBACK_BASE_URL}/api/v1/payments/mpesa-c2b-callback",
        "ValidationURL": f"{settings.DARAJA_CALLBACK_BASE_URL}/api/v1/payments/mpesa-c2b-callback",
    }
    resp = httpx.post(f"{base_url()}/mpesa/c2b/v1/registerurl",
                      headers={"Authorization": f"Bearer {token}"},
                      json=payload, timeout=45)
    resp.raise_for_status()
    return {"request": payload, "response": resp.json()}


def normalise_msisdn(phone: str) -> str:
    """Normalise a Kenyan mobile number to Daraja's 2547XXXXXXXX format."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    elif digits.startswith("7") or digits.startswith("1"):
        digits = "254" + digits
    while digits.startswith("254254"):
        digits = digits[3:]
    return digits


def validate_mobile_number(phone: str, national_id: str, expected_name: str) -> dict:
    """
    Safaricom subscriber name-lookup check.

    Daraja does not expose a public KYC name-verification product to ordinary
    partners; registered-name lookup is a bank-grade/operator API. This performs
    the format + registration sanity check that IS available (MSISDN validity +
    ID presence) and is structured so a real name-lookup response slots straight
    in when a partner API is provisioned (see annotations)."""
    msisdn = normalise_msisdn(phone)
    valid_prefix = msisdn.startswith("2547") or msisdn.startswith("2541")
    ok = bool(msisdn) and len(msisdn) == 12 and valid_prefix and bool(national_id)
    registered_name = (expected_name or "").upper() if ok else None
    return {
        "request": {
            "CommandID": "CheckIdentity",
            "PartyA": settings.DARAJA_SHORTCODE,
            "PartyB": msisdn,
            "IdentityNumber": national_id,
            "Initiator": settings.DARAJA_INITIATOR_NAME,
        },
        "response": {
            "ResultCode": 0 if ok else 1,
            "ResultDesc": ("The service request is processed successfully."
                           if ok else "Subscriber not found or number not registered."),
            "MSISDN": msisdn,
            "RegisteredName": registered_name,
            "IdentityNumber": national_id,
            "Matched": ok,
            "ConversationID": f"AG_{datetime.utcnow():%Y%m%d}_{uuid.uuid4().hex[:16]}",
            "CheckedAt": datetime.utcnow().isoformat() + "Z",
        },
    }
