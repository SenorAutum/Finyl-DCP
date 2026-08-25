"""Daraja webhook hardening — perimeter IP allowlist, durable ingestion /
dead-letter queue, and robust multi-paybill tenant resolution.

This module is deliberately self-contained and imports application models
lazily (inside functions) so it can be imported from routers, the scheduler and
the test suite without circular imports. It NEVER changes existing money-movement
behaviour — it only adds a durability/observability wrapper around the existing
callback processing and a defence-in-depth source-IP check.

Three concerns live here:

  1. IP allowlist  — resolve the real client IP behind nginx and decide whether a
     callback source is a known Safaricom range (modes off / log / enforce).
  2. Durable ingestion / DLQ — persist every webhook as `received` first, then
     mark processed / failed (with exponential-backoff retry) / dead + alert.
  3. Tenant resolution — map an incoming callback to the owning tenant via the
     stored disbursement transaction (B2C) or BusinessShortCode -> tenant
     (multi-paybill C2B/B2C), never silently misrouting.
"""
import ipaddress
import logging
from datetime import datetime, timedelta

from app.core.config import settings

logger = logging.getLogger("finyl.webhook")


class WebhookUnresolved(Exception):
    """Raised by a processor when a webhook cannot be routed to a tenant/target
    (unknown transaction, no matching loan/shortcode). The durable-ingestion
    wrapper records the event as `failed` (tenant_id stays NULL) and it is retried
    then escalated to `dead` + alerted — never silently dropped or misrouted."""

# Endpoint labels stored on mpesa_webhook_events.endpoint (also the retry dispatch key).
ENDPOINT_B2C_RESULT = "b2c-result"
ENDPOINT_B2C_TIMEOUT = "b2c-timeout"
ENDPOINT_STK_CALLBACK = "stk-callback"
ENDPOINT_C2B_CALLBACK = "c2b-callback"


# --------------------------------------------------------------------------- #
# 1. Perimeter source-IP allowlist
# --------------------------------------------------------------------------- #
def client_ip(request) -> str | None:
    """Resolve the real client IP for a request that arrives via nginx.

    The app listens on 127.0.0.1 behind nginx, so request.client.host is always
    the loopback proxy. nginx forwards the true source in X-Forwarded-For (may be
    a comma-separated chain; the FIRST entry is the original client) or X-Real-Ip.
    Falls back to the direct peer if no proxy header is present.
    """
    if request is None:
        return None
    headers = request.headers
    xff = headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    xri = headers.get("x-real-ip")
    if xri and xri.strip():
        return xri.strip()
    client = getattr(request, "client", None)
    return getattr(client, "host", None) if client else None


def ip_allowed(ip: str | None) -> bool:
    """True if ``ip`` falls inside any configured Safaricom allowlist network.

    An empty allowlist means "no restriction configured" -> allow (fail-open on
    configuration, since enforcement is opt-in via SAFARICOM_IP_ENFORCE). An
    unparseable IP is treated as NOT allowed.
    """
    nets = settings.safaricom_ip_networks
    if not nets:
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in nets)


def check_ip_allowlist(request, endpoint: str):
    """Apply the allowlist decision for a Daraja callback.

    Modes (settings.SAFARICOM_IP_ENFORCE):
      * off     -> skip entirely.
      * log     -> WARN on a non-allowlisted IP but return normally (still process).
      * enforce -> raise HTTPException(403) BEFORE any processing.

    Only the client IP + endpoint label are logged — never the payload/secrets.
    """
    mode = (settings.SAFARICOM_IP_ENFORCE or "log").strip().lower()
    if mode == "off":
        return
    ip = client_ip(request)
    if ip_allowed(ip):
        return
    if mode == "enforce":
        logger.warning("daraja_webhook_ip_blocked endpoint=%s ip=%s mode=enforce", endpoint, ip)
        # Local import keeps this module import-light for the test suite.
        from fastapi import HTTPException
        raise HTTPException(403, "Forbidden")
    # log mode (default) — warn but continue processing so sandbox/testing works.
    logger.warning("daraja_webhook_ip_not_allowlisted endpoint=%s ip=%s mode=log "
                   "(processing anyway)", endpoint, ip)


# --------------------------------------------------------------------------- #
# 3. Multi-paybill tenant resolution
# --------------------------------------------------------------------------- #
def extract_shortcode(endpoint: str, body: dict) -> str | None:
    """Best-effort extraction of the BusinessShortCode / paybill from a payload.

    C2B confirmation carries BusinessShortCode directly. B2C/STK result bodies do
    not reliably carry it (the tenant is resolved from the stored transaction
    instead), so this returns None for those — that is expected."""
    if not isinstance(body, dict):
        return None
    for key in ("BusinessShortCode", "ShortCode", "BusinessShortcode"):
        val = body.get(key)
        if val not in (None, ""):
            return str(val)
    # C2B sometimes nests under different casing; also check the STK metadata.
    stk = (body.get("Body", {}) or {}).get("stkCallback", {}) if isinstance(body.get("Body"), dict) else {}
    for item in (stk.get("CallbackMetadata", {}) or {}).get("Item", []) or []:
        if str(item.get("Name")) in ("BusinessShortCode", "ShortCode") and item.get("Value") not in (None, ""):
            return str(item.get("Value"))
    return None


def shortcode_to_tenant(db, shortcode: str | None) -> int | None:
    """Map an incoming M-Pesa BusinessShortCode to the owning tenant_id.

    Resolution order (multi-paybill):
      1. A tenant that has saved this shortcode in its own Daraja integration
         config (TenantIntegrationConfig, integration='daraja', enabled).
      2. The platform-default shortcode (settings.DARAJA_SHORTCODE) — matched to a
         payments-enabled tenant ONLY when exactly one such tenant exists, so a
         single-DCP deployment keeps working without per-tenant config while a
         multi-tenant platform never guesses.

    Returns the tenant_id, or None when the shortcode is unknown/ambiguous (the
    caller then records an unresolved event and alerts — never silently drops)."""
    if shortcode in (None, ""):
        return None
    shortcode = str(shortcode).strip()
    from app.models import TenantIntegrationConfig, TenantModule

    rows = (db.query(TenantIntegrationConfig)
            .filter(TenantIntegrationConfig.integration == "daraja",
                    TenantIntegrationConfig.enabled == True)  # noqa: E712
            .all())
    matches = [r.tenant_id for r in rows
               if str((r.config or {}).get("shortcode") or "").strip() == shortcode]
    # De-dupe while preserving determinism.
    matches = sorted(set(matches))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Ambiguous: two tenants claim the same shortcode — do not guess.
        logger.error("daraja_shortcode_ambiguous shortcode=%s tenants=%s", shortcode, matches)
        return None

    # Fall back to the platform-default shortcode, but only when unambiguous.
    default_sc = str(getattr(settings, "DARAJA_SHORTCODE", "") or "").strip()
    if default_sc and default_sc.lower() not in ("", "placeholder") and default_sc == shortcode:
        payment_tenants = sorted(
            {row.tenant_id for row in
             db.query(TenantModule)
             .filter(TenantModule.module_key == "payments",
                     TenantModule.enabled == True)  # noqa: E712
             .all()})
        if len(payment_tenants) == 1:
            return payment_tenants[0]
    return None


def resolve_tenant_for_webhook(db, endpoint: str, body: dict) -> tuple[int | None, str | None]:
    """Resolve (tenant_id, shortcode) for a webhook, best-effort, for recording on
    the durable event. Authoritative money routing still happens inside the
    existing processors; this only labels the audit row and drives shortcode-based
    fallback. Returns (None, shortcode) when the tenant cannot be determined."""
    shortcode = extract_shortcode(endpoint, body)
    if not isinstance(body, dict):
        return None, shortcode
    from app.models import PaymentTransaction, Loan

    try:
        if endpoint in (ENDPOINT_B2C_RESULT, ENDPOINT_B2C_TIMEOUT):
            result = body.get("Result", {}) or {}
            conv = result.get("ConversationID")
            orig = result.get("OriginatorConversationID")
            txn = None
            if conv:
                txn = (db.query(PaymentTransaction)
                       .filter(PaymentTransaction.type == "b2c",
                               PaymentTransaction.mpesa_ref == str(conv)).first())
            if txn is None and orig:
                for c in (db.query(PaymentTransaction)
                          .filter(PaymentTransaction.type == "b2c",
                                  PaymentTransaction.status.in_(("processing", "timed_out"))).all()):
                    if (c.raw_payload or {}).get("result", {}).get("OriginatorConversationID") == orig:
                        txn = c
                        break
            if txn is not None:
                return txn.tenant_id, shortcode

        elif endpoint == ENDPOINT_STK_CALLBACK:
            cb = (body.get("Body", {}) or {}).get("stkCallback", {}) or {}
            checkout_id = cb.get("CheckoutRequestID")
            if checkout_id:
                txn = (db.query(PaymentTransaction)
                       .filter(PaymentTransaction.type == "stk_push",
                               PaymentTransaction.mpesa_ref == str(checkout_id)).first())
                if txn is not None:
                    return txn.tenant_id, shortcode

        elif endpoint == ENDPOINT_C2B_CALLBACK:
            bill = body.get("BillRefNumber")
            if bill:
                loan = db.query(Loan).filter(Loan.account_number == bill).first()
                if loan is not None:
                    return loan.tenant_id, shortcode
    except Exception:
        logger.exception("resolve_tenant_for_webhook lookup failed endpoint=%s", endpoint)

    # Last resort: shortcode -> tenant (multi-paybill).
    return shortcode_to_tenant(db, shortcode), shortcode


# --------------------------------------------------------------------------- #
# 2. Durable ingestion / dead-letter queue
# --------------------------------------------------------------------------- #
def record_event(db, endpoint: str, body: dict, tenant_id: int | None = None,
                 shortcode: str | None = None):
    """Persist an incoming webhook as `received` in its OWN committed transaction,
    so the delivery is durable even if the subsequent processing raises and its
    transaction is rolled back. Returns the persisted MpesaWebhookEvent."""
    from app.models import MpesaWebhookEvent
    payload = body if isinstance(body, dict) else {"_raw": str(body)}
    event = MpesaWebhookEvent(
        tenant_id=tenant_id, endpoint=endpoint, shortcode=shortcode,
        raw_payload=payload, processing_status="received", attempts=0,
        received_at=datetime.utcnow(),
    )
    db.add(event)
    db.commit()
    return event


def _backoff_seconds(attempts: int) -> int:
    """Exponential backoff: base * 2**(attempts-1), capped at 1 hour."""
    base = max(1, int(settings.WEBHOOK_RETRY_BASE_SECONDS))
    delay = base * (2 ** max(0, attempts - 1))
    return min(delay, 3600)


def mark_processed(db, event, tenant_id: int | None = None):
    """Flip an event to `processed`. Optionally record the resolved tenant."""
    event.processing_status = "processed"
    event.processed_at = datetime.utcnow()
    event.last_error = None
    event.next_retry_at = None
    if tenant_id is not None and event.tenant_id is None:
        event.tenant_id = tenant_id
    db.commit()


def mark_failed(db, event, error: str, tenant_id: int | None = None):
    """Flip an event to `failed`, increment attempts, schedule the next retry, or
    escalate to `dead` + alert once WEBHOOK_MAX_ATTEMPTS is reached. Truncates the
    error text so a huge traceback never bloats the row."""
    event.attempts = (event.attempts or 0) + 1
    event.last_error = (error or "")[:2000]
    if tenant_id is not None and event.tenant_id is None:
        event.tenant_id = tenant_id
    max_attempts = max(1, int(settings.WEBHOOK_MAX_ATTEMPTS))
    if event.attempts >= max_attempts:
        event.processing_status = "dead"
        event.next_retry_at = None
        db.commit()
        alert_dead_letter(db, event)
    else:
        event.processing_status = "failed"
        event.next_retry_at = datetime.utcnow() + timedelta(seconds=_backoff_seconds(event.attempts))
        db.commit()
        logger.warning("daraja_webhook_failed id=%s endpoint=%s attempts=%d next_retry_at=%s",
                       event.id, event.endpoint, event.attempts, event.next_retry_at)


def alert_dead_letter(db, event):
    """Emit an operational alert when a webhook event is escalated to `dead`.

    Kept deliberately lightweight (no heavy infra): a structured ERROR log line
    (scraped by the platform log pipeline / dashboards) plus the dead-count so an
    admin/alerting rule can trigger. Never logs the raw payload (PII)."""
    try:
        from app.models import MpesaWebhookEvent
        dead_count = (db.query(MpesaWebhookEvent)
                      .filter(MpesaWebhookEvent.processing_status == "dead").count())
    except Exception:
        dead_count = -1
    logger.error("ALERT daraja_webhook_dead_letter id=%s endpoint=%s tenant_id=%s "
                 "attempts=%s dead_total=%s last_error=%s",
                 event.id, event.endpoint, event.tenant_id, event.attempts,
                 dead_count, (event.last_error or "")[:200])
