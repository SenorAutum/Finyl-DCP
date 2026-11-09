"""
WP9 - running on AWS: settings from Secrets Manager, Amazon SES e-mail, any
strong secret as the field key, one migration run at a time, restore check,
background-job failure marker, no seeding on AWS start.
"""
import base64
import importlib.util
import logging
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.core import config as config_mod
from app.core import crypto
from app.core.config import Settings, settings
from app.services import mailer

BACKEND = Path(__file__).resolve().parents[1]
STRONG = "finyl-dcp-test-secret-key-0123456789abcdef"


def _load_script(name):
    spec = importlib.util.spec_from_file_location(f"wp9_{name}", BACKEND / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- settings -------------------------------------------------------------------

def test_app_settings_json_fills_only_unset_values(monkeypatch):
    monkeypatch.setenv("APP_SETTINGS_JSON",
                       '{"WP9_A": "from-json", "WP9_B": "from-json", "WP9_FLAG": true, "WP9_NONE": null}')
    monkeypatch.setenv("WP9_B", "direct")
    for k in ("WP9_A", "WP9_FLAG", "WP9_NONE"):
        monkeypatch.delenv(k, raising=False)
    config_mod._load_app_settings_json()
    try:
        assert os.environ["WP9_A"] == "from-json"
        assert os.environ["WP9_B"] == "direct"          # a direct value always wins
        assert os.environ["WP9_FLAG"] == "true"
        assert "WP9_NONE" not in os.environ
    finally:
        for k in ("WP9_A", "WP9_FLAG"):
            os.environ.pop(k, None)


@pytest.mark.parametrize("raw", ["{not json", '["a list"]'])
def test_app_settings_json_must_be_an_object(monkeypatch, raw):
    monkeypatch.setenv("APP_SETTINGS_JSON", raw)
    with pytest.raises(ValueError):
        config_mod._load_app_settings_json()


def test_database_url_built_from_parts_with_escaped_password():
    s = Settings(JWT_SECRET=STRONG, DB_HOST="db.internal", DB_PORT=5432, DB_NAME="finyl",
                 DB_USER="finyl", DB_PASSWORD="p@ss:/w?rd#1")
    assert s.DATABASE_URL == ("postgresql://finyl:p%40ss%3A%2Fw%3Frd%231@db.internal:5432/finyl"
                              "?sslmode=require")


def test_database_url_unchanged_without_db_host():
    s = Settings(JWT_SECRET=STRONG, DATABASE_URL="postgresql://a:b@c/d")
    assert s.DATABASE_URL == "postgresql://a:b@c/d"


# --- field encryption key -------------------------------------------------------

def test_real_fernet_key_is_used_unchanged():
    key = Fernet.generate_key().decode()
    assert crypto._as_fernet_key(key) == key.encode()


def test_aws_generated_secret_becomes_a_working_key(monkeypatch):
    secret = "Zq8" * 21 + "x"                           # 64 letters, like Secrets Manager
    derived = crypto._as_fernet_key(secret)
    assert len(base64.urlsafe_b64decode(derived)) == 32
    assert derived == crypto._as_fernet_key(secret)     # stable across restarts
    monkeypatch.setattr(settings, "FIELD_ENCRYPTION_KEY", secret)
    token = crypto.encrypt_pii("12345678")
    assert token != "12345678"
    assert crypto.decrypt_pii(token) == "12345678"
    assert crypto.pii_hash("12345678") == crypto.pii_hash("12345678")


# --- e-mail through Amazon SES ----------------------------------------------------

class _FakeSes:
    def __init__(self):
        self.calls = []

    def send_email(self, **kw):
        self.calls.append(kw)
        return {"MessageId": "m-1"}


def test_ses_sends_raw_message_with_attachment(monkeypatch):
    fake = _FakeSes()
    monkeypatch.setattr(settings, "MAIL_BACKEND", "ses")
    monkeypatch.setattr(settings, "MAIL_FROM", "Finyl <reports@finyl.example>")
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(mailer, "_ses_client", lambda: fake)
    assert mailer.is_configured() is True
    ok = mailer.send_email("a@x.example, b@y.example", "Loan book", "Attached.",
                           attachments=[("loan_book.csv", b"id,amount\n1,100\n", "text/csv")])
    assert ok is True
    call = fake.calls[0]
    assert call["FromEmailAddress"] == "Finyl <reports@finyl.example>"
    assert call["Destination"] == {"ToAddresses": ["a@x.example", "b@y.example"]}
    raw = call["Content"]["Raw"]["Data"]
    assert b"loan_book.csv" in raw and b"Subject: Loan book" in raw


def test_ses_failure_returns_false_and_does_not_raise(monkeypatch, caplog):
    class Boom:
        def send_email(self, **kw):
            raise RuntimeError("MessageRejected: Email address is not verified")
    monkeypatch.setattr(settings, "MAIL_BACKEND", "ses")
    monkeypatch.setattr(settings, "MAIL_FROM", "reports@finyl.example")
    monkeypatch.setattr(mailer, "_ses_client", lambda: Boom())
    with caplog.at_level(logging.ERROR):
        assert mailer.send_email("someone@client.example", "s", "b") is False
    assert "email_send_failed backend=ses" in caplog.text
    assert "someone@" not in caplog.text                 # no full address in logs


def test_mail_not_configured_without_sender_or_smtp_host(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "ses")
    monkeypatch.setattr(settings, "MAIL_FROM", "")
    monkeypatch.setattr(settings, "SMTP_FROM", "")
    assert mailer.is_configured() is False
    monkeypatch.setattr(settings, "MAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "MAIL_FROM", "x@y.example")
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    assert mailer.is_configured() is False


# --- background-job failure marker (CloudWatch alarm looks for it) ---------------

def test_failed_job_logs_marker_for_alarm(db, monkeypatch, caplog):
    from sqlalchemy.orm import sessionmaker
    from app.services import scheduler
    monkeypatch.setattr(scheduler, "SessionLocal", sessionmaker(bind=db.get_bind()))

    def broken():
        raise RuntimeError("boom")
    with caplog.at_level(logging.ERROR):
        scheduler.guarded("wp9_test_job", broken, 5)()
    assert "scheduled_job_failed job=wp9_test_job" in caplog.text


# --- startup on AWS -------------------------------------------------------------

def test_startup_does_not_seed_when_switched_off(tmp_path):
    fake = tmp_path / "uvicorn"
    fake.write_text("#!/bin/sh\necho fake-uvicorn-started\n")
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "SEED_ON_START": "false"}
    out = subprocess.run(["sh", str(BACKEND / "startup.sh")], env=env, cwd=BACKEND,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "not seeding" in out.stdout
    assert "Ensuring database is seeded" not in out.stdout
    assert "fake-uvicorn-started" in out.stdout


# --- Postgres only: one migration run at a time, and the restore check -----------

pg_only = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").startswith("postgres"),
                             reason="needs PostgreSQL")


@pytest.fixture()
def pg_schema():
    """A brand-new empty database (the migrations always build schema finyl_dcp)."""
    import psycopg2
    from sqlalchemy.engine import make_url
    base = os.environ["TEST_DATABASE_URL"]
    name = f"wp9_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(base)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    yield url, "finyl_dcp"
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()


@pg_only
def test_two_migration_runs_at_once_apply_each_file_once(pg_schema, monkeypatch):
    url, schema = pg_schema
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr(settings, "DB_SCHEMA", schema)
    monkeypatch.setattr(sys, "argv", ["run_migrations.py"])
    runner = _load_script("run_migrations")
    results = []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        results.append(runner.main())
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert results == [0, 0]

    import psycopg2
    conn = psycopg2.connect(url)
    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*), count(DISTINCT filename) FROM "{schema}".schema_migrations')
        total, distinct = cur.fetchone()
    conn.close()
    expected = len(list((BACKEND / "migrations").glob("*.sql")))
    assert total == distinct == expected

    # The restore check on this copy: schema complete, but no DCPs -> FAILED.
    proc = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "verify_restore.py")],
        env={**os.environ, "DATABASE_URL": url, "DB_SCHEMA": schema, "DB_HOST": "",
             "JWT_SECRET": STRONG, "APP_SETTINGS_JSON": ""},
        capture_output=True, text=True, timeout=120)
    assert f"migrations: {expected} recorded, {expected} in this version" in proc.stdout
    assert "no DCPs in the restored copy" in proc.stdout
    assert "RESTORE CHECK FAILED" in proc.stdout and proc.returncode == 1

    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'INSERT INTO "{schema}".tenants (name, code) VALUES (%s, %s)', ("Restore DCP", "RST"))
    conn.close()
    proc = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "verify_restore.py")],
        env={**os.environ, "DATABASE_URL": url, "DB_SCHEMA": schema, "DB_HOST": "",
             "JWT_SECRET": STRONG, "APP_SETTINGS_JSON": ""},
        capture_output=True, text=True, timeout=120)
    assert "DCPs: 1" in proc.stdout
    assert "RESTORE CHECK PASSED" in proc.stdout and proc.returncode == 0


# --- M-Pesa callback addresses Safaricom accepts, and paybill registration ------

@pytest.fixture()
def api_as(db):
    import app.main as main_mod
    from fastapi.testclient import TestClient
    from app.core.database import get_db
    from app.core.deps import get_current_user

    def make(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        if user is not None:
            main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app)
    yield make
    main_mod.app.dependency_overrides.clear()


def test_callback_addresses_avoid_words_safaricom_rejects(monkeypatch):
    from app.services import mpesa
    monkeypatch.setattr(settings, "DARAJA_CALLBACK_BASE_URL", "https://d1abc.cloudfront.net/")
    monkeypatch.setattr(settings, "MPESA_CALLBACK_TOKEN", "Tok3nAbc")
    url = mpesa.callback_url("c2b-callback")
    assert url == "https://d1abc.cloudfront.net/api/v1/payments/hooks/Tok3nAbc/c2b-callback"
    assert "mpesa" not in url.lower() and "safaricom" not in url.lower()


@pytest.mark.parametrize("prefix", ["hooks", "mpesa"])
def test_new_and_old_callback_paths_both_work(api_as, db, monkeypatch, prefix):
    from app.models import MpesaWebhookEvent
    monkeypatch.setattr(settings, "MPESA_CALLBACK_TOKEN", "Tok3nAbc")
    monkeypatch.setattr(settings, "SAFARICOM_IP_ENFORCE", "off")
    client = api_as(None)
    bad = client.post(f"/api/v1/payments/{prefix}/wrong/c2b-callback", json={})
    assert bad.status_code in (403, 404)
    r = client.post(f"/api/v1/payments/{prefix}/Tok3nAbc/c2b-callback",
                    json={"TransID": f"WP9{prefix.upper()}1", "TransAmount": "100",
                          "BillRefNumber": "NOSUCH", "BusinessShortCode": "600000",
                          "MSISDN": "254700000000"})
    assert r.status_code == 200 and r.json()["ResultCode"] == 0
    assert db.query(MpesaWebhookEvent).count() == 1


def test_register_paybill_uses_dcp_settings_and_is_audited(api_as, seed, db, monkeypatch):
    from app.core.security import hash_password
    from app.models import AuditLog, User
    from app.services import mpesa
    creds = mpesa.DarajaCreds("key", "secret", "600999", "pk", "init", "cred", "sandbox")
    seen = {}
    monkeypatch.setattr(mpesa, "resolve_creds", lambda _db, tid: seen.setdefault("tid", tid) and creds)
    monkeypatch.setattr(mpesa, "register_c2b_urls", lambda c: {
        "request": {"ShortCode": c.shortcode, "ConfirmationURL": "https://x/api/v1/payments/hooks/T/c2b-callback"},
        "response": {"ResponseCode": "0", "ResponseDescription": "Success"}})
    sa = User(email="sa9@finyl.app", hashed_password=hash_password("Passw0rd!x"), full_name="SA",
              role="super_admin", tenant_id=None)
    admin = User(email="adm9@a.dcp", hashed_password=hash_password("Passw0rd!x"), full_name="Adm",
                 role="tenant_admin", tenant_id=seed.tenant.id)
    db.add_all([sa, admin])
    db.commit()

    r = api_as(admin).post("/api/v1/integrations/daraja/register-c2b", json={"tenant_id": seed.tenant.id})
    assert r.status_code == 403                       # Finyl staff only

    r = api_as(sa).post("/api/v1/integrations/daraja/register-c2b", json={"tenant_id": seed.tenant.id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["shortcode"] == "600999" and body["detail"] == "Success"
    assert seen["tid"] == seed.tenant.id
    log = db.query(AuditLog).filter_by(action="mpesa.c2b_registered").one()
    assert log.tenant_id == seed.tenant.id

    r = api_as(sa).post("/api/v1/integrations/daraja/register-c2b", json={"tenant_id": 99999})
    assert r.status_code == 404


def test_register_paybill_refuses_dcp_without_mpesa_settings(api_as, seed, db, monkeypatch):
    from app.core.security import hash_password
    from app.models import User
    from app.services import mpesa
    monkeypatch.setattr(mpesa, "resolve_creds",
                        lambda _db, tid: mpesa.DarajaCreds("", "", "", "", "", "", "sandbox"))
    sa = User(email="sa9b@finyl.app", hashed_password=hash_password("Passw0rd!x"), full_name="SA",
              role="super_admin", tenant_id=None)
    db.add(sa)
    db.commit()
    r = api_as(sa).post("/api/v1/integrations/daraja/register-c2b", json={"tenant_id": seed.tenant.id})
    assert r.status_code == 400 and "no M-Pesa settings" in r.json()["detail"]
