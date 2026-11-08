"""
WP7 reproductions: screens and DCP administration (review section 7, items 2,
6, 7, 8, 10, 11). Written against the code as it stood after WP6. Each test
asserts the CORRECT behaviour, so on the old code it fails and shows the
defect; after WP7 it passes and guards against a regression.
"""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import (Borrower, CustomRole, KycConsent, RolePermissionOverride,
                        Tenant, TenantIntegrationConfig, TenantModule,
                        TenantSecurityConfig, User)


@pytest.fixture()
def w(seed, db):
    import app.main as main_mod
    from app.routers import auth as auth_mod

    A = seed.tenant

    def mk(role, email, **kw):
        u = User(email=email, hashed_password="x", full_name=email.split("@")[0],
                 role=role, tenant_id=A.id, **kw)
        db.add(u)
        db.flush()
        return u

    users = SimpleNamespace(
        sysadmin=mk("system_admin", "sys@a.dcp"),
        bm=mk("branch_manager", "bm@a.dcp", branch_id=seed.branch.id),
        ro=mk("relationship_officer", "ro@a.dcp", staff_id=seed.staff.id,
              branch_id=seed.branch.id),
    )
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        main_mod.app.dependency_overrides[auth_mod.rate_limit_login] = lambda: None
        return TestClient(main_mod.app)

    yield SimpleNamespace(seed=seed, db=db, A=A, u=users, api=api)
    main_mod.app.dependency_overrides.clear()


# --- 7.6: DCP administration ------------------------------------------------
def test_dcp_admin_can_manage_own_staff(w):
    api = w.api(w.u.sysadmin)
    assert api.get("/api/v1/access/users").status_code == 200
    r = api.post("/api/v1/access/users", json={
        "email": "new.officer@a.dcp", "full_name": "New Officer",
        "role": "relationship_officer", "password": "Str0ng!Passw0rd"})
    assert r.status_code == 200, r.text


def test_custom_role_can_be_assigned(w):
    w.db.add(CustomRole(tenant_id=w.A.id, role_key="field_auditor", label="Field Auditor"))
    w.db.commit()
    r = w.api(w.u.sysadmin).post(f"/api/v1/access/users/{w.u.ro.id}/role",
                                 json={"role": "field_auditor"})
    assert r.status_code == 200, r.text


def test_revoked_permission_is_honoured_everywhere(w):
    """The Roles screen revokes clients.approve from branch managers; the
    client form's approver list must stop offering them."""
    w.db.add(RolePermissionOverride(tenant_id=w.A.id, role="branch_manager",
                                    permission_key="clients.approve", granted=False))
    w.db.commit()
    ref = w.api(w.u.ro).get("/api/v1/clients/reference").json()
    approver_ids = {a["id"] for a in ref.get("approvers", [])}
    assert w.u.bm.id not in approver_ids


def test_dcp_admin_can_propose_a_product(w):
    r = w.api(w.u.sysadmin).post("/api/v1/lending/products", json={
        "name": "Boda Boda Loan", "code": "BB1", "interest_rate": 10,
        "min_amount": 1000, "max_amount": 20000, "tenure": 4})
    assert r.status_code in (200, 201, 202), r.text


# --- 7.10: tenant configuration ---------------------------------------------
def test_users_of_a_deactivated_dcp_are_refused(w, monkeypatch):
    from app.core import deps
    from app.core.security import create_access_token
    w.A.active = False
    w.db.commit()
    token = create_access_token(w.u.ro.id, w.u.ro.role, w.A.id, token_version=0)
    with pytest.raises(Exception) as e:
        deps.get_current_user(token=token, db=w.db)
    assert getattr(e.value, "status_code", None) in (401, 403)


def test_signup_switches_on_core_modules_only(w):
    import app.main as main_mod
    main_mod.app.dependency_overrides[get_db] = lambda: w.db
    from app.routers import auth as auth_mod
    main_mod.app.dependency_overrides[auth_mod.rate_limit_login] = lambda: None
    r = TestClient(main_mod.app).post("/api/v1/auth/signup", json={
        "organization_name": "Kilimo Credit", "admin_full_name": "Wanjiru Admin",
        "admin_email": "admin@kilimo.co.ke", "password": "Str0ng!Passw0rd#1",
        "confirm_password": "Str0ng!Passw0rd#1"})
    assert r.status_code == 201, r.text
    t = w.db.query(Tenant).filter_by(name="Kilimo Credit").one()
    on = {m.module_key for m in w.db.query(TenantModule).filter_by(tenant_id=t.id, enabled=True)}
    assert "cbk_reporting" not in on and "lending" in on


def test_disabled_dcp_mpesa_does_not_fall_back_to_platform(w, monkeypatch):
    from app.core.config import settings
    from app.services import mpesa
    monkeypatch.setattr(settings, "DARAJA_CONSUMER_KEY", "platform-key")
    monkeypatch.setattr(settings, "DARAJA_CONSUMER_SECRET", "platform-secret")
    monkeypatch.setattr(settings, "DARAJA_SHORTCODE", "600000")
    w.db.add(TenantIntegrationConfig(tenant_id=w.A.id, integration="daraja",
                                     config={"shortcode": "777111"}, secrets={},
                                     enabled=False))
    w.db.commit()
    creds = mpesa.resolve_creds(w.db, w.A.id)
    assert not creds.configured and creds.shortcode != "600000"


# --- 7.8: consent ------------------------------------------------------------
def test_client_without_data_processing_consent_fails_kyc(w):
    from app.services import kyc_decisions
    w.seed.make_kyc_ready(w.seed.borrower)
    w.db.query(KycConsent).delete()
    w.db.commit()
    d = kyc_decisions.evaluate(w.db, tenant_id=w.A.id, client_id=w.seed.borrower.id)
    assert d.decision == "fail"


# --- 7.7: security switches --------------------------------------------------
def _login(w, user, headers=None):
    from app.core.security import hash_password
    user.hashed_password = hash_password("Passw0rd!x")
    w.db.commit()
    return w.api(user).post("/api/v1/auth/login", headers=headers or {},
                            json={"email": user.email, "password": "Passw0rd!x"})


def test_geofence_asks_for_location_instead_of_refusing(w):
    w.db.add(TenantSecurityConfig(tenant_id=w.A.id, geofence_enabled=True,
                                  geofence_center_lat=-1.2864, geofence_center_lng=36.8172,
                                  geofence_radius_km=20))
    w.db.commit()
    r = _login(w, w.u.ro)
    assert r.status_code == 428, r.text


def test_unknown_device_login_leaves_a_request_for_the_admin(w):
    from app.models import UserDevice
    w.db.add(TenantSecurityConfig(tenant_id=w.A.id, device_binding_enabled=True))
    w.db.commit()
    r = _login(w, w.u.ro, headers={"X-Device-Fingerprint": "dev-abc-123"})
    assert r.status_code == 403
    assert w.db.query(UserDevice).filter_by(user_id=w.u.ro.id).count() == 1


def test_caller_cannot_choose_their_own_ip():
    """The caller sends "X-Forwarded-For: 6.6.6.6"; the proxy (nginx / AWS load
    balancer) appends the real address 41.90.1.2. The real one must be used."""
    from app.routers import auth as auth_mod
    req = SimpleNamespace(headers={"x-forwarded-for": "6.6.6.6, 41.90.1.2"},
                          client=SimpleNamespace(host="10.0.0.5"))
    assert auth_mod._client_ip(req) == "41.90.1.2"


# --- 7.11: screen contracts --------------------------------------------------
APP_ROUTES = {"/", "/clients", "/loans", "/crm", "/settings", "/collections",
              "/field", "/gps-tracking", "/guarantors", "/access/audit", "/access/users",
              "/reporting", "/products", "/access/org", "/approvals", "/payments"}


def test_search_only_links_to_pages_that_exist(w):
    from app.services import search
    for kw, route in search._NAV_COMMANDS.items():
        assert route.split("?")[0] in APP_ROUTES, (kw, route)


def test_people_pickers_get_user_ids(w):
    org = w.api(w.u.sysadmin).get("/api/v1/lending/org").json()
    assert "users" in org and w.u.ro.id in {u["id"] for u in org["users"]}


# --- 7.2: backups --------------------------------------------------------------
def test_count_snapshot_is_not_called_a_completed_backup(w):
    sa = User(email="sa@finyl.app", hashed_password="x", full_name="SA", role="super_admin")
    w.db.add(sa)
    w.db.commit()
    r = w.api(sa).post("/api/v1/access/backup", headers={"X-Tenant-Id": str(w.A.id)})
    assert r.json().get("status") != "completed"
