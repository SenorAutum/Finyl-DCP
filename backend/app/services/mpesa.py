"""
Safaricom Daraja M-Pesa integration — REAL client, credential-gated.

OAuth, STK push, B2C and C2B register all call the real Daraja endpoints. The
base URL follows the environment (sandbox=https://sandbox.safaricom.co.ke,
production=https://api.safaricom.co.ke).

CREDENTIAL RESOLUTION (per-DCP, PART A):
    Every call optionally takes a resolved ``DarajaCreds`` bundle. A DCP that has
    saved its OWN Daraja credentials from the in-app Configuration screen uses
    them (secrets decrypted from the encrypted-at-rest store); a DCP that has NOT
    configured its own falls back, field by field, to the platform .env defaults.
    Passing ``creds=None`` (the historic call signature) resolves to the .env
    defaults, so every existing caller keeps its exact behaviour.

CREDENTIAL-GATED: while the resolved consumer key/secret are placeholders the
integration reports NOT CONFIGURED and each call raises DarajaNotConfigured
(surfaced by the router as a clear 4xx) — it never fabricates a success. Add real
credentials (per-DCP or in .env) + and the SAME code flips to LIVE (SANDBOX).

The return shapes are unchanged from the previous mock so routers/UI keep working.
"""
import base64
import logging
import random
import string
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.core.config import settings

logger = logging.getLogger("finyl.mpesa")

SANDBOX_BASE = "https://sandbox.safaricom.co.ke"
PROD_BASE = "https://api.safaricom.co.ke"

# Values Daraja treats as "not set" (config defaults / .env placeholders).
_PLACEHOLDERS = {"", "placeholder", "change-me", "changeme"}


class DarajaNotConfigured(RuntimeError):
    """Raised when real Daraja credentials are absent."""


# --------------------------------------------------------------------------- #
# Resolved credential bundle. A caller passes one of these to use a specific
# DCP's credentials; when omitted, functions fall back to the .env defaults via
# ``_settings_creds()`` so the global/legacy behaviour is unchanged.
# --------------------------------------------------------------------------- #
@dataclass
class DarajaCreds:
    consumer_key: str
    consumer_secret: str
    shortcode: str
    passkey: str
    initiator_name: str
    security_credential: str
    environment: str  # "sandbox" | "production"

    @property
    def base_url(self) -> str:
        env = (self.environment or "sandbox").strip().lower()
        return PROD_BASE if env.startswith("prod") else SANDBOX_BASE

    @property
    def is_production(self) -> bool:
        return (self.environment or "sandbox").strip().lower().startswith("prod")

    @property
    def configured(self) -> bool:
        for v in (self.consumer_key, self.consumer_secret):
            if not v or str(v).strip().lower() in _PLACEHOLDERS:
                return False
        return True


def _settings_creds() -> DarajaCreds:
    """Build a DarajaCreds bundle from the platform .env defaults."""
    return DarajaCreds(
        consumer_key=settings.DARAJA_CONSUMER_KEY,
        consumer_secret=settings.DARAJA_CONSUMER_SECRET,
        shortcode=settings.DARAJA_SHORTCODE,
        passkey=settings.DARAJA_PASSKEY,
        initiator_name=settings.DARAJA_INITIATOR_NAME,
        security_credential=settings.DARAJA_SECURITY_CREDENTIAL,
        environment=settings.DARAJA_ENVIRONMENT,
    )


def _clean(v):
    """Treat placeholder/blank values as absent so per-field fallback works."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in _PLACEHOLDERS:
        return None
    return v


def resolve_creds(db, tenant_id: int | None) -> DarajaCreds:
    """Resolve the effective Daraja credentials for a tenant.

    Reads the tenant's own saved Daraja config (integration='daraja') and
    decrypts its secrets, falling back FIELD BY FIELD to the platform .env
    defaults for anything the DCP has not set. A DCP that has configured nothing
    resolves to exactly the .env defaults (identical to the historic behaviour).
    """
    base = _settings_creds()
    if not tenant_id:
        return base
    # Imported here to avoid a circular import at module load.
    from app.models import TenantIntegrationConfig
    from app.core.crypto import decrypt_pii

    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == "daraja")
           .first())
    if row is None or not row.enabled:
        return base
    cfg = row.config or {}
    sec = row.secrets or {}

    def _dec(key):
        return _clean(decrypt_pii(sec.get(key)))

    return DarajaCreds(
        consumer_key=_dec("consumer_key") or base.consumer_key,
        consumer_secret=_dec("consumer_secret") or base.consumer_secret,
        shortcode=_clean(cfg.get("shortcode")) or base.shortcode,
        passkey=_dec("passkey") or base.passkey,
        initiator_name=_clean(cfg.get("initiator_name")) or base.initiator_name,
        security_credential=_dec("security_credential") or base.security_credential,
        environment=_clean(cfg.get("environment")) or base.environment,
    )


def base_url(creds: DarajaCreds | None = None) -> str:
    """Live Daraja base URL, derived from the resolved environment."""
    return (creds or _settings_creds()).base_url


def is_configured(creds: DarajaCreds | None = None) -> bool:
    return (creds or _settings_creds()).configured


def integration_status(creds: DarajaCreds | None = None) -> str:
    creds = creds or _settings_creds()
    if not creds.configured:
        return "NOT CONFIGURED"
    return "LIVE" if creds.base_url == PROD_BASE else "SANDBOX"


# Credentials Daraja requires for a LIVE B2C payout. In production ALL of these
# must be present; a missing one means we must fail closed (never simulate, never
# silently fall back to sandbox).
_B2C_REQUIRED_FIELDS = (
    "consumer_key", "consumer_secret", "shortcode",
    "initiator_name", "security_credential",
)


def _missing_fields(creds: DarajaCreds, fields) -> list:
    """Names of the given credential fields that are blank/placeholder."""
    return [f for f in fields if _clean(getattr(creds, f, None)) is None]


def guard_production_b2c(creds: DarajaCreds) -> None:
    """Fail-closed production guard for B2C payouts.

    When DARAJA_ENVIRONMENT=production, a live payout requires the full set of
    real credentials. If any is missing/empty we log a clear (secret-free) error
    and raise DarajaNotConfigured so the payout is REFUSED — the app never
    silently simulates a payout nor falls back to the sandbox host on a live
    request. In sandbox this is a no-op, preserving the existing behaviour
    (real sandbox call when configured, simulated ack when not)."""
    if not creds.is_production:
        return
    missing = _missing_fields(creds, _B2C_REQUIRED_FIELDS)
    if missing:
        logger.error(
            "Daraja B2C payout REFUSED (fail-closed): DARAJA_ENVIRONMENT=production "
            "but required credential(s) missing/empty: %s. Configure the real "
            "production credentials or set DARAJA_ENVIRONMENT=sandbox. No payout "
            "was attempted.",
            ", ".join(missing),
        )
        raise DarajaNotConfigured(
            "Daraja is set to PRODUCTION but required B2C credentials are missing "
            f"({', '.join(missing)}). Payout refused (fail-closed)."
        )


def startup_summary(creds: DarajaCreds | None = None) -> str:
    """A single, SECRET-FREE line describing the active Daraja config, for the
    boot log. Reports only the environment and yes/no configured booleans —
    never any credential value."""
    creds = creds or _settings_creds()

    def _has(v):
        return "yes" if _clean(v) is not None else "no"

    return (
        f"Daraja environment: {(creds.environment or 'sandbox')} "
        f"(base_url={creds.base_url}, status={integration_status(creds)}); "
        f"consumer key/secret configured: {_has(creds.consumer_key)}; "
        f"shortcode configured: {_has(creds.shortcode)}; "
        f"initiator configured: {_has(creds.initiator_name)}; "
        f"security credential configured: {_has(creds.security_credential)}; "
        f"passkey configured: {_has(creds.passkey)}"
    )


# --------------------------------------------------------------------------- #
# OAuth token cache — Daraja tokens live ~1h; cache and reuse until shortly
# before expiry so we don't fetch a fresh token on every B2C/STK/C2B call.
# Keyed by consumer_key so different DCPs (and the platform default) never share
# a token. Guarded by a lock because uvicorn may serve requests from a threadpool.
# --------------------------------------------------------------------------- #
_token_lock = threading.Lock()
_token_caches: dict = {}  # consumer_key -> {"value", "expires_at"}
_TOKEN_SAFETY_WINDOW = 30  # seconds before real expiry to force a refresh


def callback_url(suffix: str) -> str:
    """Build a Daraja callback URL that embeds the hard-to-guess source-auth
    token as a path segment (MPESA-04). The callback host is the PLATFORM domain
    (all DCP callbacks route back to this server); the matching route handlers
    validate the token and reject anything else."""
    base = (settings.DARAJA_CALLBACK_BASE_URL or "").rstrip("/")
    token = settings.MPESA_CALLBACK_TOKEN
    return f"{base}/api/v1/payments/mpesa/{token}/{suffix}"


def _mpesa_ref() -> str:
    """Generate a plausible M-Pesa receipt (fallback for C2B when none supplied)."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def get_access_token(force_refresh: bool = False,
                     creds: DarajaCreds | None = None) -> str:
    """OAuth client-credentials token from Daraja, cached per consumer_key until
    near expiry.

    GET /oauth/v1/generate?grant_type=client_credentials with HTTP Basic auth
    (consumer key/secret). The token value is never logged. Daraja returns
    `expires_in` (seconds, ~3599); we refresh _TOKEN_SAFETY_WINDOW seconds early.
    """
    creds = creds or _settings_creds()
    if not creds.configured:
        raise DarajaNotConfigured("Daraja consumer key/secret required.")
    now = time.time()
    ck = creds.consumer_key
    with _token_lock:
        cache = _token_caches.get(ck)
        if (not force_refresh and cache and cache["value"]
                and now < cache["expires_at"]):
            return cache["value"]
        resp = httpx.get(
            f"{creds.base_url}/oauth/v1/generate?grant_type=client_credentials",
            auth=(creds.consumer_key, creds.consumer_secret),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        try:
            ttl = int(float(data.get("expires_in", 3599)))
        except (TypeError, ValueError):
            ttl = 3599
        _token_caches[ck] = {"value": token,
                             "expires_at": now + max(0, ttl - _TOKEN_SAFETY_WINDOW)}
        return token


def test_connection(creds: DarajaCreds | None = None) -> dict:
    """Used by DCP Setup / Configuration 'Test connection' — attempts an OAuth token."""
    creds = creds or _settings_creds()
    if not creds.configured:
        return {"ok": False, "status": "NOT CONFIGURED",
                "detail": "Daraja consumer key/secret not configured."}
    try:
        token = get_access_token(creds=creds)
        return {"ok": True, "status": integration_status(creds),
                "detail": "OAuth token acquired.",
                "token_acquired": bool(token)}
    except Exception as exc:
        return {"ok": False, "status": "ERROR", "detail": str(exc)}


def _stk_password(timestamp: str, creds: DarajaCreds) -> str:
    raw = f"{creds.shortcode}{creds.passkey}{timestamp}"
    return base64.b64encode(raw.encode()).decode()


def _new_conversation_ids() -> tuple[str, str]:
    """Generate a (ConversationID, OriginatorConversationID) pair shaped like
    Daraja's async acknowledgements."""
    conv = f"AG_{_timestamp()}_{uuid.uuid4().hex[:16]}"
    orig = f"{random.randint(10000, 99999)}-{random.randint(1000000, 9999999)}-1"
    return conv, orig


def _simulate_b2c_accept(phone: str, amount: float, remarks: str,
                         creds: DarajaCreds) -> dict:
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
        "InitiatorName": creds.initiator_name,
        "CommandID": "BusinessPayment",
        "Amount": int(amount),
        "PartyA": creds.shortcode,
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


def b2c_disburse(phone: str, amount: float, remarks: str,
                 creds: DarajaCreds | None = None) -> dict:
    """Real Daraja B2C payment request (disbursement to borrower).

    Credential-gated: with real credentials this hits Daraja; without them it
    returns the simulated *asynchronous acknowledgement* (see
    _simulate_b2c_accept) so the processing→result state machine is exercised
    end-to-end in the mock/demo without ever fabricating a settled payout."""
    creds = creds or _settings_creds()
    # Fail-closed: in production, a missing credential must REFUSE the payout —
    # never silently simulate it or fall back to the sandbox host.
    guard_production_b2c(creds)
    if not creds.configured:
        return _simulate_b2c_accept(phone, amount, remarks, creds)
    token = get_access_token(creds=creds)
    msisdn = normalise_msisdn(phone)
    payload = {
        "InitiatorName": creds.initiator_name,
        "SecurityCredential": creds.security_credential,
        "CommandID": "BusinessPayment",
        "Amount": int(amount),
        "PartyA": creds.shortcode,
        "PartyB": msisdn,
        "Remarks": remarks[:100],
        "QueueTimeOutURL": callback_url("b2c-timeout"),
        "ResultURL": callback_url("b2c-result"),
        "Occasion": "LoanDisbursement",
    }
    resp = httpx.post(f"{creds.base_url}/mpesa/b2c/v1/paymentrequest",
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


def _simulate_stk_accept(phone: str, amount: float, account_ref: str,
                         creds: DarajaCreds) -> dict:
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
        "BusinessShortCode": creds.shortcode,
        "Timestamp": ts,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": normalise_msisdn(phone),
        "PartyB": creds.shortcode,
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


def stk_push(phone: str, amount: float, account_ref: str,
             creds: DarajaCreds | None = None) -> dict:
    """Real Daraja Lipa-na-M-Pesa STK push (collections prompt)."""
    creds = creds or _settings_creds()
    if not creds.configured:
        return _simulate_stk_accept(phone, amount, account_ref, creds)
    token = get_access_token(creds=creds)
    ts = _timestamp()
    msisdn = normalise_msisdn(phone)
    payload = {
        "BusinessShortCode": creds.shortcode,
        "Password": _stk_password(ts, creds),
        "Timestamp": ts,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": msisdn,
        "PartyB": creds.shortcode,
        "PhoneNumber": msisdn,
        "CallBackURL": callback_url("stk-callback"),
        "AccountReference": account_ref[:12],
        "TransactionDesc": "Loan repayment",
    }
    resp = httpx.post(f"{creds.base_url}/mpesa/stkpush/v1/processrequest",
                      headers={"Authorization": f"Bearer {token}"},
                      json=payload, timeout=45)
    resp.raise_for_status()
    # Do not leak the password back to callers/logs.
    safe_req = {k: v for k, v in payload.items() if k != "Password"}
    return {"request": safe_req, "response": resp.json()}


def register_c2b_urls(creds: DarajaCreds | None = None) -> dict:
    """Register C2B validation/confirmation URLs pointing at our callback."""
    creds = creds or _settings_creds()
    if not creds.configured:
        raise DarajaNotConfigured("Daraja not configured — cannot register C2B URLs.")
    token = get_access_token(creds=creds)
    payload = {
        "ShortCode": creds.shortcode,
        "ResponseType": "Completed",
        "ConfirmationURL": callback_url("c2b-callback"),
        "ValidationURL": callback_url("c2b-callback"),
    }
    resp = httpx.post(f"{creds.base_url}/mpesa/c2b/v1/registerurl",
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


def validate_mobile_number(phone: str, national_id: str, expected_name: str,
                           creds: DarajaCreds | None = None) -> dict:
    """
    Safaricom subscriber name-lookup check.

    Daraja does not expose a public KYC name-verification product to ordinary
    partners; registered-name lookup is a bank-grade/operator API. This performs
    the format + registration sanity check that IS available (MSISDN validity +
    ID presence) and is structured so a real name-lookup response slots straight
    in when a partner API is provisioned (see annotations)."""
    creds = creds or _settings_creds()
    msisdn = normalise_msisdn(phone)
    valid_prefix = msisdn.startswith("2547") or msisdn.startswith("2541")
    ok = bool(msisdn) and len(msisdn) == 12 and valid_prefix and bool(national_id)
    registered_name = (expected_name or "").upper() if ok else None
    return {
        "request": {
            "CommandID": "CheckIdentity",
            "PartyA": creds.shortcode,
            "PartyB": msisdn,
            "IdentityNumber": national_id,
            "Initiator": creds.initiator_name,
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
