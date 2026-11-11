"""
WP12 - every way a client can pay: the full list of payment types, M-Pesa
Ratiba standing orders for real, recovering paybill/till payments whose
notification never arrived (Pull Transactions and the M-Pesa portal
statement), and instant payment notifications from banks.
"""
import hashlib
import hmac
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import (ApprovalThreshold, AuditLog, MpesaRatibaConsent, PendingApproval,
                        Repayment, SuspenseEntry, TenantIntegrationConfig, User)
from app.routers import payments
from app.services import mpesa
from app.services import webhook_security as ws


@pytest.fixture()
def w(seed, db, monkeypatch):
    import app.main as main_mod

    def mk(role, email):
        u = User(email=email, hashed_password="x", full_name=email.split("@")[0], role=role,
                 tenant_id=seed.tenant.id)
        db.add(u)
        db.flush()
        return u

    loan = seed.make_loan(status="active", outstanding_balance="11000.00",
                          due_date=date.today() + timedelta(days=60))
    db.add(TenantIntegrationConfig(tenant_id=seed.tenant.id, integration="daraja",
                                   config={"shortcode": "600111", "till_numbers": ["5123456"]},
                                   secrets={}, enabled=True))
    db.add(ApprovalThreshold(tenant_id=seed.tenant.id, scope_type="role", scope_key="all",
                             threshold_type="manual_payment", amount=100000))
    u = SimpleNamespace(admin=mk("system_admin", "admin12@a.dcp"), recon=seed.recon,
                        officer=mk("branch_manager", "bm12@a.dcp"),
                        sa=User(email="sa12@finyl.app", hashed_password="x", full_name="SA",
                                role="super_admin"))
    db.add(u.sa)
    db.commit()
    monkeypatch.setattr(settings, "DARAJA_CALLBACK_BASE_URL", "https://finyl.example")
    monkeypatch.setattr(settings, "MPESA_CALLBACK_TOKEN", "Tok3nAbc")
    monkeypatch.setattr(settings, "SAFARICOM_IP_ENFORCE", "off")

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        if user is None:
            main_mod.app.dependency_overrides.pop(get_current_user, None)
        else:
            main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app)

    yield SimpleNamespace(seed=seed, db=db, A=seed.tenant, u=u, api=api, loan=loan,
                          H={"X-Tenant-Id": str(seed.tenant.id)})
    main_mod.app.dependency_overrides.clear()


CREDS = mpesa.DarajaCreds("key", "secret", "600111", "pk", "init", "cred", "sandbox")


# =========================================================================== #
# Payment types
# =========================================================================== #
def test_every_payment_type_can_be_recorded(w):
    r = w.api(w.u.recon).get("/api/v1/payments/channels", headers=w.H)
    keys = {c["key"] for c in r.json()["channels"]}
    assert {"mpesa_pochi", "mpesa_send", "bank_paybill", "bank_eft", "bank_rtgs", "pesalink",
            "cheque", "cash"} <= keys

    def manual(**kw):
        body = {"amount": 500, "paid_at": (datetime.utcnow() - timedelta(hours=1)).isoformat(),
                "note": "Statement line", "loan_id": w.loan.id, **kw}
        return w.api(w.u.recon).post("/api/v1/payments/manual", json=body, headers=w.H)

    assert manual(channel="pesalink", reference="PL123456").status_code == 200
    assert w.db.query(Repayment).filter_by(mpesa_ref="PL123456").one().method == "manual_pesalink"
    assert manual(channel="cheque").status_code == 422           # cheque number needed
    assert manual(channel="cash").status_code == 200             # receipt optional
    assert manual(channel="carrier_pigeon", reference="X1").status_code == 422


# =========================================================================== #
# M-Pesa Ratiba
# =========================================================================== #
def _consent(w, **kw):
    body = {"loan_id": w.loan.id, "phone": "0712345678", "deduction_amount": 1000,
            "frequency": "3", **kw}
    return w.api(w.u.officer).post("/api/v1/collections/ratiba/consent", json=body, headers=w.H)


def test_ratiba_consent_is_checked(w):
    assert _consent(w, deduction_amount=999.5).status_code == 422      # whole shillings
    assert _consent(w, frequency="9").status_code == 422
    assert _consent(w, phone="12345").status_code == 422
    r = _consent(w)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["frequency_label"] == "Weekly" and d["end_date"] == w.loan.due_date.isoformat()


def test_ratiba_goes_to_safaricom_and_the_callback_activates_it(w, monkeypatch):
    from app.services import ratiba
    sent = {}
    monkeypatch.setattr(mpesa, "resolve_creds", lambda db, tid: CREDS)

    def fake(creds, payload):
        sent.update(payload)
        return {"ResponseHeader": {"responseRefID": "REF-77", "responseCode": "200"},
                "ResponseBody": {"responseDescription": "Request accepted for processing"}}
    monkeypatch.setattr(ratiba, "_call_ratiba_api", fake)
    _consent(w)
    r = w.api(w.u.officer).post("/api/v1/collections/ratiba/initiate", json={"loan_id": w.loan.id},
                                headers=w.H)
    assert r.status_code == 200 and r.json()["status"] == "awaiting_customer"
    assert sent["AccountReference"] == w.seed.borrower.customer_number
    assert sent["Frequency"] == "3" and sent["Amount"] == "1000" and sent["PartyA"] == "254712345678"
    assert sent["TransactionType"] == "Standing Order Customer Pay Bill"
    assert sent["CallBackURL"].endswith("/hooks/Tok3nAbc/ratiba-callback")
    # Sending twice is refused.
    assert w.api(w.u.officer).post("/api/v1/collections/ratiba/initiate", json={"loan_id": w.loan.id},
                                   headers=w.H).status_code == 400
    cb = {"responseHeader": {"responseRefID": "REF-77", "responseCode": "0",
                             "responseDescription": "The service request is processed successfully"},
          "responseBody": {"responseData": [{"name": "TransactionID", "value": "SC8F2IQMH5"},
                                            {"name": "Status", "value": "OKAY"}]}}
    r = w.api(None).post("/api/v1/payments/hooks/Tok3nAbc/ratiba-callback", json=cb)
    assert r.status_code == 200
    row = w.db.query(MpesaRatibaConsent).one()
    w.db.refresh(row)
    assert row.ratiba_status == "active" and row.ratiba_ref == "SC8F2IQMH5"


def test_ratiba_without_keys_is_simulated_only_where_allowed(w, monkeypatch):
    _consent(w)
    r = w.api(w.u.officer).post("/api/v1/collections/ratiba/initiate", json={"loan_id": w.loan.id},
                                headers=w.H)
    assert r.json()["status"] == "simulated"
    w.db.query(MpesaRatibaConsent).delete()
    w.db.commit()
    monkeypatch.setattr(settings, "DARAJA_ENVIRONMENT", "production")
    _consent(w)
    r = w.api(w.u.officer).post("/api/v1/collections/ratiba/initiate", json={"loan_id": w.loan.id},
                                headers=w.H)
    assert r.status_code == 400 and "not set up" in r.json()["detail"]


# =========================================================================== #
# Missed paybill / till payments
# =========================================================================== #
def test_pull_transactions_records_only_what_is_missing(w, monkeypatch):
    from app.services import mpesa_pull
    monkeypatch.setattr(mpesa, "resolve_creds", lambda db, tid: CREDS)
    monkeypatch.setattr(mpesa, "register_pull", lambda c, sc, nom: {
        "ResponseStatus": "1000", "ResponseDescription": "Shortcode Registered Successfully"})
    r = w.api(w.u.sa).post("/api/v1/integrations/daraja/register-pull",
                           json={"tenant_id": w.A.id, "nominated_number": "0722000000"})
    assert r.status_code == 200 and [x["number"] for x in r.json()["results"]] == ["600111", "5123456"]
    # One payment arrived normally; the other's notification was lost.
    payments._process_c2b_callback(w.db, {
        "TransID": "QAA0000001", "TransTime": "20261110090000", "TransAmount": "500",
        "BusinessShortCode": "600111", "BillRefNumber": w.loan.account_number,
        "MSISDN": "254712345678", "FirstName": "JANE"})
    pages = {("600111", 0): {"ResponseCode": "1000", "Response": [[
        {"transactionId": "QAA0000001", "trxDate": "2026-11-10T09:00:00Z", "msisdn": "2547****678",
         "transactiontype": "c2b-pay-bill-debit", "billreference": w.loan.account_number, "amount": "500"},
        {"transactionId": "QAA0000002", "trxDate": "2026-11-10T09:30:00Z", "msisdn": "2547****678",
         "transactiontype": "c2b-pay-bill-debit", "billreference": w.loan.account_number, "amount": "700"},
    ]]}}
    monkeypatch.setattr(mpesa, "query_pull",
                        lambda c, sc, s, e, off: pages.get((sc, off), {"ResponseCode": "1001"}))
    res = mpesa_pull.pull_for_tenant(w.db, w.A.id, hours=24)
    assert (res["found"], res["recovered"]) == (2, 1)
    rep = w.db.query(Repayment).filter_by(mpesa_ref="QAA0000002").one()
    assert rep.loan_id == w.loan.id
    assert mpesa_pull.pull_for_tenant(w.db, w.A.id, hours=24)["recovered"] == 0
    status = w.api(w.u.sa).get(f"/api/v1/integrations/daraja/pull-status?tenant_id={w.A.id}").json()
    assert status["registered"] == ["5123456", "600111"] and status["last_run"]["found"] == 2


def test_mpesa_portal_statement_catches_missed_payments(w):
    cust = w.seed.borrower.customer_number
    payments._process_c2b_callback(w.db, {
        "TransID": "QBB0000001", "TransTime": "20261110090000", "TransAmount": "400",
        "BusinessShortCode": "600111", "BillRefNumber": cust, "MSISDN": "254712345678"})
    head = ("Receipt No.,Completion Time,Initiation Time,Details,Transaction Status,Paid In,"
            "Withdrawn,Balance,Balance Confirmed,Reason Type,Other Party Info,Linked Transaction ID,A/C No.")
    rows = [
        "Organisation Name:,MULAR CREDIT,,,,,,,,,,,",
        head,
        f"QBB0000001,10-11-2026 09:00:00,10-11-2026 09:00:00,Pay Bill from 2547****678 - JANE,Completed,400.00,,1400.00,true,Pay Bill,2547****678 - JANE WANJIKU,,{cust}",
        f"QBB0000002,10-11-2026 10:00:00,10-11-2026 10:00:00,Pay Bill from 2547****678 - JANE,Completed,600.00,,2000.00,true,Pay Bill,2547****678 - JANE WANJIKU,,{cust}",
        "QBB0000003,10-11-2026 11:00:00,10-11-2026 11:00:00,Pay Bill Online,Completed,250.00,,2250.00,true,Pay Bill,25471****678 - JANE WANJIKU,,WRONGACC",
        "QBB0000004,10-11-2026 12:00:00,10-11-2026 12:00:00,Pay Bill Online,Failed,999.00,,2250.00,true,Pay Bill,2547****678 - JANE,,X",
        "QBB0000005,10-11-2026 13:00:00,10-11-2026 13:00:00,Business Charge,Completed,,50.00,2200.00,true,Charge,,,",
    ]
    data = "\n".join(rows).encode()
    r = w.api(w.u.recon).post("/api/v1/bank-import/preview", headers=w.H,
                              files={"file": ("ORG_600111_Statement.csv", data)})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["layout"]["kind"] == "mpesa" and d["layout"]["account"] == "A/C No."
    by = {ln["reference"]: ln for ln in d["lines"] if ln["reference"]}
    assert by["QBB0000001"]["status"] == "duplicate"                   # already recorded
    assert by["QBB0000002"]["status"] == "matched" and by["QBB0000002"]["matched_by"] == "customer_number"
    assert by["QBB0000003"]["status"] == "review" and by["QBB0000003"]["suggested_loan_id"] == w.loan.id
    assert sum(1 for ln in d["lines"] if ln["status"] == "skipped") == 2  # failed + a charge
    r = w.api(w.u.recon).post("/api/v1/bank-import/import", headers=w.H,
                              files={"file": ("ORG_600111_Statement.csv", data)})
    assert r.status_code == 200 and (r.json()["applied"], r.json()["suspense"]) == (1, 1)
    rep = w.db.query(Repayment).filter_by(mpesa_ref="QBB0000002").one()
    assert rep.method == "manual_mpesa_paybill"
    e = w.db.query(SuspenseEntry).filter_by(mpesa_ref="QBB0000003").one()
    assert e.source == "mpesa_stmt" and e.payer_name == "JANE WANJIKU" and e.bill_ref == "WRONGACC"


# =========================================================================== #
# Bank notifications
# =========================================================================== #
SECRET = "a-very-long-shared-secret-1234"
FIELDS = {"reference": "transaction.reference", "amount": "transaction.amount",
          "date": "transaction.date", "account": "transaction.billNumber",
          "narrative": "transaction.narration", "payer_name": "payer.name",
          "direction": "transaction.type"}


def _bank(w, **kw):
    body = {"name": "Equity collections", "auth_mode": "hmac_sha256", "secret": SECRET,
            "field_map": FIELDS, **kw}
    return w.api(w.u.admin).post("/api/v1/settings/bank-notifications", json=body, headers=w.H)


def _notice(ref="EQ123", amount="1500.00", account=None, kind="CR", narration="DEPOSIT"):
    return {"transaction": {"reference": ref, "amount": amount, "date": "2026-11-10T10:15:00+03:00",
                            "billNumber": account, "narration": narration, "type": kind},
            "payer": {"name": "JANE WANJIKU"}}


def _send(w, url, payload, secret=SECRET, header="X-Signature"):
    raw = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    path = url.split("https://finyl.example", 1)[1]
    return w.api(None).post(path, content=raw, headers={header: sig, "Content-Type": "application/json"})


def test_bank_notification_set_up_is_checked(w):
    assert _bank(w, secret="short").status_code == 422
    assert _bank(w, field_map={"amount": "a"}).status_code == 422          # no reference
    assert _bank(w, allowed_ips="not-an-ip").status_code == 422
    r = _bank(w)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["url"].startswith("https://finyl.example/api/v1/payments/hooks/bank/") and b["secret_set"]
    listed = w.api(w.u.admin).get("/api/v1/settings/bank-notifications", headers=w.H).json()["banks"]
    assert "secret" not in json.dumps(listed).lower().replace("secret_set", "")


def test_genuine_bank_notification_is_applied_once(w):
    b = _bank(w).json()
    before = w.loan.outstanding_balance
    r = _send(w, b["url"], _notice(account=w.loan.account_number))
    assert r.status_code == 200, r.text
    rep = w.db.query(Repayment).filter_by(mpesa_ref="EQ123").one()
    assert rep.loan_id == w.loan.id and rep.method == "bank_notice"
    assert rep.payment_date == datetime(2026, 11, 10, 7, 15)
    w.db.refresh(w.loan)
    assert w.loan.outstanding_balance < before
    assert _send(w, b["url"], _notice(account=w.loan.account_number)).status_code == 200
    assert w.db.query(Repayment).filter_by(mpesa_ref="EQ123").count() == 1     # not twice
    # Unmatched -> suspense; money out -> ignored.
    _send(w, b["url"], _notice(ref="EQ124", account="NOPE"))
    assert w.db.query(SuspenseEntry).filter_by(mpesa_ref="EQ124").one().source == "bank_ipn"
    _send(w, b["url"], _notice(ref="EQ125", account=w.loan.account_number, kind="DR"))
    assert w.db.query(Repayment).filter_by(mpesa_ref="EQ125").count() == 0


def test_fake_bank_notifications_are_refused(w):
    b = _bank(w).json()
    assert _send(w, b["url"], _notice(), secret="wrong-secret-wrong-secret").status_code == 401
    assert _send(w, b["url"].replace("/bank/", "/bank/x"), _notice()).status_code == 404
    raw = json.dumps(_notice()).encode()
    path = b["url"].split("https://finyl.example", 1)[1]
    assert w.api(None).post(path, content=raw).status_code == 401          # no signature
    # Shared-key mode and a sender-address limit.
    k = w.api(w.u.admin).put(f"/api/v1/settings/bank-notifications/{b['id']}", headers=w.H,
                             json={"name": "Equity", "auth_mode": "shared_key", "auth_header": "X-Api-Key",
                                   "field_map": FIELDS}).json()
    assert w.api(None).post(path, content=raw, headers={"X-Api-Key": SECRET}).status_code == 200
    w.api(w.u.admin).put(f"/api/v1/settings/bank-notifications/{b['id']}", headers=w.H,
                         json={"name": "Equity", "auth_mode": "shared_key", "auth_header": "X-Api-Key",
                               "field_map": FIELDS, "allowed_ips": "196.201.214.0/24"})
    assert w.api(None).post(path, content=json.dumps(_notice(ref="EQ9")).encode(),
                            headers={"X-Api-Key": SECRET}).status_code == 401
    assert k["auth_mode"] == "shared_key"


def test_sample_notification_dry_run_records_nothing(w):
    b = _bank(w).json()
    r = w.api(w.u.admin).post(f"/api/v1/settings/bank-notifications/{b['id']}/test", headers=w.H,
                              json={"sample": _notice(ref="EQ777", account=w.seed.borrower.customer_number)})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["reference"] == "EQ777" and d["amount"] == 1500.0 and "applied to loan" in d["result"]
    assert w.db.query(Repayment).filter_by(mpesa_ref="EQ777").count() == 0
    assert w.db.query(AuditLog).filter_by(action="config.bank_notifications.save").count() == 1
