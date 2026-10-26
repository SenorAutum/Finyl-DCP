"""
Regression tests for the tenant-isolation and authorization defects in the
Finyl-DCP system review (F01-F05, F11, F12) plus the fail-closed scope fix.

Unlike the legacy suite these go through the REAL HTTP routes with the REAL
permission dependencies (only the identity and DB session are injected), because
calling a handler function directly skips ``Depends(require_permission(...))``
and would therefore never have caught an authorization defect.

Every test asserts the CORRECTED behaviour: it fails on commit 8f4c5ca and passes
once the fixes are applied.
"""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.deps import UserScope, get_current_user
from app.core.ownership import OwnershipError
from app.models import (BankStatement, Borrower, Branch, Complaint, Loan, Region,
                        Staff, Tenant, TenantModule, User, UserDevice)
from app.services import bank_statement as bank_service

ADULT_DOB = "1990-01-01"
MINOR_DOB = date(date.today().year - 10, 1, 1).isoformat()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def world(seed, db):
    """Two tenants, several roles, and data on both sides of every boundary."""
    import app.main as main_mod

    A = seed.tenant
    for mod in ("complaints", "dashboard"):
        db.add(TenantModule(tenant_id=A.id, module_key=mod, enabled=True))

    # --- tenant A: a second officer + that officer's client -----------------
    staff2 = Staff(tenant_id=A.id, branch_id=seed.branch.id, name="Officer Two",
                   role="loan_officer")
    db.add(staff2)
    db.flush()
    borrower2 = Borrower(tenant_id=A.id, first_name="Peter", last_name="Otieno",
                         national_id="87654321", phone="0722000111",
                         branch_id=seed.branch.id, region_id=seed.region.id,
                         officer_staff_id=staff2.id)
    # an unowned client (officer_staff_id IS NULL) - the NULL-match canary
    orphan = Borrower(tenant_id=A.id, first_name="Orphan", last_name="Client",
                      national_id="11110000", phone="0733000999",
                      branch_id=seed.branch.id, region_id=seed.region.id,
                      officer_staff_id=None)
    db.add_all([borrower2, orphan])

    # --- tenant B -----------------------------------------------------------
    B = Tenant(name="Other DCP", code="ODCP")
    db.add(B)
    db.flush()
    for mod in ("lending", "complaints"):
        db.add(TenantModule(tenant_id=B.id, module_key=mod, enabled=True))
    regionB = Region(tenant_id=B.id, name="Mombasa")
    db.add(regionB)
    db.flush()
    branchB = Branch(tenant_id=B.id, region_id=regionB.id, name="Nyali")
    db.add(branchB)
    db.flush()
    staffB = Staff(tenant_id=B.id, branch_id=branchB.id, name="B Officer", role="loan_officer")
    db.add(staffB)
    db.flush()
    borrowerB = Borrower(tenant_id=B.id, first_name="Secret", last_name="Customer",
                         national_id="99999999", phone="0799123456",
                         branch_id=branchB.id, region_id=regionB.id,
                         officer_staff_id=staffB.id)
    db.add(borrowerB)
    db.flush()

    def mk(role, email, tenant=A, **kw):
        u = User(email=email, hashed_password="x", full_name=email.split("@")[0],
                 role=role, tenant_id=tenant.id, **kw)
        db.add(u)
        db.flush()
        return u

    users = SimpleNamespace(
        ro=mk("relationship_officer", "ro@a.dcp", staff_id=seed.staff.id,
              branch_id=seed.branch.id),
        ro_unlinked=mk("relationship_officer", "ro_unlinked@a.dcp"),
        ro2=mk("relationship_officer", "ro2@a.dcp", staff_id=staff2.id,
               branch_id=seed.branch.id),
        hq_ops=mk("hq_operations", "hq@a.dcp"),
        bm=mk("branch_manager", "bm@a.dcp", branch_id=seed.branch.id),
        sysadmin=mk("system_admin", "sys@a.dcp"),
        tadmin=mk("tenant_admin", "tadmin@a.dcp"),
        sysadmin_b=mk("system_admin", "sys@b.dcp", tenant=B),
        userB=mk("relationship_officer", "ro@b.dcp", tenant=B, staff_id=staffB.id),
    )
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app)

    yield SimpleNamespace(seed=seed, db=db, A=A, B=B, staff2=staff2, borrower2=borrower2,
                          orphan=orphan, borrowerB=borrowerB, staffB=staffB,
                          branchB=branchB, regionB=regionB, u=users, api=api)
    main_mod.app.dependency_overrides.clear()


def _active_loan(w, balance="11000.00"):
    return w.seed.make_loan(status="active", principal="10000.00",
                            outstanding_balance=balance)


# --------------------------------------------------------------------------- #
# F01 - generic transition must be permission-gated and balance-aware
# --------------------------------------------------------------------------- #
def test_f01_read_only_hq_cannot_transition_a_loan(world):
    loan = _active_loan(world)
    r = world.api(world.u.hq_ops).post(f"/api/v1/lending/loans/{loan.id}/transition",
                                       json={"status": "paid"})
    assert r.status_code == 403
    world.db.refresh(loan)
    assert loan.status == "active"


def test_f01_front_line_officer_cannot_transition_a_loan(world):
    loan = _active_loan(world)
    r = world.api(world.u.ro).post(f"/api/v1/lending/loans/{loan.id}/transition",
                                   json={"status": "defaulted"})
    assert r.status_code == 403


def test_f01_cannot_mark_paid_while_balance_outstanding(world):
    loan = _active_loan(world, balance="11000.00")
    r = world.api(world.u.bm).post(f"/api/v1/lending/loans/{loan.id}/transition",
                                   json={"status": "paid"})
    assert r.status_code == 409
    assert "outstanding balance" in r.json()["detail"]
    world.db.refresh(loan)
    assert loan.status == "active"


def test_f01_paid_is_accepted_at_zero_balance(world):
    loan = _active_loan(world, balance="0.00")
    r = world.api(world.u.bm).post(f"/api/v1/lending/loans/{loan.id}/transition",
                                   json={"status": "paid"})
    assert r.status_code == 200
    world.db.refresh(loan)
    assert loan.status == "paid"


def test_f01_authorised_role_can_still_mark_overdue(world):
    loan = _active_loan(world)
    r = world.api(world.u.bm).post(f"/api/v1/lending/loans/{loan.id}/transition",
                                   json={"status": "overdue"})
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# F02 - legacy /lending/borrowers must obey the canonical onboarding rules
# --------------------------------------------------------------------------- #
LEGACY = "/api/v1/lending/borrowers"


def _legacy_body(**over):
    body = {"first_name": "New", "last_name": "Client", "national_id": "55556666",
            "phone": "0711222333", "date_of_birth": ADULT_DOB}
    body.update(over)
    return body


def test_f02_legacy_create_requires_clients_create_permission(world):
    r = world.api(world.u.hq_ops).post(LEGACY, json=_legacy_body())
    assert r.status_code == 403


def test_f02_legacy_create_rejects_underage_applicant(world):
    r = world.api(world.u.ro).post(LEGACY, json=_legacy_body(date_of_birth=MINOR_DOB))
    assert r.status_code == 422
    assert world.db.query(Borrower).filter(Borrower.national_id_hash.isnot(None),
                                           Borrower.first_name == "New").count() == 0


def test_f02_legacy_create_is_pending_and_owned_by_the_officer(world):
    r = world.api(world.u.ro).post(LEGACY, json=_legacy_body())
    assert r.status_code == 200, r.text
    b = world.db.get(Borrower, r.json()["id"])
    assert b.profile_status == "pending_approval"      # NOT silently approved
    assert b.officer_staff_id == world.seed.staff.id


def test_f02_officer_without_staff_link_is_refused_on_both_routes(world):
    legacy = world.api(world.u.ro_unlinked).post(LEGACY, json=_legacy_body())
    canonical = world.api(world.u.ro_unlinked).post(
        "/api/v1/clients", json={"first_name": "X", "last_name": "Y",
                                 "national_id": "44443333", "phone": "0700000001",
                                 "date_of_birth": ADULT_DOB})
    assert legacy.status_code == 403
    assert canonical.status_code == 403          # previously: created as APPROVED


def test_f02_legacy_create_rejects_other_tenants_branch(world):
    r = world.api(world.u.ro).post(LEGACY, json=_legacy_body(branch_id=world.branchB.id))
    assert r.status_code == 404


def test_f02_legacy_update_respects_portfolio_scope(world):
    r = world.api(world.u.ro).put(f"{LEGACY}/{world.borrower2.id}",
                                  json=_legacy_body(national_id="87654321", phone="0722000111"))
    assert r.status_code == 403                  # borrower2 belongs to officer two


def test_f02_legacy_update_cannot_change_locked_fields_without_permission(world):
    own = world.seed.borrower
    r = world.api(world.u.ro).put(f"{LEGACY}/{own.id}",
                                  json=_legacy_body(first_name="Jane", last_name="Wanjiku",
                                                    national_id="12345678",
                                                    phone="0799999999"))   # phone is a locked field
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# F03 - complaints may only reference records inside the caller's tenant
# --------------------------------------------------------------------------- #
def test_f03_complaint_cannot_reference_other_tenants_client(world):
    r = world.api(world.u.bm).post("/api/v1/complaints",
                                   json={"borrower_id": world.borrowerB.id,
                                         "category": "other", "description": "x"})
    assert r.status_code == 404
    assert "Secret" not in r.text and "0799123456" not in r.text      # no PII echoed
    assert world.db.query(Complaint).filter(Complaint.tenant_id == world.A.id).count() == 0


def test_f03_complaint_cannot_be_assigned_to_other_tenants_staff(world):
    r = world.api(world.u.bm).post("/api/v1/complaints",
                                   json={"borrower_id": world.seed.borrower.id,
                                         "assigned_staff_id": world.staffB.id})
    assert r.status_code == 404


def test_f03_same_tenant_complaint_still_works(world):
    r = world.api(world.u.bm).post("/api/v1/complaints",
                                   json={"borrower_id": world.seed.borrower.id,
                                         "category": "other", "description": "late SMS"})
    assert r.status_code == 200, r.text
    assert r.json()["borrower_name"]


# --------------------------------------------------------------------------- #
# F04 - device registry must not cross tenants
# --------------------------------------------------------------------------- #
def test_f04_tenant_admin_cannot_register_device_for_other_tenants_user(world):
    r = world.api(world.u.sysadmin).post(f"/api/v1/users/{world.u.userB.id}/devices",
                                         json={"device_fingerprint": "evil-fp"})
    assert r.status_code == 404
    assert world.db.query(UserDevice).filter(UserDevice.user_id == world.u.userB.id).count() == 0


def test_f04_tenant_admin_cannot_list_or_revoke_other_tenants_devices(world):
    d = UserDevice(user_id=world.u.userB.id, device_fingerprint="legit-fp")
    world.db.add(d)
    world.db.commit()
    api = world.api(world.u.sysadmin)
    assert api.get(f"/api/v1/users/{world.u.userB.id}/devices").status_code == 404
    assert api.delete(f"/api/v1/users/{world.u.userB.id}/devices/{d.id}").status_code == 404
    world.db.refresh(d)
    assert d.active is not False


def test_f04_in_tenant_administration_and_self_service_still_work(world):
    ok_admin = world.api(world.u.sysadmin).post(
        f"/api/v1/users/{world.u.ro.id}/devices", json={"device_fingerprint": "fp-1"})
    ok_self = world.api(world.u.ro2).post(
        f"/api/v1/users/{world.u.ro2.id}/devices", json={"device_fingerprint": "fp-2"})
    denied = world.api(world.u.ro2).post(
        f"/api/v1/users/{world.u.ro.id}/devices", json={"device_fingerprint": "fp-3"})
    assert ok_admin.status_code == 200 and ok_self.status_code == 200
    assert denied.status_code == 403


# --------------------------------------------------------------------------- #
# F05 - statement ingestion must verify the target client's tenant
# --------------------------------------------------------------------------- #
def test_f05_service_refuses_client_from_another_tenant(world):
    with pytest.raises(OwnershipError):
        bank_service.ingest(world.db, tenant_id=world.A.id, client_id=world.borrowerB.id,
                            bank_name="KCB", transactions=[])
    assert world.db.query(BankStatement).count() == 0


def test_f05_authenticated_route_refuses_cross_tenant_client(world):
    r = world.api(world.u.ro).post("/api/v1/collections/bank-statement",
                                   json={"client_id": world.borrowerB.id, "bank_name": "KCB",
                                         "transactions": []})
    assert r.status_code == 404
    assert world.db.query(BankStatement).count() == 0


# --------------------------------------------------------------------------- #
# F11 - loan application: scope + officer attribution
# --------------------------------------------------------------------------- #
APPLY = "/api/v1/lending/loans/apply"


def _apply(world, user, borrower, **over):
    body = {"borrower_id": borrower.id, "product_id": world.seed.product.id,
            "principal": 5000}
    body.update(over)
    return world.api(user).post(APPLY, json=body)


def test_f11_officer_cannot_apply_for_another_officers_client(world):
    r = _apply(world, world.u.ro, world.borrower2)
    assert r.status_code == 403
    assert world.db.query(Loan).filter(Loan.borrower_id == world.borrower2.id).count() == 0


def test_f11_officer_cannot_book_a_loan_against_another_officer(world):
    r = _apply(world, world.u.ro, world.seed.borrower, staff_id=world.staff2.id)
    assert r.status_code == 403


def test_f11_officer_loan_is_attributed_to_the_officer(world):
    r = _apply(world, world.u.ro, world.seed.borrower)
    assert r.status_code == 200, r.text
    assert world.db.get(Loan, r.json()["id"]).staff_id == world.seed.staff.id


def test_f11_cross_tenant_client_is_not_found(world):
    assert _apply(world, world.u.ro, world.borrowerB).status_code == 404


def test_f11_admin_cannot_name_staff_from_another_tenant(world):
    r = _apply(world, world.u.tadmin, world.seed.borrower, staff_id=world.staffB.id)
    assert r.status_code == 404


def test_f11_admin_may_name_an_in_tenant_officer(world):
    r = _apply(world, world.u.tadmin, world.seed.borrower, staff_id=world.staff2.id)
    assert r.status_code == 200, r.text
    assert world.db.get(Loan, r.json()["id"]).staff_id == world.staff2.id


def test_f11_unlinked_officer_cannot_apply(world):
    assert _apply(world, world.u.ro_unlinked, world.seed.borrower).status_code == 403


# --------------------------------------------------------------------------- #
# F12 + fail-closed scope
# --------------------------------------------------------------------------- #
def test_f12_unlinked_officer_gets_an_empty_portfolio_not_the_company(world):
    _active_loan(world)
    r = world.api(world.u.ro_unlinked).get("/api/v1/dashboard/my-portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["clients"] == []
    assert body["kpis"]["clients_total"] == 0 and body["kpis"]["loans_total"] == 0


def test_f12_linked_officer_sees_only_own_book(world):
    _active_loan(world)
    r = world.api(world.u.ro).get("/api/v1/dashboard/my-portfolio")
    ids = {c["id"] for c in r.json()["clients"]}
    assert ids == {world.seed.borrower.id}


def test_f12_company_wide_role_still_sees_the_whole_tenant(world):
    r = world.api(world.u.hq_ops).get("/api/v1/dashboard/my-portfolio")
    ids = {c["id"] for c in r.json()["clients"]}
    assert {world.seed.borrower.id, world.borrower2.id, world.orphan.id} <= ids
    assert world.borrowerB.id not in ids


def test_scope_null_staff_never_matches_unowned_rows(world):
    """`officer_staff_id == NULL` renders as IS NULL: an unlinked officer used to
    be able to see every client that nobody owns."""
    scope = UserScope(world.u.ro_unlinked, world.db)
    q = scope.apply_client(world.db.query(Borrower).filter(Borrower.tenant_id == world.A.id),
                           Borrower)
    assert q.count() == 0
    assert scope.can_see_client(world.orphan) is False
    assert scope.resolvable is False


def test_scope_unknown_role_fails_closed(world):
    custom = User(email="x@a.dcp", hashed_password="x", full_name="x",
                  role="some_custom_role", tenant_id=world.A.id)
    world.db.add(custom)
    world.db.flush()
    scope = UserScope(custom, world.db)
    assert scope.can_see_client(world.seed.borrower) is False
    assert scope.apply_client(world.db.query(Borrower), Borrower).count() == 0
