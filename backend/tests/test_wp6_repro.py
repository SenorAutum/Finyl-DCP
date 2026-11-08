"""
WP6 reproductions of the review's KYC and onboarding findings (F13-F22),
written against the code as merged on main (8ed069f). Each test asserts the
CORRECT behaviour, so on the old code it fails and shows the defect; after WP6
it passes and guards against it.

They go through the real HTTP routes with the real permission dependencies
(only the identity and DB session are injected), like test_isolation_authz.
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import (Borrower, ClientConsentLog, ClientEditRequest,
                        ClientMobileWallet, CrbCheck, TenantModule,
                        TenantValidationPrefs, User)
from app.services import kyc_decisions

ADULT_DOB = date(1990, 1, 1)


@pytest.fixture()
def w(seed, db):
    import app.main as main_mod

    A = seed.tenant

    def mk(role, email, **kw):
        u = User(email=email, hashed_password="x", full_name=email.split("@")[0],
                 role=role, tenant_id=A.id, **kw)
        db.add(u)
        db.flush()
        return u

    users = SimpleNamespace(
        ro=mk("relationship_officer", "ro@a.dcp", staff_id=seed.staff.id,
              branch_id=seed.branch.id),
        bm=mk("branch_manager", "bm@a.dcp", branch_id=seed.branch.id),
        bm2=mk("branch_manager", "bm2@a.dcp", branch_id=seed.branch.id),
        rm=mk("regional_manager", "rm@a.dcp", region_id=seed.region.id),
    )
    b = seed.borrower
    b.date_of_birth = ADULT_DOB
    b.profile_status = "approved"
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app)

    yield SimpleNamespace(seed=seed, db=db, A=A, b=b, u=users, api=api)
    main_mod.app.dependency_overrides.clear()


def _prefs(db, tenant_id, **kw):
    p = db.query(TenantValidationPrefs).filter_by(tenant_id=tenant_id).first()
    if not p:
        p = TenantValidationPrefs(tenant_id=tenant_id)
        db.add(p)
    for k, v in kw.items():
        setattr(p, k, v)
    db.commit()
    return p


# --------------------------------------------------------------------------- #
# F13 - edit tier chosen by the caller; maker can approve own request
# --------------------------------------------------------------------------- #
def test_f13_phone_change_cannot_be_filed_as_secondary(w):
    r = w.api(w.u.ro).post(f"/api/v1/clients/{w.b.id}/edit-request", json={
        "edit_tier": "secondary",
        "field_changes": {"phone": {"old_value": w.b.phone, "new_value": "0799000111"}}})
    assert r.status_code == 200, r.text
    assert r.json()["edit_tier"] == "primary"


def test_f13_requester_cannot_approve_own_edit(w):
    r = w.api(w.u.bm).post(f"/api/v1/clients/{w.b.id}/edit-request", json={
        "field_changes": {"business_sector": {"old_value": None, "new_value": "retail"}}})
    rid = r.json()["id"]
    r = w.api(w.u.bm).post(f"/api/v1/clients/edit-requests/{rid}/approve", json={})
    assert r.status_code == 403
    assert w.db.get(ClientEditRequest, rid).status == "pending"


# --------------------------------------------------------------------------- #
# F14 - locked wallet can be replaced through the ordinary update
# --------------------------------------------------------------------------- #
def test_f14_locked_wallet_number_cannot_be_swapped_by_update(w):
    wal = ClientMobileWallet(tenant_id=w.A.id, client_id=w.b.id, mobile_number="0712345678",
                             operator="M-Pesa", active=True, wallet_locked=True)
    w.db.add(wal)
    w.db.commit()
    body = {"first_name": w.b.first_name, "last_name": w.b.last_name,
            "national_id": w.b.national_id, "phone": w.b.phone,
            "date_of_birth": ADULT_DOB.isoformat(),
            "wallets": [{"id": wal.id, "mobile_number": "0799999999",
                         "operator": "M-Pesa", "active": True}],
            "next_of_kin": []}
    r = w.api(w.u.bm).put(f"/api/v1/clients/{w.b.id}", json=body)
    assert r.status_code == 409, r.text
    w.db.refresh(wal)
    assert wal.mobile_number == "0712345678"


# --------------------------------------------------------------------------- #
# F15 / F16 - format checks presented as verified ownership
# --------------------------------------------------------------------------- #
def test_f15_mpesa_check_does_not_echo_the_expected_name(w):
    r = w.api(w.u.ro).post("/api/v1/clients/validate-mpesa", json={
        "client_id": w.b.id, "expected_name": "ANY NAME I LIKE"})
    assert r.status_code == 200, r.text
    assert r.json()["matched"] is not True
    assert r.json()["registered_name"] != "ANY NAME I LIKE"
    w.db.refresh(w.b)
    assert not w.b.mpesa_validated


def test_f16_alt_phone_is_not_validated_without_a_name_lookup(w):
    w.b.alt_phone = "0722000333"
    w.db.commit()
    r = w.api(w.u.ro).post(f"/api/v1/clients/{w.b.id}/validate-alt-phone")
    assert r.status_code == 200, r.text
    assert r.json()["alt_phone_validated"] is False
    w.db.refresh(w.b)
    assert not w.b.alt_phone_validated


# --------------------------------------------------------------------------- #
# F17 / F18 / F19 - the central decision
# --------------------------------------------------------------------------- #
def _kyc_ready(w):
    """Everything the default KYC settings need (self-contained so this file
    also runs against the pre-WP6 code)."""
    from app.models import ClientDocument, ClientNextOfKin
    b = w.b
    b.mpesa_validated = True
    b.mpesa_validation_method = "manual"          # WP6 columns (ignored before)
    b.mpesa_validation_phone = "254712345678"
    w.db.add(ClientDocument(tenant_id=w.A.id, client_id=b.id, doc_type="national_id_front",
                            file_name="id.jpg", ocr_applied=True,
                            ocr_field_mapping={"national_id": b.national_id}))
    w.db.add(ClientNextOfKin(tenant_id=w.A.id, client_id=b.id, full_name="Kin",
                             relationship_type="Spouse", active=True))
    from app.models import KycConsent            # required since WP7
    w.db.add(KycConsent(tenant_id=w.A.id, borrower_id=b.id, consent_data_processing=True))
    w.db.commit()


def test_f17_mandatory_crb_does_not_crash_and_needs_a_good_result(w):
    _prefs(w.db, w.A.id, crb_check_mandatory=True, ocr_mandatory=False)
    _kyc_ready(w)
    w.db.add(CrbCheck(tenant_id=w.A.id, client_id=w.b.id, provider="metropol",
                      status="error", error="timeout"))
    w.db.commit()
    d = kyc_decisions.evaluate(w.db, tenant_id=w.A.id, client_id=w.b.id)
    assert d.decision == "fail"


def test_f18_mandatory_ocr_is_enforced(w):
    _prefs(w.db, w.A.id, ocr_mandatory=True)
    _kyc_ready(w)                           # everything in order...
    from app.models import ClientDocument
    w.db.query(ClientDocument).delete()     # ...except no ID has been read
    w.db.commit()
    d = kyc_decisions.evaluate(w.db, tenant_id=w.A.id, client_id=w.b.id)
    assert d.decision != "pass"


def test_f19_failed_ekyc_cannot_coexist_with_pass(w):
    _prefs(w.db, w.A.id, ocr_mandatory=False)
    _kyc_ready(w)
    w.b.ekyc_status = "not_verified"
    w.db.commit()
    d = kyc_decisions.evaluate(w.db, tenant_id=w.A.id, client_id=w.b.id)
    assert d.decision == "fail"


# --------------------------------------------------------------------------- #
# F20 - decision not enforced at profile approval / loan application
# --------------------------------------------------------------------------- #
def test_f20_client_failing_kyc_cannot_be_profile_approved(w):
    w.b.profile_status = "pending_approval"
    w.db.commit()                                     # mpesa never validated -> fail
    r = w.api(w.u.bm).post(f"/api/v1/approvals/clients/{w.b.id}", json={"action": "approve"})
    assert r.status_code in (409, 422), r.text
    w.db.refresh(w.b)
    assert w.b.profile_status == "pending_approval"


def test_f20_approved_client_failing_kyc_cannot_apply(w):
    r = w.api(w.u.ro).post("/api/v1/lending/loans/apply", json={
        "borrower_id": w.b.id, "product_id": w.seed.product.id, "principal": 5000})
    assert r.status_code in (409, 422), r.text


def test_f20_second_open_loan_is_refused(w):
    _prefs(w.db, w.A.id, ocr_mandatory=False)
    _kyc_ready(w)
    w.seed.make_loan(status="active", outstanding_balance="5000.00")
    w.db.commit()
    r = w.api(w.u.ro).post("/api/v1/lending/loans/apply", json={
        "borrower_id": w.b.id, "product_id": w.seed.product.id, "principal": 5000})
    assert r.status_code == 409, r.text
    assert "open loan" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# F21 - login OTP never delivered
# --------------------------------------------------------------------------- #
def test_f21_login_otp_is_sent_by_sms_to_the_user(w, monkeypatch):
    from app.services import otp, sms
    sent = []
    monkeypatch.setattr(sms, "_dispatch_to_provider",
                        lambda phone, msg: sent.append(phone) or {"status": "sent"})
    user = w.u.ro
    user.phone = "0711000222"
    w.db.commit()
    out = otp.deliver(w.db, user, "123456", channels=["sms"])
    assert out["channels"].get("sms") is True
    assert sent


# --------------------------------------------------------------------------- #
# F22 - consent code not bound to the phone it was sent to
# --------------------------------------------------------------------------- #
def test_f22_consent_code_sent_to_a_cannot_verify_b(w, monkeypatch):
    from app.services import otp, sms
    codes = []
    real_generate = otp.generate

    def spy(*a, **k):
        c = real_generate(*a, **k)
        codes.append(c)
        return c
    monkeypatch.setattr(otp, "generate", spy)
    monkeypatch.setattr(sms, "_dispatch_to_provider", lambda phone, msg: {"status": "sent"})
    api = w.api(w.u.ro)
    r = api.post("/api/v1/clients/request-consent-otp",
                 json={"phone": "0712345678", "client_id": w.b.id})
    assert r.status_code == 200, r.text
    r = api.post("/api/v1/clients/verify-consent-otp",
                 json={"code": codes[-1], "phone": "0799888777", "client_id": w.b.id})
    assert r.status_code == 422, r.text
    assert w.db.query(ClientConsentLog).count() == 0
