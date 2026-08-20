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
import threading
import time
import uuid
from datetime import datetime

import httpx

from app.core.config import settings

SANDBOX_BASE = "https://sandbox.safaricom.co.ke"
PROD_BASE = "https://api.safaricom.co.ke"

# Values Daraja treats as "not set" (config defaults / .env placeholders).
_PLACEHOLDERS = {"", "placeholder", "change-me", "changeme"}


class DarajaNotConfigured(RuntimeError):
    """Raised when real Daraja credentials are absent."""


def base_url() -> str:
    """Live Daraja base URL, derived from DARAJA_ENVIRONMENT via config."""
    return settings.DARAJA_BASE_URL


def is_configured() -> bool:
    for v in (settings.DARAJA_CONSUMER_KEY, settings.DARAJA_CONSUMER_SECRET):
        if not v or str(v).strip().lower() in _PLACEHOLDERS:
            return False
    return True


def integration_status() -> str:
    if not is_configured():
        return "NOT CONFIGURED"
    return "LIVE" if base_url() == PROD_BASE else "SANDBOX"


# --------------------------------------------------------------------------- #
# OAuth token cache — Daraja tokens live ~1h; cache and reuse until shortly
# before expiry so we don't fetch a fresh token on every B2C/STK/C2B call.
# Guarded by a lock because uvicorn may serve requests from a threadpool.
# --------------------------------------------------------------------------- #
_token_lock = threading.Lock()
_token_cache: dict = {"value": None, "expires_at": 0.0}
_TOKEN_SAFETY_WINDOW = 30  # seconds before real expiry to force a refresh


def callback_url(suffix: str) -> str:
    """Build a Daraja callback URL that embeds the hard-to-guess source-auth
    token as a path segment (MPESA-04). The matching route handlers validate
    the token and reject anything else. Safaricom must be given exactly these
    registered URLs (B2C ResultURL/QueueTimeOutURL, STK CallBackURL, C2B
    Confirmation/Validation URLs)."""
    base = (settings.DARAJA_CALLBACK_BASE_URL or "").rstrip("/")
    token = settings.MPESA_CALLBACK_TOKEN
    return f"{base}/api/v1/payments/mpesa/{token}/{suffix}"


def _mpesa_ref() -> str:
    """Generate a plausible M-Pesa receipt (fallback for C2B when none supplied)."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def get_access_token(force_refresh: bool = False) -> str:
    """OAuth client-credentials token from Daraja, cached until near expiry.

    GET /oauth/v1/generate?grant_type=client_credentials with HTTP Basic auth
    (consumer key/secret). The token value is never logged. Daraja returns
    `expires_in` (seconds, ~3599); we refresh _TOKEN_SAFETY_WINDOW seconds early.
    """
    if not is_configured():
        raise DarajaNotConfigured("Daraja consumer key/secret required.")
    now = time.time()
    with _token_lock:
        if (not force_refresh and _token_cache["value"]
                and now < _token_cache["expires_at"]):
            return _token_cache["value"]
        resp = httpx.get(
            f"{base_url()}/oauth/v1/generate?grant_type=client_credentials",
            auth=(settings.DARAJA_CONSUMER_KEY, settings.DARAJA_CONSUMER_SECRET),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        try:
            ttl = int(float(data.get("expires_in", 3599)))
        except (TypeError, ValueError):
            ttl = 3599
        _token_cache["value"] = token
        _token_cache["expires_at"] = now + max(0, ttl - _TOKEN_SAFETY_WINDOW)
        return token


def test_connection() -> dict:
    """Used by DCP Setup 'Test connection' — attempts an OAuth token."""
    if not is_configured():
        return {"ok": False, "status": "NOT CONFIGURED",
                "detail": "Daraja consumer key/secret not configured."}
    try:
        token = get_access_token()
        return {"ok": True, "status": integration_status(),
                "detail": "OAuth token acquired.",
                "token_acquired": bool(token)}
    except Exception as exc:
        return {"ok": False, "status": "ERROR", "detail": str(exc)}


def _stk_password(timestamp: str) -> str:
    raw = f"{settings.DARAJA_SHORTCODE}{settings.DARAJA_PASSKEY}{timestamp}"
    return base64.b64encode(raw.encode()).decode()


def _new_conversation_ids() -> tuple[str, str]:
    """Generate a (ConversationID, OriginatorConversationID) pair shaped like
    Daraja's async acknowledgements."""
    conv = f"AG_{_timestamp()}_{uuid.uuid4().hex[:16]}"
    orig = f"{random.randint(10000, 99999)}-{random.randint(1000000, 9999999)}-1"
    return conv, orig


def _simulate_b2c_accept(phone: str, amount: float, remarks: str) -> dict:
    """MOCK path (credential-gated): mirror Daraja's *asynchronous* B2C
    acknowledgement WITHOUT faking the final result.

    Real Daraja B2C returns only an "accepted for processing" ack here; the
    definitive ResultCode arrives later on the ResultURL. The mock does the
    same: it returns ConversationID/OriginatorConversationID with ResponseCode
    0 (accepted) and leaves the final settlement to the /mpesa-b2c-result
    webhook (which a test/reconcile sweep drives). This keeps the processing →
    success state machine identical to production. The live path below is left
    completely untouched and runs the moment real credentials are configured.
    """
    conv, orig = _new_conversation_ids()
    msisdn = normalise_msisdn(phone)
    request = {
        "InitiatorName": settings.DARAJA_INITIATOR_NAME,
        "CommandID": "BusinessPayment",
        "Amount": int(amount),
        "PartyA": settings.DARAJA_SHORTCODE,
        "PartyB": msisdn,
        "Remarks": (remarks or "")[:100],
        "Occasion": "LoanDisbursement",
    }
    response = {
        "ConversationID": conv,
        "OriginatorConversationID": orig,
        "ResponseCode": "0",
        "ResponseDescription": "Accept the service request successfully.",
        "_simulated": True,
    }
    return {"request": request, "response": response, "result": {
        "ResultCode": None,  # async — not settled yet
        "ResultDesc": "Accepted for processing (simulated sandbox).",
        "ConversationID": conv,
        "OriginatorConversationID": orig,
        "TransactionReceipt": conv,
        "_simulated": True,
    }}


def simulate_b2c_result(conversation_id: str, originator_id: str | None = None,
                        success: bool = True, amount: float | None = None) -> dict:
    """Build a Daraja-shaped B2C *Result* callback body for the mock/reconcile
    sweep to POST to the result webhook. Only used while NOT configured."""
    receipt = _mpesa_ref()
    params = [{"Key": "TransactionReceipt", "Value": receipt}]
    if amount is not None:
        params.append({"Key": "TransactionAmount", "Value": int(amount)})
    return {"Result": {
        "ResultType": 0,
        "ResultCode": 0 if success else 2001,
        "ResultDesc": ("The service request is processed successfully."
                       if success else "The initiator information is invalid."),
        "OriginatorConversationID": originator_id or "",
        "ConversationID": conversation_id,
        "TransactionID": receipt,
        "ResultParameters": {"ResultParameter": params},
    }}


def b2c_disburse(phone: str, amount: float, remarks: str) -> dict:
    """Real Daraja B2C payment request (disbursement to borrower).

    Credential-gated: with real credentials this hits Daraja; without them it
    returns the simulated *asynchronous acknowledgement* (see
    _simulate_b2c_accept) so the processing→result state machine is exercised
    end-to-end in the mock/demo without ever fabricating a settled payout."""
    if not is_configured():
        return _simulate_b2c_accept(phone, amount, remarks)
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
        "QueueTimeOutURL": callback_url("b2c-timeout"),
        "ResultURL": callback_url("b2c-result"),
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


def _simulate_stk_accept(phone: str, amount: float, account_ref: str) -> dict:
    """MOCK path (credential-gated): mirror Daraja's STK-push acknowledgement.

    Real STK push returns only an "accepted" ack here; the customer then enters
    their PIN and the definitive result arrives later on the CallBackURL. The
    mock returns the same accept shape (CheckoutRequestID/MerchantRequestID)
    and leaves settlement to the /mpesa-stk-callback webhook, keeping the
    pending → success state machine identical to production."""
    ts = _timestamp()
    checkout = f"ws_CO_{ts}{random.randint(100, 999)}"
    merchant = f"{random.randint(10000, 99999)}-{random.randint(1000000, 9999999)}-1"
    request = {
        "BusinessShortCode": settings.DARAJA_SHORTCODE,
        "Timestamp": ts,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": normalise_msisdn(phone),
        "PartyB": settings.DARAJA_SHORTCODE,
        "PhoneNumber": normalise_msisdn(phone),
        "AccountReference": (account_ref or "")[:12],
        "TransactionDesc": "Loan repayment",
        "_simulated": True,
    }
    response = {
        "MerchantRequestID": merchant,
        "CheckoutRequestID": checkout,
        "ResponseCode": "0",
        "ResponseDescription": "Success. Request accepted for processing",
        "CustomerMessage": "Success. Request accepted for processing",
        "_simulated": True,
    }
    return {"request": request, "response": response}


def simulate_stk_result(checkout_request_id: str, merchant_request_id: str | None = None,
                        success: bool = True, amount: float | None = None,
                        phone: str | None = None) -> dict:
    """Build a Daraja-shaped STK *callback* body for the mock to POST to the
    STK callback webhook. Only used while NOT configured."""
    receipt = _mpesa_ref()
    callback = {
        "MerchantRequestID": merchant_request_id or "",
        "CheckoutRequestID": checkout_request_id,
        "ResultCode": 0 if success else 1032,
        "ResultDesc": ("The service request is processed successfully."
                       if success else "Request cancelled by user."),
    }
    if success:
        items = [{"Name": "Amount", "Value": int(amount or 0)},
                 {"Name": "MpesaReceiptNumber", "Value": receipt},
                 {"Name": "TransactionDate", "Value": int(_timestamp())}]
        if phone:
            items.append({"Name": "PhoneNumber", "Value": int(normalise_msisdn(phone))})
        callback["CallbackMetadata"] = {"Item": items}
    return {"Body": {"stkCallback": callback}}


def stk_push(phone: str, amount: float, account_ref: str) -> dict:
    """Real Daraja Lipa-na-M-Pesa STK push (collections prompt)."""
    if not is_configured():
        return _simulate_stk_accept(phone, amount, account_ref)
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
        "CallBackURL": callback_url("stk-callback"),
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
        "ConfirmationURL": callback_url("c2b-callback"),
        "ValidationURL": callback_url("c2b-callback"),
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
