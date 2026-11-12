"""
WP13 - the Finyl command centre: failure alerts and who is told, every DCP's
dashboard (book, disbursed vs collected, SMS and other paid services, health),
Finyl's own billing (onboarding KES 50,000, 1% of the loan book with a KES
30,000 minimum, KES 20,000 per new branch, SMS), Finyl's loans to DCPs repaid
through Finyl's paybill, the sales pipeline, and Finyl staff/partners who see
only the switches and DCPs the super admin gives them.
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import (AuditLog, Branch, PlatformAlert, PlatformAlertRecipient,
                        PlatformTenantDaily, PlatformUnmatchedPayment, PlatformUserAccess, Region,
                        Repayment, SmsLog, Tenant, User)
from app.routers import payments
from app.services import platform_alerts, platform_billing, platform_lending, platform_metrics
from app.services import webhook_security as ws


@pytest.fixture()
def w(seed, db, monkeypatch):
    import app.main as main_mod

    B = Tenant(name="Beta Credit", code="BETA")
    db.add(B)
    db.flush()
    sa = User(email="sa13@finyl.app", hashed_password="x", full_name="Finyl SA", role="super_admin")
    dcp_admin = User(email="admin13@a.dcp", hashed_password="x", full_name="DCP Admin",
                     role="tenant_admin", tenant_id=seed.tenant.id)
    db.add_all([sa, dcp_admin])
    db.commit()
    monkeypatch.setattr(settings, "FINYL_PAYBILL", "4111222")
    monkeypatch.setattr(settings, "SAFARICOM_IP_ENFORCE", "off")

    def api(user, raise_server_exceptions=True):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        if user is None:
            main_mod.app.dependency_overrides.pop(get_current_user, None)
        else:
            main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app, raise_server_exceptions=raise_server_exceptions)

    def make_platform_user(email, preset="director", tenant_ids=(), all_tenants=False, perms=None):
        body = {"email": email, "full_name": email.split("@")[0], "password": "Temp-Pass-123",
                "preset": preset, "tenant_ids": list(tenant_ids), "all_tenants": all_tenants}
        if perms is not None:
            body["permissions"] = perms
        r = api(sa).post("/api/v1/platform/users", json=body)
        assert r.status_code == 200, r.text
        return db.get(User, r.json()["id"])

    yield SimpleNamespace(seed=seed, db=db, A=seed.tenant, B=B, sa=sa, dcp_admin=dcp_admin, api=api,
                          mk=make_platform_user)
    main_mod.app.dependency_overrides.clear()


def _c2b(shortcode, account, trans_id, amount):
    return {"TransactionType": "Pay Bill", "TransID": trans_id, "TransTime": "20261102101500",
            "TransAmount": str(amount), "BusinessShortCode": shortcode, "BillRefNumber": account,
            "MSISDN": "254712000111", "FirstName": "ALPHA", "LastName": "LENDERS"}


def _ingest(db, body):
    return payments._ingest_and_process(
        db, ws.ENDPOINT_C2B_CALLBACK, body, payments._process_c2b_callback,
        {"ResultCode": 0, "ResultDesc": "Confirmation received successfully"})


# =========================================================================== #
# Who sees what
# =========================================================================== #
def test_dcp_staff_cannot_open_the_command_centre(w):
    r = w.api(w.dcp_admin).get("/api/v1/platform/overview")
    assert r.status_code == 403


def test_partner_sees_only_the_dcps_assigned_and_reassigning_is_free(w):
    d = w.mk("partner@finyl.app", preset="director", tenant_ids=[w.A.id])
    assert d.role == "platform_user" and d.tenant_id is None and d.force_password_reset

    r = w.api(d).get("/api/v1/platform/overview")
    assert r.status_code == 200
    assert [t["id"] for t in r.json()["tenants"]] == [w.A.id]
    assert w.api(d).get(f"/api/v1/platform/tenants/{w.B.id}").status_code == 404
    assert w.api(d).get(f"/api/v1/platform/tenants/{w.A.id}").status_code == 200
    # A director views; they do not run billing or manage people.
    assert w.api(d).put("/api/v1/platform/pricing", json={"branch_fee": 1}).status_code == 403
    assert w.api(d).get("/api/v1/platform/users").status_code == 403

    # The super admin moves them to Beta only.
    r = w.api(w.sa).put(f"/api/v1/platform/users/{d.id}",
                        json={"preset": "director", "tenant_ids": [w.B.id], "active": True})
    assert r.status_code == 200 and r.json()["tenants"] == ["Beta Credit"]
    r = w.api(d).get("/api/v1/platform/overview")
    assert [t["id"] for t in r.json()["tenants"]] == [w.B.id]
    assert w.api(d).get(f"/api/v1/platform/tenants/{w.A.id}").status_code == 404

    # Switching a person off signs them out at once.
    before = d.token_version or 0
    w.api(w.sa).put(f"/api/v1/platform/users/{d.id}", json={"preset": "director", "active": False})
    w.db.refresh(d)
    assert d.active is False and (d.token_version or 0) == before + 1


def test_switches_can_be_turned_on_one_by_one(w):
    u = w.mk("ops@finyl.app", preset="custom", all_tenants=True,
             perms=["platform.dashboard", "platform.pipeline"])
    assert w.api(u).get("/api/v1/platform/leads").status_code == 200
    assert w.api(u).get("/api/v1/platform/alerts").status_code == 403
    assert w.api(u).get("/api/v1/platform/facilities").status_code == 403
    w.api(w.sa).put(f"/api/v1/platform/users/{u.id}",
                    json={"preset": "custom", "all_tenants": True,
                          "permissions": ["platform.dashboard", "platform.alerts", "platform.lending"]})
    assert w.api(u).get("/api/v1/platform/alerts").status_code == 200
    assert w.api(u).get("/api/v1/platform/facilities").status_code == 200
    assert w.api(u).get("/api/v1/platform/leads").status_code == 403


def test_people_cannot_hand_out_more_than_they_have(w):
    boss = w.mk("lead@finyl.app", preset="custom", tenant_ids=[w.A.id],
                perms=["platform.dashboard", "platform.users_manage"])
    r = w.api(boss).post("/api/v1/platform/users", json={
        "email": "x@finyl.app", "full_name": "X", "password": "Temp-Pass-123", "preset": "custom",
        "permissions": ["platform.dashboard", "platform.revenue"], "tenant_ids": [w.A.id]})
    assert r.status_code == 403
    r = w.api(boss).post("/api/v1/platform/users", json={
        "email": "x@finyl.app", "full_name": "X", "password": "Temp-Pass-123", "preset": "custom",
        "permissions": ["platform.dashboard"], "tenant_ids": [w.B.id]})
    assert r.status_code == 403
    r = w.api(boss).post("/api/v1/platform/users", json={
        "email": "x@finyl.app", "full_name": "X", "password": "Temp-Pass-123", "preset": "custom",
        "permissions": ["platform.dashboard"], "tenant_ids": [w.A.id]})
    assert r.status_code == 200


def test_auth_me_for_a_finyl_user(w):
    import app.main as main_mod
    from app.core.security import create_access_token, hash_password
    d = w.mk("me@finyl.app", preset="finance", tenant_ids=[w.A.id])
    d.hashed_password = hash_password("Temp-Pass-123")
    d.force_password_reset = False
    w.db.commit()
    main_mod.app.dependency_overrides[get_db] = lambda: w.db
    main_mod.app.dependency_overrides.pop(get_current_user, None)
    token = create_access_token(d.id, d.role, None, d.token_version or 0)
    r = TestClient(main_mod.app).get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["role"] == "platform_user" and me["tenant_id"] is None
    assert "platform.revenue" in me["permissions"] and me["platform"]["tenant_ids"] == [w.A.id]


def test_finyl_user_signs_in_for_real_and_uses_the_command_centre(w, monkeypatch):
    import app.main as main_mod
    from app.core.security import hash_password
    from tests.conftest import TestingSessionLocal
    monkeypatch.setattr(main_mod, "SessionLocal", TestingSessionLocal)
    d = w.mk("real@finyl.app", preset="operations", tenant_ids=[w.A.id])
    d.hashed_password = hash_password("Temp-Pass-123")
    d.force_password_reset = False
    w.db.commit()
    main_mod.app.dependency_overrides[get_db] = lambda: w.db
    main_mod.app.dependency_overrides.pop(get_current_user, None)
    c = TestClient(main_mod.app)
    r = c.post("/api/v1/auth/login", json={"email": "real@finyl.app", "password": "Temp-Pass-123"})
    assert r.status_code == 200, r.text
    tok = r.json()["access_token"]
    r = c.get("/api/v1/platform/overview", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert [t["id"] for t in r.json()["tenants"]] == [w.A.id]
    # A Finyl user is not a DCP user: DCP pages refuse them.
    r = c.get("/api/v1/clients", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (400, 401, 403, 404)


# =========================================================================== #
# One DCP's dashboard
# =========================================================================== #
def test_dcp_dashboard_has_disbursed_vs_collected_sms_and_health(w):
    s = w.seed
    today = platform_metrics.today_kenya()
    loan = s.make_loan(status="active", principal="20000.00", outstanding_balance="15000.00",
                       disbursement_date=today - timedelta(days=3), due_date=today + timedelta(days=25))
    w.db.add(Repayment(tenant_id=w.A.id, loan_id=loan.id, amount=5000, payment_date=datetime.utcnow(),
                       method="mpesa_c2b", mpesa_ref="RPX1"))
    for i in range(4):
        w.db.add(SmsLog(tenant_id=w.A.id, recipient_phone="254700000000", message="hi", status="sent",
                        billable=True, sell_price_kes=0.8, cost_price_kes=0.5, margin_kes=0.3))
    w.db.commit()

    r = w.api(w.sa).get(f"/api/v1/platform/tenants/{w.A.id}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["book"]["outstanding"] == 15000.0
    assert d["window"]["disbursed"] == 20000.0 and d["window"]["collected"] == 5000.0
    assert d["monthly"][-1]["disbursed"] == 20000.0 and len(d["monthly"]) == 12
    assert d["sms"]["sent"] == 4 and d["sms"]["revenue"] == pytest.approx(3.2)
    assert d["health"]["band"] in ("green", "amber", "red")
    assert "billing" in d and "facilities" in d
    # The DCP can be told who at Finyl looked at its figures.
    row = w.db.query(AuditLog).filter(AuditLog.action == "platform.dcp_viewed").one()
    assert row.tenant_id == w.A.id

    o = w.api(w.sa).get("/api/v1/platform/overview").json()
    assert o["totals"]["outstanding"] == 15000.0 and o["totals"]["sms_sent"] == 4


def test_health_turns_red_and_raises_one_alert(w):
    s = w.seed
    today = platform_metrics.today_kenya()
    s.make_loan(status="overdue", principal="10000.00", outstanding_balance="10000.00",
                disbursement_date=today - timedelta(days=200), due_date=today - timedelta(days=120))
    w.db.commit()
    platform_metrics.snapshot(w.db, today)
    platform_metrics.snapshot(w.db, today)
    snap = w.db.query(PlatformTenantDaily).filter(PlatformTenantDaily.tenant_id == w.A.id).one()
    assert float(snap.outstanding) == 10000.0 and float(snap.par30) == 10000.0
    a = w.db.query(PlatformAlert).filter(PlatformAlert.kind == "dcp_health").all()
    assert len(a) == 1 and a[0].tenant_id == w.A.id


# =========================================================================== #
# Finyl's billing
# =========================================================================== #
def test_standard_prices_are_the_agreed_ones(w):
    p = w.api(w.sa).get("/api/v1/platform/pricing").json()
    assert p["onboarding_fee"] == 50000 and p["subscription_rate_pct"] == 1
    assert p["subscription_minimum"] == 30000 and p["branch_fee"] == 20000 and p["invoice_day"] == 1


def test_first_invoice_minimum_then_one_percent_and_new_branches(w):
    s = w.seed
    w.api(w.sa).put(f"/api/v1/platform/tenants/{w.A.id}/billing",
                    json={"billing_active": True, "billing_start": "2026-09-01"})
    # A small book: the KES 30,000 minimum applies, plus the one-off onboarding.
    s.make_loan(status="active", principal="1000000", outstanding_balance="1000000",
                due_date=date.today() + timedelta(days=30))
    w.db.commit()
    r = w.api(w.sa).post("/api/v1/platform/invoices/generate", json={"period": "2026-09", "tenant_id": w.A.id})
    assert r.status_code == 200, r.text
    inv = r.json()["made"][0]
    assert inv["total"] == 80000.0 and inv["number"] == "INV-2026-09-0001"
    # The same month twice is refused.
    r = w.api(w.sa).post("/api/v1/platform/invoices/generate", json={"period": "2026-09", "tenant_id": w.A.id})
    assert r.status_code == 422

    # October: the book grew to 5,000,000 (1% = 50,000) and a second branch opened
    # (the one branch it had when billing started is covered by onboarding).
    w.db.add(PlatformTenantDaily(tenant_id=w.A.id, day=date(2026, 10, 31), outstanding=5000000))
    region = w.db.query(Region).filter(Region.tenant_id == w.A.id).first()
    w.db.add(Branch(tenant_id=w.A.id, region_id=region.id, name="Thika"))
    w.db.commit()
    r = w.api(w.sa).post("/api/v1/platform/invoices/generate", json={"period": "2026-10", "tenant_id": w.A.id})
    inv = w.api(w.sa).get(f"/api/v1/platform/invoices/{r.json()['made'][0]['id']}").json()
    kinds = {ln["kind"]: ln["amount"] for ln in inv["lines"]}
    assert kinds == {"subscription": 50000.0, "branch": 20000.0}
    assert inv["pay_to"] == {"paybill": "4111222", "account": inv["number"]}

    # Billing started in September: August is refused.
    r = w.api(w.sa).post("/api/v1/platform/invoices/generate", json={"period": "2026-08", "tenant_id": w.A.id})
    assert r.status_code == 422


def test_a_dcp_can_have_its_own_prices(w):
    w.api(w.sa).put(f"/api/v1/platform/tenants/{w.A.id}/billing",
                    json={"billing_active": True, "billing_start": "2026-10-01", "onboarding_fee": 0,
                          "subscription_minimum": 15000})
    r = w.api(w.sa).post("/api/v1/platform/invoices/generate", json={"period": "2026-10", "tenant_id": w.A.id})
    assert r.json()["made"][0]["total"] == 15000.0
    b = w.api(w.sa).put(f"/api/v1/platform/tenants/{w.A.id}/billing",
                        json={"clear": ["subscription_minimum"]}).json()
    assert b["effective"]["subscription_minimum"] == 30000.0


def test_cancelling_an_invoice_frees_the_month(w):
    w.api(w.sa).put(f"/api/v1/platform/tenants/{w.A.id}/billing",
                    json={"billing_active": True, "billing_start": "2026-10-01"})
    inv = w.api(w.sa).post("/api/v1/platform/invoices/generate",
                           json={"period": "2026-10", "tenant_id": w.A.id}).json()["made"][0]
    assert w.api(w.sa).post(f"/api/v1/platform/invoices/{inv['id']}/void", json={"reason": "no"}).status_code == 422
    r = w.api(w.sa).post(f"/api/v1/platform/invoices/{inv['id']}/void", json={"reason": "Wrong book figure"})
    assert r.json()["status"] == "void"
    again = w.api(w.sa).post("/api/v1/platform/invoices/generate",
                             json={"period": "2026-10", "tenant_id": w.A.id}).json()["made"][0]
    # The onboarding fee comes back on the new invoice.
    assert again["total"] == 80000.0 and again["number"] == "INV-2026-10-0002"


def test_branches_a_dcp_already_had_are_not_charged_as_new(w):
    region = w.db.query(Region).filter(Region.tenant_id == w.A.id).first()
    for n in ("Thika", "Nakuru", "Eldoret"):
        w.db.add(Branch(tenant_id=w.A.id, region_id=region.id, name=n))
    w.db.commit()
    b = w.api(w.sa).put(f"/api/v1/platform/tenants/{w.A.id}/billing",
                        json={"billing_active": True, "billing_start": "2026-10-01"}).json()
    assert b["branches_billed"] == 3 and b["billing_start"] == "2026-10-01"
    inv = w.api(w.sa).post("/api/v1/platform/invoices/generate",
                           json={"period": "2026-10", "tenant_id": w.A.id}).json()["made"][0]
    assert inv["total"] == 80000.0                     # onboarding + minimum, no branch line
    w.db.add(Branch(tenant_id=w.A.id, region_id=region.id, name="Kisumu"))
    w.db.commit()
    inv = w.api(w.sa).post("/api/v1/platform/invoices/generate",
                           json={"period": "2026-11", "tenant_id": w.A.id}).json()["made"][0]
    assert inv["total"] == 50000.0                     # minimum + the one new branch


def test_monthly_job_bills_last_month_for_every_active_dcp(w):
    for t in (w.A, w.B):
        b = platform_billing.billing_for(w.db, t.id)
        b.billing_active, b.billing_start = True, date(2026, 1, 1)
    w.db.commit()
    out = platform_billing.generate_due(w.db, today=date(2026, 11, 1))
    assert out == {"period": "2026-10", "made": 2, "skipped": 0}
    assert platform_billing.generate_due(w.db, today=date(2026, 11, 1))["skipped"] == 2


def test_payment_to_finyl_paybill_settles_the_invoice(w):
    w.api(w.sa).put(f"/api/v1/platform/tenants/{w.A.id}/billing",
                    json={"billing_active": True, "billing_start": "2026-10-01"})
    inv = w.api(w.sa).post("/api/v1/platform/invoices/generate",
                           json={"period": "2026-10", "tenant_id": w.A.id}).json()["made"][0]
    _ingest(w.db, _c2b("4111222", inv["number"].lower(), "FNY0001", 30000))
    _ingest(w.db, _c2b("4111222", inv["number"], "FNY0001", 30000))         # repeated: ignored
    # The DCP's own code also works: its oldest unpaid invoice.
    _ingest(w.db, _c2b("4111222", w.A.code, "FNY0002", 50000))
    got = w.api(w.sa).get(f"/api/v1/platform/invoices/{inv['id']}").json()
    assert got["paid"] == 80000.0 and got["status"] == "paid" and len(got["payments"]) == 2


def test_unrecognised_payment_waits_and_can_be_allocated(w):
    w.api(w.sa).put(f"/api/v1/platform/tenants/{w.A.id}/billing",
                    json={"billing_active": True, "billing_start": "2026-10-01"})
    inv = w.api(w.sa).post("/api/v1/platform/invoices/generate",
                           json={"period": "2026-10", "tenant_id": w.A.id}).json()["made"][0]
    _ingest(w.db, _c2b("4111222", "SEPT FEES", "FNY0009", 1000))
    items = w.api(w.sa).get("/api/v1/platform/unmatched").json()["items"]
    assert len(items) == 1 and items[0]["account_entered"] == "SEPT FEES"
    assert w.db.query(PlatformAlert).filter(PlatformAlert.kind == "payment_unroutable").count() == 1
    r = w.api(w.sa).post(f"/api/v1/platform/unmatched/{items[0]['id']}/allocate", json={"invoice_id": inv["id"]})
    assert r.status_code == 200
    assert w.api(w.sa).get(f"/api/v1/platform/invoices/{inv['id']}").json()["paid"] == 1000.0
    assert w.db.query(PlatformUnmatchedPayment).one().status == "allocated"


# =========================================================================== #
# Finyl's loans to DCPs
# =========================================================================== #
def test_facility_drawdown_schedule_and_repayment_through_the_paybill(w):
    r = w.api(w.sa).post("/api/v1/platform/facilities", json={
        "tenant_id": w.A.id, "limit_amount": 1000000, "interest_rate_pct": 2, "term_months": 6,
        "interest_method": "flat", "start_date": "2026-09-01"})
    assert r.status_code == 200, r.text
    fac = r.json()
    assert fac["number"] == "FAC-0001" and fac["available"] == 1000000.0
    r = w.api(w.sa).post(f"/api/v1/platform/facilities/{fac['id']}/drawdowns",
                         json={"amount": 1200000, "disbursed_on": "2026-09-01"})
    assert r.status_code == 422                                     # above the limit
    r = w.api(w.sa).post(f"/api/v1/platform/facilities/{fac['id']}/drawdowns",
                         json={"amount": 600000, "disbursed_on": "2026-09-01"})
    f = r.json()
    assert f["drawn"] == 600000.0 and f["available"] == 400000.0 and len(f["schedule"]) == 6
    # Flat 2% a month: 100,000 principal + 12,000 interest per instalment.
    assert f["schedule"][0] == {"due": "2026-10-01", "principal": 100000.0, "interest": 12000.0,
                                "paid": 0.0, "unpaid": 112000.0}
    _ingest(w.db, _c2b("4111222", "fac-0001", "FNYL100", 112000))
    f = w.api(w.sa).get(f"/api/v1/platform/facilities/{fac['id']}").json()
    assert f["paid"] == 112000.0 and f["principal_outstanding"] == 500000.0
    assert f["payments"][0]["interest"] == 12000.0 and f["payments"][0]["principal"] == 100000.0
    # The second instalment (1 Nov) is unpaid: an alert for Finyl.
    platform_lending.overdue_alerts(w.db)
    a = w.db.query(PlatformAlert).filter(PlatformAlert.kind == "facility_overdue").one()
    assert a.tenant_id == w.A.id
    # The DCP's dashboard shows the facility.
    d = w.api(w.sa).get(f"/api/v1/platform/tenants/{w.A.id}").json()
    assert d["facilities"][0]["number"] == "FAC-0001"


def test_reducing_balance_facility_is_fully_repaid_and_closes(w):
    fac = w.api(w.sa).post("/api/v1/platform/facilities", json={
        "tenant_id": w.B.id, "limit_amount": 100000, "interest_rate_pct": 3, "term_months": 3}).json()
    f = w.api(w.sa).post(f"/api/v1/platform/facilities/{fac['id']}/drawdowns", json={"amount": 100000}).json()
    total = round(sum(r["principal"] + r["interest"] for r in f["schedule"]), 2)
    assert 100000 < total < 109000
    f = w.api(w.sa).post(f"/api/v1/platform/facilities/{fac['id']}/payments",
                         json={"amount": total, "reference": "BANK-77"}).json()
    assert f["status"] == "closed" and f["principal_outstanding"] == 0.0


# =========================================================================== #
# Pipeline
# =========================================================================== #
def test_pipeline_from_lead_to_live(w):
    r = w.api(w.sa).post("/api/v1/platform/leads", json={"company_name": "Gamma Loans", "expected_loan_book": 8000000})
    lead = r.json()
    assert lead["stage"] == "lead"
    assert w.api(w.sa).post(f"/api/v1/platform/leads/{lead['id']}/stage", json={"stage": "live"}).status_code == 422
    assert w.api(w.sa).post(f"/api/v1/platform/leads/{lead['id']}/stage", json={"stage": "lost"}).status_code == 422
    w.api(w.sa).post(f"/api/v1/platform/leads/{lead['id']}/stage", json={"stage": "demo", "note": "Went well"})
    p = w.api(w.sa).get("/api/v1/platform/leads").json()
    assert {s["key"]: s["count"] for s in p["stages"]}["demo"] == 1
    assert p["potential_monthly_subscription"] == 80000.0      # 1% of 8m
    assert {t["name"] for t in p["onboarded"]} == {w.A.name, "Beta Credit"}
    w.api(w.sa).put(f"/api/v1/platform/leads/{lead['id']}", json={"tenant_id": w.B.id})
    r = w.api(w.sa).post(f"/api/v1/platform/leads/{lead['id']}/stage", json={"stage": "live"})
    assert r.status_code == 200 and r.json()["tenant"] == "Beta Credit"
    h = w.api(w.sa).get(f"/api/v1/platform/leads/{lead['id']}/history").json()["items"]
    assert [x["to"] for x in h] == ["lead", "demo", "live"]


# =========================================================================== #
# Alerts
# =========================================================================== #
def test_repeating_failures_make_one_alert_and_email_the_right_people(w, monkeypatch):
    from app.services import mailer
    sent = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subj, body, **k: sent.append((to, subj)) or True)
    rec = w.db.query(PlatformAlertRecipient).all()
    if not rec:   # create_all does not run the migration's seed row
        w.db.add(PlatformAlertRecipient(name="Afridon IT support", email="afridon.1.it@gmail.com",
                                        min_severity="warning"))
    w.db.add(PlatformAlertRecipient(name="Only Beta", email="beta@finyl.app", min_severity="info",
                                    tenant_ids=[w.B.id]))
    w.db.add(PlatformAlertRecipient(name="Critical only", email="cto@finyl.app", min_severity="critical"))
    w.db.commit()
    for _ in range(3):
        platform_alerts.raise_alert("sms_failed", "SMS could not be sent", "provider said no",
                                    tenant_id=w.A.id, key="uwazii")
    a = w.db.query(PlatformAlert).one()
    assert a.count == 3 and a.severity == "warning"
    platform_alerts.deliver_pending(w.db)
    assert [to for to, _ in sent] == ["afridon.1.it@gmail.com"]
    assert "[Finyl WARNING] Test DCP" in sent[0][1]
    platform_alerts.deliver_pending(w.db)                       # already told: not again
    assert len(sent) == 1

    r = w.api(w.sa).get("/api/v1/platform/alerts").json()
    assert r["items"][0]["count"] == 3 and r["items"][0]["tenant"] == "Test DCP"
    w.api(w.sa).post(f"/api/v1/platform/alerts/{a.id}/resolve", json={"note": "Topped up credit"})
    platform_alerts.raise_alert("sms_failed", "SMS could not be sent", tenant_id=w.A.id, key="uwazii")
    assert w.db.query(PlatformAlert).count() == 2               # a resolved alert is not reopened


def test_alert_recipients_are_managed_in_the_app(w):
    r = w.api(w.sa).post("/api/v1/platform/alert-recipients",
                         json={"name": "Support", "email": "not-an-email"})
    assert r.status_code == 422
    r = w.api(w.sa).post("/api/v1/platform/alert-recipients",
                         json={"name": "Support", "email": "support@finyl.app", "min_severity": "critical",
                               "kinds": ["payout_failed", "server_error"]})
    assert r.status_code == 200
    rid = r.json()["id"]
    r = w.api(w.sa).put(f"/api/v1/platform/alert-recipients/{rid}",
                        json={"name": "Support desk", "email": "support@finyl.app", "kinds": ["bogus"]})
    assert r.status_code == 422
    items = w.api(w.sa).get("/api/v1/platform/alert-recipients").json()["items"]
    assert any(i["name"] == "Support" and i["kinds"] == ["payout_failed", "server_error"] for i in items)
    assert w.api(w.sa).delete(f"/api/v1/platform/alert-recipients/{rid}").status_code == 200


def test_unexpected_error_gives_a_reference_and_raises_an_alert(w, monkeypatch):
    from app.routers import platform as platform_router

    def boom(*a, **k):
        raise RuntimeError("database fell over")
    monkeypatch.setattr(platform_router.billing, "pricing", boom)
    r = w.api(w.sa, raise_server_exceptions=False).get("/api/v1/platform/pricing")
    assert r.status_code == 500
    d = r.json()["detail"]
    assert d["code"] == "server_error" and d["error_id"] and "database fell over" not in d["message"]
    a = w.db.query(PlatformAlert).filter(PlatformAlert.kind == "server_error").one()
    assert a.severity == "critical" and d["error_id"] in a.detail


def test_failed_job_raises_a_critical_alert(w, monkeypatch):
    from app.services import scheduler
    from tests.conftest import TestingSessionLocal
    monkeypatch.setattr(scheduler, "SessionLocal", TestingSessionLocal)

    def job():
        raise ValueError("no")
    scheduler.guarded("wp13_demo_job", job)()
    a = w.db.query(PlatformAlert).filter(PlatformAlert.kind == "job_failed").one()
    assert a.severity == "critical" and "wp13_demo_job" in a.title


def test_access_rows_are_stored(w):
    d = w.mk("store@finyl.app", preset="finance", tenant_ids=[w.B.id, w.A.id])
    row = w.db.get(PlatformUserAccess, d.id)
    assert row.preset == "finance" and row.tenant_ids == sorted([w.A.id, w.B.id])
    assert "platform.billing_manage" in row.permissions


def test_weekly_summary_and_billing_list(w, monkeypatch):
    from app.services import mailer
    sent = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subj, body, **k: sent.append((to, subj, body)) or True)
    w.db.add(PlatformAlertRecipient(name="Partner", email="p@finyl.app", tenant_ids=[w.B.id]))
    w.db.add(PlatformAlertRecipient(name="No summary", email="n@finyl.app", weekly_summary=False))
    w.db.commit()
    platform_alerts.weekly_summary(w.db)
    to = {t: b for t, _, b in sent}
    assert set(to) == {"p@finyl.app"} or set(to) == {"p@finyl.app", "afridon.1.it@gmail.com"}
    assert "Beta Credit" in to["p@finyl.app"] and "Test DCP" not in to["p@finyl.app"]
    items = w.api(w.sa).get("/api/v1/platform/billing/dcps").json()["items"]
    assert {i["name"] for i in items} == {"Test DCP", "Beta Credit"} and items[0]["unpaid"] == 0
