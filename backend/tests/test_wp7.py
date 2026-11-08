"""
WP7 - screens and DCP administration: the corrected behaviour in more detail
than the reproductions in test_wp7_repro.py.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import hash_password
from app.models import (CustomRole, OtpToken, Product, ProductChangeRequest,
                        RolePermissionOverride, Tenant, TenantIntegrationConfig,
                        TenantSecurityConfig, User, UserDevice)

_PW = None


def _pw():
    global _PW
    if _PW is None:
        _PW = hash_password("Passw0rd!x")
    return _PW


@pytest.fixture()
def w(seed, db):
    import app.main as main_mod
    from app.routers import auth as auth_mod

    A = seed.tenant
    B = Tenant(name="Other DCP", code="OTH", active=True)
    db.add(B)
    db.flush()

    def mk(role, email, tenant=A, **kw):
        u = User(email=email, hashed_password=_pw(), full_name=email.split("@")[0],
                 role=role, tenant_id=tenant.id if tenant else None, **kw)
        db.add(u)
        db.flush()
        return u

    users = SimpleNamespace(
        sysadmin=mk("system_admin", "sys@a.dcp"),
        sysadmin2=mk("system_admin", "sys2@a.dcp"),
        bm=mk("branch_manager", "bm@a.dcp", branch_id=seed.branch.id),
        ro=mk("relationship_officer", "ro@a.dcp", staff_id=seed.staff.id,
              branch_id=seed.branch.id),
        other=mk("relationship_officer", "ro@b.dcp", tenant=B),
        sa=mk("super_admin", "sa@finyl.app", tenant=None),
    )
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        main_mod.app.dependency_overrides[auth_mod.rate_limit_login] = lambda: None
        return TestClient(main_mod.app)

    yield SimpleNamespace(seed=seed, db=db, A=A, B=B, u=users, api=api,
                          H={"X-Tenant-Id": str(A.id)})
    main_mod.app.dependency_overrides.clear()


# =========================================================================== #
# Staff administration by the DCP's own admin
# =========================================================================== #
def test_dcp_admin_cannot_lock_out_or_promote_themselves(w):
    api = w.api(w.u.sysadmin)
    me = w.u.sysadmin.id
    assert api.post(f"/api/v1/access/users/{me}/state?activate=false").status_code == 403
    assert api.post(f"/api/v1/access/users/{me}/role",
                    json={"role": "tenant_admin"}).status_code == 403
    # ...but a second administrator can
    assert w.api(w.u.sysadmin2).post(f"/api/v1/access/users/{me}/role",
                                     json={"role": "tenant_admin"}).status_code == 200


def test_dcp_admin_only_sees_and_touches_own_dcp(w):
    api = w.api(w.u.sysadmin)
    emails = {u["email"] for u in api.get("/api/v1/access/users").json()}
    assert "ro@b.dcp" not in emails
    assert api.put(f"/api/v1/access/users/{w.u.other.id}",
                   json={"full_name": "x"}).status_code == 404
    # a DCP user cannot point at another DCP with the tenant header
    assert "ro@b.dcp" not in {u["email"] for u in api.get(
        "/api/v1/access/users", headers={"X-Tenant-Id": str(w.B.id)}).json()}


def test_platform_tools_stay_with_finyl(w):
    api = w.api(w.u.sysadmin)
    assert api.post("/api/v1/access/backup").status_code == 403
    assert api.get("/api/v1/access/integrity").status_code == 403
    assert api.get("/api/v1/access/approver-config").status_code == 403


def test_custom_role_gets_its_granted_permissions(w):
    api = w.api(w.u.sysadmin)
    assert api.post("/api/v1/access/roles", json={"role_key": "loan_viewer",
                                                  "label": "Loan viewer"}).status_code == 200
    assert api.post("/api/v1/access/roles/loan_viewer/permissions",
                    json={"permission_key": "loans.view_all", "granted": True}).status_code == 200
    roles = {r["role"] for r in api.get("/api/v1/access/permissions").json()["assignable_roles"]}
    assert "loan_viewer" in roles
    assert api.post(f"/api/v1/access/users/{w.u.ro.id}/role",
                    json={"role": "loan_viewer"}).status_code == 200
    w.db.refresh(w.u.ro)
    from app.services.authz import user_can
    assert user_can(w.db, w.u.ro, "loans.view_all")
    assert not user_can(w.db, w.u.ro, "loans.create")


def test_revoked_permission_stops_payout_resolution(w):
    """payments resolve used the static role map, ignoring a revoke."""
    w.db.add(RolePermissionOverride(tenant_id=w.A.id, role="branch_manager",
                                    permission_key="disburse.approve", granted=False))
    w.db.commit()
    from app.services.authz import user_can
    assert not user_can(w.db, w.u.bm, "disburse.approve")


# =========================================================================== #
# Products: the DCP proposes, Finyl approves
# =========================================================================== #
NEW = {"name": "Boda Boda Loan", "code": "BB1", "interest_rate": 10,
       "min_amount": 1000, "max_amount": 20000, "tenure_value": 4}


def test_new_product_is_not_live_until_finyl_approves(w):
    r = w.api(w.u.sysadmin).post("/api/v1/lending/products", json=NEW)
    assert r.status_code == 200 and r.json()["status"] == "pending_approval"
    assert w.db.query(Product).filter_by(code="BB1").count() == 0
    rid = r.json()["request_id"]
    # the DCP cannot approve its own proposal
    assert w.api(w.u.sysadmin).post(f"/api/v1/lending/product-requests/{rid}/approve",
                                    json={}).status_code == 403
    r = w.api(w.u.sa).post(f"/api/v1/lending/product-requests/{rid}/approve",
                           json={"note": "CBK letter ref 123"}, headers=w.H)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    p = w.db.query(Product).filter_by(code="BB1").one()
    assert p.tenant_id == w.A.id and float(p.interest_rate) == 10


def test_change_proposal_carries_only_what_changed(w):
    p = w.seed.product
    body = {"name": p.name, "code": p.code, "interest_rate": 12,
            "min_amount": float(p.min_amount), "max_amount": float(p.max_amount)}
    r = w.api(w.u.sysadmin).put(f"/api/v1/lending/products/{p.id}", json=body)
    assert r.json()["status"] == "pending_approval"
    req = w.db.get(ProductChangeRequest, r.json()["request_id"])
    assert req.proposed == {"interest_rate": 12}
    w.db.refresh(p)
    assert float(p.interest_rate) != 12                   # not live yet
    # one open proposal per product
    assert w.api(w.u.sysadmin).put(f"/api/v1/lending/products/{p.id}",
                                   json=dict(body, interest_rate=13)).status_code == 409
    listed = w.api(w.u.sysadmin).get("/api/v1/lending/products").json()
    assert next(x for x in listed if x["id"] == p.id)["pending_change"]["proposed"] == {"interest_rate": 12}
    # reject needs a reason; then the DCP can propose again
    assert w.api(w.u.sa).post(f"/api/v1/lending/product-requests/{req.id}/reject",
                              json={}, headers=w.H).status_code == 422
    assert w.api(w.u.sa).post(f"/api/v1/lending/product-requests/{req.id}/reject",
                              json={"note": "Above CBK-approved rate"},
                              headers=w.H).status_code == 200
    assert w.api(w.u.sysadmin).put(f"/api/v1/lending/products/{p.id}",
                                   json=dict(body, interest_rate=11)).status_code == 200


def test_pausing_applies_at_once_and_others_cannot_propose(w):
    p = w.seed.product
    r = w.api(w.u.sysadmin).put(f"/api/v1/lending/products/{p.id}",
                                json={"name": p.name, "code": p.code, "active": False})
    assert r.json()["status"] == "live"
    w.db.refresh(p)
    assert p.active is False
    # switching it back on is a change Finyl approves
    r = w.api(w.u.sysadmin).put(f"/api/v1/lending/products/{p.id}",
                                json={"name": p.name, "code": p.code, "active": True})
    assert r.json()["status"] == "pending_approval"
    # a branch manager has no products.manage
    assert w.api(w.u.bm).post("/api/v1/lending/products", json=NEW).status_code == 403


def test_withdraw_and_min_age_floor(w):
    r = w.api(w.u.sysadmin).post("/api/v1/lending/products", json=dict(NEW, min_age=16))
    assert r.status_code == 422
    rid = w.api(w.u.sysadmin).post("/api/v1/lending/products", json=NEW).json()["request_id"]
    r = w.api(w.u.sysadmin).post(f"/api/v1/lending/product-requests/{rid}/withdraw")
    assert r.json()["status"] == "withdrawn"
    assert w.api(w.u.sa).post(f"/api/v1/lending/product-requests/{rid}/approve",
                              json={}, headers=w.H).status_code == 409


# =========================================================================== #
# Tenant switches
# =========================================================================== #
def _login(w, user, headers=None):
    return w.api(user).post("/api/v1/auth/login", headers=headers or {},
                            json={"email": user.email, "password": "Passw0rd!x"})


def test_finyl_can_switch_a_dcp_off_and_on(w):
    sa = w.api(w.u.sa)
    assert sa.post(f"/api/v1/admin/tenants/{w.A.id}/state",
                   json={"active": False}).status_code == 422          # reason needed
    assert sa.post(f"/api/v1/admin/tenants/{w.A.id}/state",
                   json={"active": False, "reason": "Licence suspended"}).status_code == 200
    r = _login(w, w.u.ro)
    assert r.status_code == 403 and "deactivated" in r.json()["detail"]
    assert w.api(w.u.sa).post(f"/api/v1/admin/tenants/{w.A.id}/state",
                              json={"active": True}).status_code == 200
    assert _login(w, w.u.ro).status_code == 200


def test_mpesa_platform_fallback_setting(w, monkeypatch):
    from app.core.config import settings
    from app.services import mpesa
    monkeypatch.setattr(settings, "DARAJA_CONSUMER_KEY", "platform-key")
    monkeypatch.setattr(settings, "DARAJA_CONSUMER_SECRET", "platform-secret")
    monkeypatch.setattr(settings, "DARAJA_ENVIRONMENT", "production")
    # live platform credentials: a DCP without its own setup gets nothing
    assert not mpesa.resolve_creds(w.db, w.A.id).configured
    monkeypatch.setattr(settings, "DARAJA_PLATFORM_FALLBACK", "true")
    assert mpesa.resolve_creds(w.db, w.A.id).configured
    # sandbox platform credentials: allowed by default (demos and tests)
    monkeypatch.setattr(settings, "DARAJA_PLATFORM_FALLBACK", "auto")
    monkeypatch.setattr(settings, "DARAJA_ENVIRONMENT", "sandbox")
    assert mpesa.resolve_creds(w.db, w.A.id).configured


# =========================================================================== #
# Devices and location
# =========================================================================== #
def test_device_request_approval_and_revocation(w):
    w.db.add(TenantSecurityConfig(tenant_id=w.A.id, device_binding_enabled=True))
    w.db.commit()
    H = {"X-Device-Fingerprint": "abcd1234efgh5678"}
    r = _login(w, w.u.ro, H)
    assert r.status_code == 403 and "ABCD-1234" in r.json()["detail"]
    pend = w.api(w.u.sysadmin).get("/api/v1/devices/pending").json()["items"]
    assert [d["code"] for d in pend] == ["ABCD-1234"]
    dev_id = pend[0]["id"]
    # an officer cannot approve devices; nobody approves their own
    assert w.api(w.u.ro).post(f"/api/v1/users/{w.u.ro.id}/devices/{dev_id}/approve").status_code == 403
    assert w.api(w.u.sysadmin).post(
        f"/api/v1/users/{w.u.ro.id}/devices/{dev_id}/approve").status_code == 200
    assert _login(w, w.u.ro, H).status_code == 200
    tv = w.u.ro.token_version or 0
    assert w.api(w.u.sysadmin).delete(
        f"/api/v1/users/{w.u.ro.id}/devices/{dev_id}").json()["sessions_ended"] is True
    w.db.refresh(w.u.ro)
    assert (w.u.ro.token_version or 0) == tv + 1
    assert _login(w, w.u.ro, H).status_code == 403


def test_geofence_inside_and_outside(w):
    w.db.add(TenantSecurityConfig(tenant_id=w.A.id, geofence_enabled=True,
                                  geofence_center_lat=-1.2864, geofence_center_lng=36.8172,
                                  geofence_radius_km=20))
    w.db.commit()
    inside = {"X-Geo-Lat": "-1.29", "X-Geo-Lng": "36.82"}
    outside = {"X-Geo-Lat": "-4.05", "X-Geo-Lng": "39.66"}          # Mombasa
    assert _login(w, w.u.ro, inside).status_code == 200
    assert _login(w, w.u.ro, outside).status_code == 403


def test_client_ip_with_zero_and_two_proxies(monkeypatch):
    from app.core.config import settings
    from app.core.net import client_ip
    req = SimpleNamespace(headers={"x-forwarded-for": "6.6.6.6, 41.90.1.2, 10.0.0.2"},
                          client=SimpleNamespace(host="10.0.0.9"))
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    assert client_ip(req) == "10.0.0.9"
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2)
    assert client_ip(req) == "41.90.1.2"


# =========================================================================== #
# Screens
# =========================================================================== #
def test_otp_history_lists_codes_without_the_digits(w):
    from app.services import otp
    otp.generate(w.db, w.u.ro, purpose="login")
    otp.generate(w.db, w.u.other, purpose="login")              # other DCP
    r = w.api(w.u.sysadmin).get("/api/v1/security/otp-history")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["user_name"] for i in items] == ["ro"]
    assert items[0]["state"] == "waiting" and "otp_hash" not in items[0]
    assert w.api(w.u.ro).get("/api/v1/security/otp-history").status_code == 403


def test_search_offers_only_pages_the_person_can_open(w):
    def routes(user):
        hits = w.api(user).get("/api/v1/search?q=users").json()["hits"]
        return {h["route"] for h in hits if h["entity_type"] == "command"}
    assert "/access/users" in routes(w.u.sysadmin)
    assert "/access/users" not in routes(w.u.ro)


def test_consent_given_by_sms_code_counts(w, monkeypatch):
    from app.services import kyc_decisions, otp, sms
    w.seed.make_kyc_ready(w.seed.borrower)
    from app.models import KycConsent
    w.db.query(KycConsent).delete()
    w.db.commit()
    assert "consent_missing" in kyc_decisions.evaluate(
        w.db, tenant_id=w.A.id, client_id=w.seed.borrower.id).reasons
    sent = []
    monkeypatch.setattr(sms, "_dispatch_to_provider",
                        lambda phone, msg: sent.append(msg) or {"status": "sent"})
    api = w.api(w.u.ro)
    b = w.seed.borrower
    assert api.post("/api/v1/clients/request-consent-otp",
                    json={"phone": b.phone, "client_id": b.id}).status_code == 200
    code = sent[-1].split("Share code ")[1][:6]
    assert api.post("/api/v1/clients/verify-consent-otp",
                    json={"code": code, "phone": b.phone, "client_id": b.id}).status_code == 200
    assert kyc_decisions.evaluate(w.db, tenant_id=w.A.id, client_id=b.id).decision == "pass"


def test_switching_on_devices_registers_the_admins_own_browser(w):
    api = w.api(w.u.sysadmin)
    assert api.put("/api/v1/settings/security",
                   json={"device_binding_enabled": True}).status_code == 422
    r = api.put("/api/v1/settings/security", json={"device_binding_enabled": True},
                headers={"X-Device-Fingerprint": "admin-browser-1"})
    assert r.status_code == 200
    assert _login(w, w.u.sysadmin, {"X-Device-Fingerprint": "admin-browser-1"}).status_code == 200
