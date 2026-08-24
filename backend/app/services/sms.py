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
import logging
import re
import time
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import SmsLog, SmsRateCard, SmsTemplate, Tenant, SmsOptOut

PROVIDER_NAME = "uwazii"

# --- Active SMS rate cache ----------------------------------------------------
# The active rate rarely changes; cache it briefly to avoid a query per message.
_rate_cache: dict = {"rate": None, "fetched_at": 0.0}
_RATE_TTL = 60  # seconds


def get_active_rate(db: Session) -> dict | None:
    """Return the current active SMS rate as
    {sell_price_kes, cost_price_kes, margin_kes} (Decimal), or None if unset.
    Cached in-process for a short TTL; never raises."""
    import time as _t
    now = _t.time()
    if _rate_cache["rate"] is not None and (now - _rate_cache["fetched_at"]) < _RATE_TTL:
        return _rate_cache["rate"]
    try:
        row = (db.query(SmsRateCard)
               .filter(SmsRateCard.active == True)  # noqa: E712
               .order_by(SmsRateCard.effective_from.desc()).first())
    except Exception:
        return _rate_cache["rate"]  # fall back to any cached value
    if not row:
        return None
    rate = {
        "sell_price_kes": row.sell_price_kes,
        "cost_price_kes": row.cost_price_kes,
        "margin_kes": (row.sell_price_kes or 0) - (row.cost_price_kes or 0),
    }
    _rate_cache["rate"] = rate
    _rate_cache["fetched_at"] = now
    return rate

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

    # Per-message billing: revenue is recognised on SENT (not delivered).
    # Snapshot the CURRENT active rate onto the row so later rate changes never
    # rewrite historical revenue. Non-sent messages are not billed.
    if result["status"] == "sent":
        rate = get_active_rate(db)
        if rate:
            log.billable = True
            log.sell_price_kes = rate["sell_price_kes"]
            log.cost_price_kes = rate["cost_price_kes"]
            log.margin_kes = rate["margin_kes"]
        else:
            log.billable = True  # sent counts as billable even if rate not yet set
    else:
        log.billable = False

    db.add(log)
    db.flush()
    return log


# --- Per-DCP customizable templates ------------------------------------------
#
# Every loan-lifecycle SMS is now rendered from a per-tenant template that may be
# customised in the Messaging admin screen. Each template is a body string with
# {{placeholder}} tokens drawn from the CANONICAL placeholder set below. When a
# tenant has no stored row for an event the built-in DEFAULT_TEMPLATES are used,
# so existing tenants keep working unchanged.

# The six customizable lifecycle events.
EVENT_KEYS = [
    "loan_qualified", "loan_disbursed", "repayment_reminder",
    "overdue_alert", "defaulted", "payment_receipt",
]

# Promotional/collections reminders that a borrower may opt out of. Transactional
# events (loan_disbursed, payment_receipt, loan_qualified) are NEVER suppressed.
SUPPRESSIBLE_EVENTS = {"repayment_reminder", "overdue_alert", "defaulted"}


def is_opted_out(db: Session, tenant_id: int, phone: str) -> bool:
    """Return True when the phone has an active opt-out for this tenant.

    Fail-safe: any error resolves to False (do not suppress) and logs a warning,
    so a register problem can never silently drop transactional-adjacent SMS."""
    try:
        norm = normalise_phone(phone)
        row = (db.query(SmsOptOut)
               .filter(SmsOptOut.tenant_id == tenant_id,
                       SmsOptOut.phone == norm,
                       SmsOptOut.active.is_(True)).first())
        return row is not None
    except Exception as exc:  # pragma: no cover - defensive
        logging.getLogger("finyl.sms").warning(
            "opt-out lookup failed (fail-open, sending): %s", exc)
        return False

# Canonical placeholder tokens available to every template, with a human label.
CANONICAL_PLACEHOLDERS = {
    "first_name": "Borrower's first name",
    "last_name": "Borrower's last name",
    "amount": "Loan principal (payment amount for receipts), KES, thousands-grouped",
    "due_date": "Loan due date",
    "balance": "Outstanding balance, KES, thousands-grouped",
    "account_number": "Loan account number",
    "days_left": "Days remaining until the due date (reminders)",
    "dcp_name": "Your DCP / company name",
    "loan_ref": "Loan account number / transaction reference",
}

# Human labels for each event key (surfaced in the admin UI).
EVENT_LABELS = {
    "loan_qualified": "Loan qualified / approved",
    "loan_disbursed": "Loan disbursed",
    "repayment_reminder": "Repayment reminder",
    "overdue_alert": "Overdue alert",
    "defaulted": "Loan defaulted",
    "payment_receipt": "Payment receipt",
}

# Built-in fallbacks — identical wording to migration 007's seed rows.
DEFAULT_TEMPLATES = {
    "loan_qualified": (
        "Dear {{first_name}}, good news! Your loan {{account_number}} of KES "
        "{{amount}} has been APPROVED. It will be disbursed to your M-Pesa shortly. "
        "{{dcp_name}}."),
    "loan_disbursed": (
        "Dear {{first_name}}, your loan {{account_number}} of KES {{amount}} has "
        "been disbursed to your M-Pesa. Repay by {{due_date}}. {{dcp_name}}."),
    "repayment_reminder": (
        "Hi {{first_name}}, a friendly reminder: loan {{account_number}} balance "
        "KES {{balance}} is due in {{days_left}} day(s) on {{due_date}}. Pay via "
        "M-Pesa Paybill. {{dcp_name}}."),
    "overdue_alert": (
        "Dear {{first_name}}, loan {{account_number}} is OVERDUE. Outstanding KES "
        "{{balance}}. Penalties may apply. Kindly settle to protect your credit "
        "score. {{dcp_name}}."),
    "defaulted": (
        "Dear {{first_name}}, loan {{account_number}} has been marked DEFAULTED. "
        "Outstanding KES {{balance}}. Please contact us urgently to settle and "
        "protect your credit score. {{dcp_name}}."),
    "payment_receipt": (
        "Payment received: KES {{amount}} for loan {{account_number}} (ref "
        "{{loan_ref}}). New balance KES {{balance}}. Thank you! {{dcp_name}}."),
}

_TOKEN_RE = re.compile(r"{{\s*(\w+)\s*}}")


def render_body(body: str, context: dict) -> str:
    """Substitute every {{token}} in `body` with context[token] (missing/None ->
    empty string). Unknown tokens render as empty so a template never leaks a
    literal placeholder to a borrower."""
    def _repl(m):
        val = (context or {}).get(m.group(1))
        return "" if val is None else str(val)
    return _TOKEN_RE.sub(_repl, body or "")


def dcp_name(db: Session, tenant_id: int) -> str:
    """Resolve the tenant's display name for the {{dcp_name}} token."""
    try:
        t = db.get(Tenant, tenant_id)
        return t.name if t and t.name else "Finyl-DCP"
    except Exception:
        return "Finyl-DCP"


def get_template(db: Session, tenant_id: int, event_key: str) -> tuple[str, bool]:
    """Return (body, active) for a tenant's event template, falling back to the
    built-in DEFAULT_TEMPLATES (always active) when no stored row exists."""
    try:
        row = (db.query(SmsTemplate)
               .filter(SmsTemplate.tenant_id == tenant_id,
                       SmsTemplate.event_key == event_key).first())
    except Exception:
        row = None
    if row is not None:
        return row.body, bool(row.active)
    return DEFAULT_TEMPLATES.get(event_key, ""), True


def render_template(db: Session, tenant_id: int, event_key: str, context: dict) -> tuple[str, bool]:
    """Render a tenant's template for `event_key` against `context`.
    Returns (rendered_text, active)."""
    body, active = get_template(db, tenant_id, event_key)
    return render_body(body, context), active


def _base_context(db, tenant_id, borrower, loan) -> dict:
    """Build the canonical context dict from a borrower + loan for rendering."""
    return {
        "first_name": getattr(borrower, "first_name", "") or "",
        "last_name": getattr(borrower, "last_name", "") or "",
        "amount": (f"{float(loan.principal):,.0f}"
                   if loan is not None and loan.principal is not None else ""),
        "due_date": (loan.due_date if loan is not None and loan.due_date else ""),
        "balance": (f"{float(loan.outstanding_balance or 0):,.0f}"
                    if loan is not None else ""),
        "account_number": getattr(loan, "account_number", "") or "",
        "days_left": "",
        "dcp_name": dcp_name(db, tenant_id),
        "loan_ref": getattr(loan, "account_number", "") or "",
    }


def _fire(db, tenant_id, phone, event_key, context, trigger_type) -> SmsLog | None:
    """Render the tenant's template and dispatch it. Returns None (and sends
    nothing) when the tenant has switched the template OFF via the active flag."""
    body, active = render_template(db, tenant_id, event_key, context)
    if not active:
        return None
    # Opt-out enforcement — only for suppressible (non-transactional) events.
    if event_key in SUPPRESSIBLE_EVENTS and is_opted_out(db, tenant_id, phone):
        logging.getLogger("finyl.sms").info(
            "SMS suppressed for opted-out recipient (event=%s)", event_key)
        return None
    return send_sms(db, tenant_id, phone, body, trigger_type)


# --- Loan-lifecycle triggers (template-driven) --------------------------------

def sms_loan_qualified(db, tenant_id, borrower, loan):
    """Fired when a loan is APPROVED (qualified) — before disbursement."""
    return _fire(db, tenant_id, borrower.phone, "loan_qualified",
                 _base_context(db, tenant_id, borrower, loan), "loan_qualified")


def sms_loan_disbursed(db, tenant_id, borrower, loan):
    """Fired when an approved loan is DISBURSED to the borrower's M-Pesa."""
    return _fire(db, tenant_id, borrower.phone, "loan_disbursed",
                 _base_context(db, tenant_id, borrower, loan), "loan_disbursed")


# Backward-compatible alias — historical name mapped to the disbursed event.
sms_loan_approval = sms_loan_disbursed


def sms_repayment_reminder(db, tenant_id, borrower, loan, days_left: int):
    ctx = _base_context(db, tenant_id, borrower, loan)
    ctx["days_left"] = days_left
    return _fire(db, tenant_id, borrower.phone, "repayment_reminder",
                 ctx, "repayment_reminder")


def sms_overdue_alert(db, tenant_id, borrower, loan):
    return _fire(db, tenant_id, borrower.phone, "overdue_alert",
                 _base_context(db, tenant_id, borrower, loan), "overdue_alert")


def sms_defaulted(db, tenant_id, borrower, loan):
    """Fired when a loan is moved to the DEFAULTED status."""
    return _fire(db, tenant_id, borrower.phone, "defaulted",
                 _base_context(db, tenant_id, borrower, loan), "defaulted")


def sms_payment_receipt(db, tenant_id, borrower, loan, amount, ref):
    ctx = _base_context(db, tenant_id, borrower, loan)
    ctx["amount"] = f"{float(amount):,.0f}"
    ctx["loan_ref"] = ref
    return _fire(db, tenant_id, borrower.phone, "payment_receipt",
                 ctx, "payment_receipt")


def sms_ticket_resolution(db, tenant_id, phone, ticket_id):
    """Complaint resolution notice — not part of the customizable loan lifecycle,
    so it keeps its fixed wording."""
    return send_sms(
        db, tenant_id, phone,
        f"Your complaint {ticket_id} has been RESOLVED. Thank you for your patience. "
        f"Reply HELP for further assistance. Finyl-DCP.",
        "ticket_resolution",
    )
