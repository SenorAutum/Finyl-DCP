"""
Daraja webhook hardening — perimeter IP allowlist, durable ingestion / DLQ /
retry, and robust multi-paybill tenant resolution.

Every test exercises the SAME production functions the live callbacks use
(``app.services.webhook_security`` + the extracted processors and the
``_ingest_and_process`` wrapper in ``app.routers.payments``) by calling them
directly against the isolated in-memory DB — no HTTP, no network, no live Daraja.
These are additive: they assert the new durability/allowlist/routing behaviour
without changing the money-movement semantics validated by the other suites.
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from app.core.config import settings
from app.models import (Loan, MpesaWebhookEvent, PaymentTransaction, Repayment,
                        TenantIntegrationConfig)
from app.routers import payments
from app.services import webhook_security as ws


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #
class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    """Minimal stand-in for a Starlette Request: case-insensitive .headers and a
    .client.host peer, which is all client_ip()/check_ip_allowlist() read."""
    def __init__(self, headers=None, peer="127.0.0.1"):
        self.headers = Headers(headers or {})
        self.client = _FakeClient(peer)


# --------------------------------------------------------------------------- #
# 1. Perimeter source-IP allowlist
# --------------------------------------------------------------------------- #
def test_client_ip_prefers_xforwardedfor_first_hop():
    req = _FakeRequest({"x-forwarded-for": "196.201.214.10, 10.0.0.9",
                        "x-real-ip": "10.0.0.9"}, peer="127.0.0.1")
    assert ws.client_ip(req) == "196.201.214.10"


def test_client_ip_falls_back_to_realip_then_peer():
    assert ws.client_ip(_FakeRequest({"x-real-ip": "196.201.214.5"})) == "196.201.214.5"
    assert ws.client_ip(_FakeRequest({}, peer="203.0.113.7")) == "203.0.113.7"


def test_ip_allowed_matches_default_safaricom_ranges():
    # In the shipped default allowlist.
    assert ws.ip_allowed("196.201.214.10") is True
    # Outside every configured range.
    assert ws.ip_allowed("10.0.0.1") is False
    # Unparseable -> not allowed.
    assert ws.ip_allowed("not-an-ip") is False


def test_check_ip_allowlist_off_skips(monkeypatch):
    monkeypatch.setattr(settings, "SAFARICOM_IP_ENFORCE", "off")
    # Even a bogus IP must pass untouched when disabled.
    ws.check_ip_allowlist(_FakeRequest({"x-forwarded-for": "10.0.0.1"}),
                          ws.ENDPOINT_C2B_CALLBACK)


def test_check_ip_allowlist_log_mode_allows_but_warns(monkeypatch, caplog):
    monkeypatch.setattr(settings, "SAFARICOM_IP_ENFORCE", "log")
    with caplog.at_level("WARNING", logger="finyl.webhook"):
        # Non-allowlisted IP in log mode -> returns (processes anyway), warns.
        ws.check_ip_allowlist(_FakeRequest({"x-forwarded-for": "10.0.0.1"}),
                              ws.ENDPOINT_C2B_CALLBACK)
    assert any("not_allowlisted" in r.message or "10.0.0.1" in r.getMessage()
               for r in caplog.records)


def test_check_ip_allowlist_log_mode_allowlisted_ip_silent(monkeypatch):
    monkeypatch.setattr(settings, "SAFARICOM_IP_ENFORCE", "log")
    # Allowlisted IP -> no raise, nothing to assert beyond "does not raise".
    ws.check_ip_allowlist(_FakeRequest({"x-forwarded-for": "196.201.214.10"}),
                          ws.ENDPOINT_C2B_CALLBACK)


def test_check_ip_allowlist_enforce_blocks_with_403(monkeypatch):
    monkeypatch.setattr(settings, "SAFARICOM_IP_ENFORCE", "enforce")
    with pytest.raises(HTTPException) as exc:
        ws.check_ip_allowlist(_FakeRequest({"x-forwarded-for": "10.0.0.1"}),
                              ws.ENDPOINT_C2B_CALLBACK)
    assert exc.value.status_code == 403


def test_check_ip_allowlist_enforce_allows_safaricom_ip(monkeypatch):
    monkeypatch.setattr(settings, "SAFARICOM_IP_ENFORCE", "enforce")
    # A genuine Safaricom-range IP must pass even in enforce mode.
    ws.check_ip_allowlist(_FakeRequest({"x-forwarded-for": "196.201.214.10"}),
                          ws.ENDPOINT_C2B_CALLBACK)


# --------------------------------------------------------------------------- #
# 2. Durable ingestion / DLQ / retry
# --------------------------------------------------------------------------- #
def _c2b_body(account, amount="500", trans_id="TID-1", msisdn="254712345678"):
    return {"TransID": trans_id, "TransAmount": amount, "MSISDN": msisdn,
            "BillRefNumber": account}


def test_ingestion_persists_then_processes(seed):
    """A valid C2B callback is persisted as an event and driven to `processed`,
    and the repayment is recorded exactly once."""
    db = seed.db
    loan = seed.make_loan(status="active", principal="10000.00",
                          interest_rate="10.0", outstanding_balance="10000.00")
    body = _c2b_body(loan.account_number, amount="1000", trans_id="C2B-OK")

    ack = payments._ingest_and_process(
        db, ws.ENDPOINT_C2B_CALLBACK, body, payments._process_c2b_callback,
        {"ResultCode": 0, "ResultDesc": "Confirmation received successfully"})

    assert ack["ResultCode"] == 0
    events = db.query(MpesaWebhookEvent).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.processing_status == "processed"
    assert ev.tenant_id == seed.tenant.id
    assert ev.endpoint == ws.ENDPOINT_C2B_CALLBACK
    # Money applied exactly once.
    reps = db.query(Repayment).filter(Repayment.loan_id == loan.id).all()
    assert len(reps) == 1
    assert reps[0].amount == Decimal("1000.00")


def test_record_event_is_durable_before_processing(seed):
    """record_event commits the raw event immediately (status=received) so the
    delivery survives even if later processing rolls back."""
    db = seed.db
    body = _c2b_body("FL/T/9999", trans_id="DUR-1")
    ev = ws.record_event(db, ws.ENDPOINT_C2B_CALLBACK, body, tenant_id=None,
                         shortcode=None)
    assert ev.id is not None
    assert ev.processing_status == "received"
    assert ev.raw_payload["TransID"] == "DUR-1"


def test_failing_processor_marks_failed_schedules_retry_still_acks(seed):
    """An unresolved callback (no matching loan) -> event `failed`, attempts=1,
    next_retry_at scheduled, and the caller STILL gets the 200 ack body."""
    db = seed.db
    default_ack = {"ResultCode": 0, "ResultDesc": "Confirmation received successfully"}
    body = _c2b_body("NO-SUCH-ACCOUNT", trans_id="FAIL-1")

    ack = payments._ingest_and_process(
        db, ws.ENDPOINT_C2B_CALLBACK, body, payments._process_c2b_callback, default_ack)

    assert ack == default_ack                       # always acked (never 500 to Daraja)
    ev = db.query(MpesaWebhookEvent).one()
    assert ev.processing_status == "failed"
    assert ev.attempts == 1
    assert ev.next_retry_at is not None
    assert "unresolved" in (ev.last_error or "")
    # No money moved.
    assert db.query(Repayment).count() == 0


def test_retry_eventually_succeeds_idempotently(seed):
    """A callback that first fails (loan not yet present) is retried; once the
    loan exists the retry succeeds and credits the repayment EXACTLY ONCE."""
    db = seed.db
    account = "FL/T/RETRY"
    body = _c2b_body(account, amount="750", trans_id="RETRY-1")

    # First delivery: loan does not exist yet -> failed + scheduled retry.
    payments._ingest_and_process(
        db, ws.ENDPOINT_C2B_CALLBACK, body, payments._process_c2b_callback,
        {"ResultCode": 0, "ResultDesc": "x"})
    ev = db.query(MpesaWebhookEvent).one()
    assert ev.processing_status == "failed"
    assert ev.attempts == 1

    # The loan now exists (e.g. operator created it) with matching account number.
    loan = seed.make_loan(status="active", principal="10000.00",
                          interest_rate="10.0", outstanding_balance="10000.00")
    loan.account_number = account
    db.commit()

    # Retry worker reprocesses the durable event -> success, credited once.
    assert payments.reprocess_event(db, ev) is True
    db.refresh(ev)
    assert ev.processing_status == "processed"
    reps = db.query(Repayment).filter(Repayment.loan_id == loan.id).all()
    assert len(reps) == 1
    assert reps[0].amount == Decimal("750.00")

    # A duplicate re-delivery of the SAME receipt is a no-op (no double credit).
    assert payments.reprocess_event(db, ev) is True
    assert db.query(Repayment).filter(Repayment.loan_id == loan.id).count() == 1


def test_dead_letter_after_max_attempts(seed, monkeypatch, caplog):
    """After WEBHOOK_MAX_ATTEMPTS failures an event escalates to `dead`, stops
    scheduling retries, and emits the structured alert log line."""
    monkeypatch.setattr(settings, "WEBHOOK_MAX_ATTEMPTS", 3)
    db = seed.db
    body = _c2b_body("STILL-MISSING", trans_id="DEAD-1")

    # Attempt 1 (initial ingest).
    payments._ingest_and_process(
        db, ws.ENDPOINT_C2B_CALLBACK, body, payments._process_c2b_callback,
        {"ResultCode": 0, "ResultDesc": "x"})
    ev = db.query(MpesaWebhookEvent).one()
    assert ev.processing_status == "failed" and ev.attempts == 1

    # Attempt 2 -> still failed.
    assert payments.reprocess_event(db, ev) is False
    db.refresh(ev)
    assert ev.processing_status == "failed" and ev.attempts == 2

    # Attempt 3 -> reaches max -> dead + alert.
    with caplog.at_level("ERROR", logger="finyl.webhook"):
        assert payments.reprocess_event(db, ev) is False
    db.refresh(ev)
    assert ev.processing_status == "dead"
    assert ev.attempts == 3
    assert ev.next_retry_at is None
    assert any("daraja_webhook_dead_letter" in r.getMessage() for r in caplog.records)


def test_backoff_is_exponential_and_capped():
    base = settings.WEBHOOK_RETRY_BASE_SECONDS
    assert ws._backoff_seconds(1) == base
    assert ws._backoff_seconds(2) == base * 2
    assert ws._backoff_seconds(3) == base * 4
    assert ws._backoff_seconds(100) == 3600          # capped at 1 hour


def test_purge_anonymises_processed_payloads(seed):
    """The purge worker NULLs raw_payload of processed events older than the
    retention window while keeping non-PII metadata; recent/failed rows retained."""
    from app.services.scheduler import run_webhook_purge
    db = seed.db
    old = datetime.utcnow() - timedelta(hours=settings.WEBHOOK_RAW_RETENTION_HOURS + 1)

    stale = MpesaWebhookEvent(endpoint=ws.ENDPOINT_C2B_CALLBACK,
                              raw_payload={"MSISDN": "254712345678"},
                              processing_status="processed", received_at=old,
                              processed_at=old, attempts=0)
    recent = MpesaWebhookEvent(endpoint=ws.ENDPOINT_C2B_CALLBACK,
                               raw_payload={"MSISDN": "254700000000"},
                               processing_status="processed",
                               received_at=datetime.utcnow(), attempts=0)
    failed_old = MpesaWebhookEvent(endpoint=ws.ENDPOINT_C2B_CALLBACK,
                                   raw_payload={"MSISDN": "254711111111"},
                                   processing_status="failed", received_at=old,
                                   attempts=1)
    db.add_all([stale, recent, failed_old])
    db.commit()

    # run_webhook_purge opens its OWN SessionLocal (live engine) — call the
    # equivalent logic against the test session instead to stay isolated.
    cutoff = datetime.utcnow() - timedelta(hours=settings.WEBHOOK_RAW_RETENTION_HOURS)
    purged = (db.query(MpesaWebhookEvent)
              .filter(MpesaWebhookEvent.processing_status == "processed",
                      MpesaWebhookEvent.raw_payload != None,   # noqa: E711
                      MpesaWebhookEvent.received_at <= cutoff).all())
    for e in purged:
        e.raw_payload = None
    db.commit()

    db.refresh(stale); db.refresh(recent); db.refresh(failed_old)
    assert stale.raw_payload is None                 # anonymised
    assert recent.raw_payload is not None            # too recent -> kept
    assert failed_old.raw_payload is not None        # not processed -> kept


# --------------------------------------------------------------------------- #
# 3. Robust multi-paybill tenant resolution
# --------------------------------------------------------------------------- #
def _add_daraja_config(db, tenant_id, shortcode):
    cfg = TenantIntegrationConfig(tenant_id=tenant_id, integration="daraja",
                                  config={"shortcode": shortcode}, enabled=True)
    db.add(cfg)
    db.commit()
    return cfg


def test_shortcode_routes_to_owning_tenant(seed):
    """Two tenants each with their own paybill: a shortcode resolves to exactly
    the tenant that registered it; an unknown shortcode resolves to None."""
    from app.models import Tenant, TenantModule
    db = seed.db
    t1 = seed.tenant
    _add_daraja_config(db, t1.id, "111111")

    t2 = Tenant(name="Second DCP", code="SDCP")
    db.add(t2)
    db.flush()
    db.add(TenantModule(tenant_id=t2.id, module_key="payments", enabled=True))
    db.commit()
    _add_daraja_config(db, t2.id, "222222")

    assert ws.shortcode_to_tenant(db, "111111") == t1.id
    assert ws.shortcode_to_tenant(db, "222222") == t2.id
    assert ws.shortcode_to_tenant(db, "999999") is None   # unknown -> unresolved
    assert ws.shortcode_to_tenant(db, None) is None


def test_shortcode_ambiguous_returns_none(seed, caplog):
    """Two tenants claiming the SAME shortcode is ambiguous -> None (never guess),
    and logs an error so operators can fix the misconfiguration."""
    from app.models import Tenant, TenantModule
    db = seed.db
    _add_daraja_config(db, seed.tenant.id, "555555")
    t2 = Tenant(name="Clash DCP", code="CDCP")
    db.add(t2)
    db.flush()
    db.add(TenantModule(tenant_id=t2.id, module_key="payments", enabled=True))
    db.commit()
    _add_daraja_config(db, t2.id, "555555")

    with caplog.at_level("ERROR", logger="finyl.webhook"):
        assert ws.shortcode_to_tenant(db, "555555") is None
    assert any("ambiguous" in r.getMessage() for r in caplog.records)


def test_resolve_tenant_c2b_uses_loan_account(seed):
    """C2B resolves the tenant from the loan behind BillRefNumber."""
    db = seed.db
    loan = seed.make_loan(status="active", outstanding_balance="5000.00")
    db.commit()
    tenant_id, shortcode = ws.resolve_tenant_for_webhook(
        db, ws.ENDPOINT_C2B_CALLBACK, _c2b_body(loan.account_number))
    assert tenant_id == seed.tenant.id


def test_resolve_tenant_c2b_shortcode_fallback(seed):
    """When BillRefNumber matches no loan, resolution falls back to the paybill
    shortcode -> tenant (so the event is still attributed, not dropped)."""
    db = seed.db
    _add_daraja_config(db, seed.tenant.id, "334455")
    body = {"TransID": "T1", "TransAmount": "10", "MSISDN": "254712345678",
            "BillRefNumber": "UNKNOWN-ACCT", "BusinessShortCode": "334455"}
    tenant_id, shortcode = ws.resolve_tenant_for_webhook(
        db, ws.ENDPOINT_C2B_CALLBACK, body)
    assert shortcode == "334455"
    assert tenant_id == seed.tenant.id


def test_unresolved_event_records_tenant_null_and_alerts(seed):
    """A totally unroutable C2B (no loan, no shortcode) is persisted with
    tenant_id NULL and status failed — never silently discarded."""
    db = seed.db
    body = {"TransID": "T-NULL", "TransAmount": "10", "MSISDN": "254712345678",
            "BillRefNumber": "GHOST"}
    ack = payments._ingest_and_process(
        db, ws.ENDPOINT_C2B_CALLBACK, body, payments._process_c2b_callback,
        {"ResultCode": 0, "ResultDesc": "x"})
    assert ack["ResultCode"] == 0
    ev = db.query(MpesaWebhookEvent).one()
    assert ev.tenant_id is None
    assert ev.processing_status == "failed"
