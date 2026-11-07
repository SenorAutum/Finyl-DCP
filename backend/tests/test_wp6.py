"""
WP6 - KYC and onboarding (F13-F22): the corrected behaviour in more detail
than the reproductions in test_wp6_repro.py. Real HTTP routes, real
permission dependencies; only identity and DB session are injected.
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import hash_password
from app.models import (Borrower, ClientConsentLog, ClientDocument, ClientEditRequest,
                        ClientMobileWallet, CrbCheck, Guarantor, KycConsent,
                        KycMismatchEscalation, Loan, OtpToken, SmsLog,
                        TenantModule, TenantSecurityConfig, TenantValidationPrefs, User)
from app.services import kyc_decisions

ADULT_DOB = date(1990, 1, 1)
_PW_HASH = None


def _pw_hash():
    global _PW_HASH
    if _PW_HASH is None:
        _PW_HASH = hash_password("Passw0rd!x")
    return _PW_HASH


@pytest.fixture()
def w(seed, db, monkeypatch):
    import app.main as main_mod
    from app.routers import auth as auth_mod
    from app.services import sms

    A = seed.tenant

    def mk(role, email, **kw):
        u = User(email=email, hashed_password=_pw_hash(),
                 full_name=email.split("@")[0], role=role, tenant_id=A.id, **kw)
        db.add(u)
        db.flush()
        return u

    users = SimpleNamespace(
        ro=mk("relationship_officer", "ro@a.dcp", staff_id=seed.staff.id,
              branch_id=seed.branch.id),
        bm=mk("branch_manager", "bm@a.dcp", branch_id=seed.branch.id),
        bm2=mk("branch_manager", "bm2@a.dcp", branch_id=seed.branch.id),
        hq=mk("hq_operations", "hq@a.dcp"),
        sysadmin=mk("system_admin", "sys@a.dcp"),
        tadmin=mk("tenant_admin", "tadmin@a.dcp"),
    )
    sa = User(email="sa@finyl.app", hashed_password="x", full_name="Super", role="super_admin")
    db.add(sa)
    db.flush()
    users.sa = sa
    b = seed.borrower
    b.date_of_birth = ADULT_DOB
    b.branch_id = seed.branch.id
    b.profile_status = "approved"
    db.commit()

    sent = []
    monkeypatch.setattr(sms, "_dispatch_to_provider",
                        lambda phone, msg: sent.append((phone, msg)) or {"status": "sent"})

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        main_mod.app.dependency_overrides[auth_mod.rate_limit_login] = lambda: None
        return TestClient(main_mod.app)

    yield SimpleNamespace(seed=seed, db=db, A=A, b=b, u=users, api=api, sent=sent)
    main_mod.app.dependency_overrides.clear()


def _prefs(w, **kw):
    p = w.db.query(TenantValidationPrefs).filter_by(tenant_id=w.A.id).first()
    if not p:
        p = TenantValidationPrefs(tenant_id=w.A.id)
        w.db.add(p)
    for k, v in kw.items():
        setattr(p, k, v)
    w.db.commit()
    return p


def _decide(w):
    return kyc_decisions.evaluate(w.db, tenant_id=w.A.id, client_id=w.b.id)


def _edit(w, user, changes, **extra):
    return w.api(user).post(f"/api/v1/clients/{w.b.id}/edit-request",
                            json={"field_changes": changes, **extra})


# =========================================================================== #
# F13 - edit requests
# =========================================================================== #
def test_f13_unknown_and_invalid_fields_are_refused(w):
    assert _edit(w, w.u.ro, {"credit_score": {"new_value": 900}}).status_code == 422
    assert _edit(w, w.u.ro, {"address": {"new_value": "Thika"}}).status_code == 422
    assert _edit(w, w.u.ro, {"phone": {"new_value": "12345"}}).status_code == 422
    assert _edit(w, w.u.ro, {"date_of_birth": {"new_value": "2015-01-01"}}).status_code == 422
    assert _edit(w, w.u.ro, {"phone": {"new_value": w.b.phone}}).status_code == 422  # no change


def test_f13_old_value_comes_from_the_record_not_the_caller(w):
    r = _edit(w, w.u.ro, {"phone": {"old_value": "0700000000", "new_value": "0799000111"}})
    assert r.status_code == 200, r.text
    assert r.json()["field_changes"]["phone"]["old_value"] == w.b.phone


def test_f13_caller_may_raise_but_not_lower_the_tier(w):
    r = _edit(w, w.u.ro, {"business_sector": {"new_value": "retail"}}, edit_tier="primary")
    assert r.json()["edit_tier"] == "primary"


def test_f13_primary_edit_needs_two_different_approvers(w):
    w.seed.make_kyc_ready(w.b)
    assert kyc_decisions.mpesa_evidence_current(w.b)
    rid = _edit(w, w.u.ro, {"phone": {"new_value": "0799000111"}}).json()["id"]

    r = w.api(w.u.bm).post(f"/api/v1/clients/edit-requests/{rid}/approve", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending" and r.json()["next_stage"] == "final"
    w.db.refresh(w.b)
    assert w.b.phone == "0712345678"                       # not applied yet

    r = w.api(w.u.bm).post(f"/api/v1/clients/edit-requests/{rid}/approve", json={})
    assert r.status_code == 403                            # same person twice

    r = w.api(w.u.bm2).post(f"/api/v1/clients/edit-requests/{rid}/approve", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    w.db.refresh(w.b)
    assert w.b.phone == "0799000111"
    # the M-Pesa confirmation was for the OLD number, so it no longer counts
    assert not w.b.mpesa_validated
    assert "mpesa_not_validated" in _decide(w).reasons


def test_f13_secondary_edit_is_one_approval_and_officer_cannot_approve(w):
    rid = _edit(w, w.u.bm, {"business_sector": {"new_value": "retail"}}).json()["id"]
    assert w.api(w.u.ro).post(f"/api/v1/clients/edit-requests/{rid}/approve",
                              json={}).status_code == 403
    r = w.api(w.u.bm2).post(f"/api/v1/clients/edit-requests/{rid}/approve", json={})
    assert r.json()["status"] == "approved"
    w.db.refresh(w.b)
    assert w.b.business_sector == "retail"


def test_f13_requester_cannot_reject_own_request(w):
    rid = _edit(w, w.u.bm, {"business_sector": {"new_value": "retail"}}).json()["id"]
    r = w.api(w.u.bm).post(f"/api/v1/clients/edit-requests/{rid}/reject",
                           json={"rejection_reason": "changed my mind"})
    assert r.status_code == 403


def test_f13_identity_change_clears_ekyc(w):
    w.b.ekyc_status = "verified"
    w.db.commit()
    rid = _edit(w, w.u.ro, {"last_name": {"new_value": "Kamau"}}).json()["id"]
    w.api(w.u.bm).post(f"/api/v1/clients/edit-requests/{rid}/approve", json={})
    w.api(w.u.bm2).post(f"/api/v1/clients/edit-requests/{rid}/approve", json={})
    w.db.refresh(w.b)
    assert w.b.last_name == "Kamau" and w.b.ekyc_status is None


# =========================================================================== #
# F14 - wallet locks and record locks
# =========================================================================== #
def _wallet(w, locked=True, number="0712345678"):
    wal = ClientMobileWallet(tenant_id=w.A.id, client_id=w.b.id, mobile_number=number,
                             operator="M-Pesa", active=True, wallet_locked=locked)
    w.db.add(wal)
    w.db.commit()
    return wal


def _put_body(w, wallets):
    return {"first_name": w.b.first_name, "last_name": w.b.last_name,
            "national_id": w.b.national_id, "phone": w.b.phone,
            "date_of_birth": ADULT_DOB.isoformat(), "branch_id": w.seed.branch.id,
            "wallets": wallets, "next_of_kin": []}


def test_f14_locked_wallet_cannot_be_dropped_from_the_update(w):
    wal = _wallet(w)
    r = w.api(w.u.bm).put(f"/api/v1/clients/{w.b.id}", json=_put_body(w, []))
    assert r.status_code == 409
    assert w.db.get(ClientMobileWallet, wal.id) is not None


def test_f14_unchanged_locked_wallet_saves_fine(w):
    wal = _wallet(w)
    r = w.api(w.u.bm).put(f"/api/v1/clients/{w.b.id}", json=_put_body(w, [
        {"id": wal.id, "mobile_number": "+254 712 345 678", "operator": "M-Pesa",
         "active": True}]))
    assert r.status_code == 200, r.text


def test_f14_direct_delete_of_locked_wallet_is_refused(w):
    wal = _wallet(w)
    assert w.api(w.u.bm).delete(f"/api/v1/clients/{w.b.id}/wallets/{wal.id}").status_code == 409
    free = _wallet(w, locked=False, number="0722000000")
    assert w.api(w.u.hq).delete(f"/api/v1/clients/{w.b.id}/wallets/{free.id}").status_code == 403
    assert w.api(w.u.bm).delete(f"/api/v1/clients/{w.b.id}/wallets/{free.id}").status_code == 200


def test_f14_names_frozen_while_a_loan_is_open(w):
    w.seed.make_loan(status="approved")                 # payout due -> record locked
    w.db.commit()
    body = _put_body(w, [])
    body["last_name"] = "Changed"
    r = w.api(w.u.bm).put(f"/api/v1/clients/{w.b.id}", json=body)
    assert r.status_code == 409


# =========================================================================== #
# F15 / F16 - M-Pesa confirmation by a second person
# =========================================================================== #
def _confirm(w, user, name="JANE WANJIKU",
             evidence="KES 1 send-money test 28 Oct, M-Pesa showed JANE WANJIKU"):
    return w.api(user).post(f"/api/v1/clients/{w.b.id}/confirm-mpesa",
                            json={"registered_name": name, "evidence": evidence,
                                  "phone": w.b.phone})


def test_f15_officer_and_creator_cannot_confirm(w):
    assert _confirm(w, w.u.ro).status_code == 403             # no clients.approve
    w.b.created_by_user_id = w.u.bm.id
    w.db.commit()
    assert _confirm(w, w.u.bm).status_code == 403             # captured it


def test_f15_name_must_match_and_evidence_is_required(w):
    assert _confirm(w, w.u.bm2, name="PETER OTIENO").status_code == 422
    assert _confirm(w, w.u.bm2, evidence="ok").status_code == 422
    w.db.refresh(w.b)
    assert not w.b.mpesa_validated


def test_f15_confirmation_records_evidence_and_locks_the_wallet(w):
    wal = _wallet(w, locked=False)
    r = _confirm(w, w.u.bm2)
    assert r.status_code == 200, r.text
    assert r.json()["wallets_locked"] == [wal.id]
    w.db.refresh(w.b)
    assert w.b.mpesa_validated and w.b.mpesa_validation_method == "manual"
    assert w.b.mpesa_validated_by == w.u.bm2.id
    assert w.b.mpesa_validation_phone == "254712345678"
    assert kyc_decisions.mpesa_evidence_current(w.b)
    # the format check alone never marks anything validated, and never echoes
    r = w.api(w.u.ro).post("/api/v1/clients/validate-mpesa", json={
        "client_id": w.b.id, "expected_name": "SOMEONE ELSE"})
    assert r.json()["verified"] is False and r.json()["registered_name"] is None
    assert r.json()["already_confirmed"] is True


def test_f15_validation_endpoints_need_permission_and_scope(w):
    r = w.api(w.u.hq).post("/api/v1/clients/validate-mpesa", json={"client_id": w.b.id})
    assert r.status_code == 403
    r = w.api(w.u.hq).post("/api/v1/clients/ekyc/verify", json={"client_id": w.b.id})
    assert r.status_code == 403


# =========================================================================== #
# F17 / F18 / F19 - central decision
# =========================================================================== #
def test_decision_passes_with_full_evidence(w):
    w.seed.make_kyc_ready(w.b)
    d = _decide(w)
    assert d.decision == "pass", d.reasons


def test_f17_crb_must_be_successful_and_current(w):
    w.seed.make_kyc_ready(w.b)
    _prefs(w, crb_check_mandatory=True)
    assert _decide(w).reasons == ["crb_not_run"]
    w.db.add(CrbCheck(tenant_id=w.A.id, client_id=w.b.id, status="error"))
    w.db.commit()
    assert _decide(w).reasons == ["crb_failed"]
    old = CrbCheck(tenant_id=w.A.id, client_id=w.b.id, status="ok", credit_score=600,
                   created_at=datetime.utcnow() - timedelta(days=120))
    w.db.add(old)
    w.db.commit()
    assert _decide(w).reasons == ["crb_out_of_date"]
    w.db.add(CrbCheck(tenant_id=w.A.id, client_id=w.b.id, status="ok", credit_score=600))
    w.db.commit()
    assert _decide(w).decision == "pass"
    _prefs(w, min_credit_score=650)
    assert _decide(w).reasons == ["crb_score_below_minimum"]
    _prefs(w, min_credit_score=None, crb_max_age_days=30)   # DCP's own validity window
    w.db.query(CrbCheck).filter(CrbCheck.created_at > datetime.utcnow() - timedelta(days=1)) \
        .update({CrbCheck.created_at: datetime.utcnow() - timedelta(days=45)})
    w.db.commit()
    assert _decide(w).reasons == ["crb_out_of_date"]


def test_f18_ocr_number_must_match(w):
    w.seed.make_kyc_ready(w.b)
    doc = w.db.query(ClientDocument).filter_by(client_id=w.b.id).first()
    doc.ocr_field_mapping = {"national_id": "99999999"}
    w.db.commit()
    assert _decide(w).reasons == ["ocr_id_number_mismatch"]
    doc.ocr_field_mapping = {"national_id": None}
    w.db.commit()
    d = _decide(w)
    assert d.decision == "escalate" and d.reasons == ["ocr_id_number_unreadable"]


def test_f18_unsupported_custom_rule_is_rejected_not_ignored(w):
    w.seed.make_kyc_ready(w.b)
    _prefs(w, custom_rules={"must_own_land": True})
    assert _decide(w).reasons == ["custom_rule_unsupported:must_own_land"]
    r = w.api(w.u.sysadmin).put("/api/v1/settings/validation-prefs",
                                json={"custom_rules": {"must_own_land": True}})
    assert r.status_code == 422
    r = w.api(w.u.sysadmin).put("/api/v1/settings/validation-prefs",
                                json={"custom_rules": {"require_documents": ["kra_pin"]},
                                      "ekyc_mandatory": True})
    assert r.status_code == 200, r.text
    assert set(_decide(w).reasons) == {"document_missing:kra_pin", "ekyc_not_run"}


def test_f18_ocr_endpoint_reads_only_id_documents(w):
    doc = ClientDocument(tenant_id=w.A.id, client_id=w.b.id, doc_type="payslip",
                         file_name="p.pdf")
    w.db.add(doc)
    w.db.commit()
    r = w.api(w.u.ro).post(f"/api/v1/clients/{w.b.id}/documents/{doc.id}/ocr")
    assert r.status_code == 422


def test_f19_ekyc_outcomes(w):
    w.seed.make_kyc_ready(w.b)
    for status, decision in (("not_verified", "fail"), ("error", "pass"),
                             ("verified", "pass")):
        w.b.ekyc_status = status
        w.db.commit()
        assert _decide(w).decision == decision, status
    _prefs(w, ekyc_mandatory=True)
    w.b.ekyc_status = "error"
    w.db.commit()
    assert _decide(w).reasons == ["ekyc_error"]


def test_f19_ekyc_for_a_saved_client_uses_the_saved_record(w):
    r = w.api(w.u.ro).post("/api/v1/clients/ekyc/verify",
                           json={"client_id": w.b.id, "first_name": "Someone",
                                 "last_name": "Else", "national_id": "11112222"})
    assert r.status_code == 422


# =========================================================================== #
# F20 - enforced at approval and at application
# =========================================================================== #
def test_f20_new_profiles_are_never_auto_approved(w):
    # tenant_admin holds both clients.create and clients.approve: before WP6 its
    # profiles were created already approved, with no KYC decision.
    r = w.api(w.u.tadmin).post("/api/v1/clients", json={
        "first_name": "New", "last_name": "Person", "national_id": "55556666",
        "phone": "0711222333", "date_of_birth": ADULT_DOB.isoformat(),
        "branch_id": w.seed.branch.id})
    assert r.status_code == 200, r.text
    c = w.db.get(Borrower, r.json()["id"])
    assert c.profile_status == "pending_approval" and c.created_by_user_id == w.u.tadmin.id
    # ...and its creator cannot approve it
    r = w.api(w.u.tadmin).post(f"/api/v1/approvals/clients/{c.id}", json={"action": "approve"})
    assert r.status_code == 403


def test_f20_profile_approval_runs_the_decision(w):
    w.b.profile_status = "pending_approval"
    w.db.commit()
    w.seed.make_kyc_ready(w.b)
    w.db.add(KycMismatchEscalation(tenant_id=w.A.id, client_id=w.b.id,
                                   mismatch_type="name_mismatch", logged_by=w.u.ro.id,
                                   status="open"))
    w.db.commit()
    r = w.api(w.u.bm).post(f"/api/v1/approvals/clients/{w.b.id}", json={"action": "approve"})
    assert r.status_code == 409 and "manual review" in r.json()["detail"]
    r = w.api(w.u.bm).post(f"/api/v1/approvals/clients/{w.b.id}",
                           json={"action": "approve", "note": "Checked ID in person"})
    assert r.status_code == 200, r.text
    w.db.refresh(w.b)
    assert w.b.profile_status == "approved" and w.b.kyc_status == "validated"


def _apply(w, **over):
    body = {"borrower_id": w.b.id, "product_id": w.seed.product.id, "principal": 5000}
    body.update(over)
    return w.api(w.u.ro).post("/api/v1/lending/loans/apply", json=body)


def test_f20_product_rules_at_application(w):
    w.seed.make_kyc_ready(w.b)
    p = w.seed.product
    p.min_age = 40
    w.db.commit()
    r = _apply(w)
    assert r.status_code == 422 and "at least 40" in r.json()["detail"]
    p.min_age, p.requires_guarantor = 18, True
    w.db.commit()
    assert "guarantor" in _apply(w).json()["detail"]
    w.db.add(Guarantor(tenant_id=w.A.id, client_id=w.b.id, full_name="G", kyc_status="validated"))
    w.db.commit()
    r = _apply(w)
    assert r.status_code == 200, r.text
    # one open loan at a time unless the product allows more
    assert _apply(w).status_code == 409
    p.rules = dict(p.rules or {}, allow_concurrent_loans=True)
    w.db.commit()
    assert _apply(w).status_code != 409


def test_f20_loan_approval_rechecks_kyc(w):
    w.seed.make_kyc_ready(w.b)
    loan = _apply(w).json()
    w.b.phone = "0799000111"                 # number changed -> confirmation lapses
    w.db.commit()
    r = w.api(w.u.bm).post(f"/api/v1/approvals/loans/{loan['id']}", json={"action": "approve"})
    assert r.status_code == 409 and "KYC" in r.json()["detail"]
    assert w.db.get(Loan, loan["id"]).status == "pending"


# =========================================================================== #
# F21 - staff sign-in codes
# =========================================================================== #
def _require_otp(w, channels=("sms",)):
    w.db.add(TenantSecurityConfig(tenant_id=w.A.id, require_otp=True, otp_channels=list(channels)))
    w.db.commit()


def _login(w, user):
    return w.api(user).post("/api/v1/auth/login",
                            json={"email": user.email, "password": "Passw0rd!x"})


def test_f21_no_phone_means_a_clear_refusal_not_a_dead_end(w):
    _require_otp(w)
    r = _login(w, w.u.ro)
    assert r.status_code == 503 and "phone number" in r.json()["detail"]
    assert w.db.query(OtpToken).filter_by(user_id=w.u.ro.id, consumed=False).count() == 0


def test_f21_code_is_sent_to_the_staff_phone_and_not_logged(w):
    _require_otp(w)
    w.u.ro.phone = "0711000222"
    w.db.commit()
    r = _login(w, w.u.ro)
    assert r.status_code == 200 and r.json()["otp_required"] is True
    assert r.json()["delivery"]["channels"] == ["sms"]
    phone, msg = w.sent[-1]
    assert phone == "0711000222"
    code = msg.split("code is ")[1][:6]
    log = w.db.query(SmsLog).filter_by(trigger_type="otp").one()
    assert code not in log.message and "******" in log.message
    r = w.api(w.u.ro).post("/api/v1/auth/login/otp", json={"email": w.u.ro.email, "code": code})
    assert r.status_code == 200 and r.json()["access_token"]


def test_f21_email_channel_uses_smtp(w, monkeypatch):
    from app.services import mailer
    mails = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subj, body: mails.append(to) or True)
    _require_otp(w, channels=("email",))
    r = _login(w, w.u.ro)
    assert r.status_code == 200 and r.json()["delivery"]["channels"] == ["email"]
    assert mails == [w.u.ro.email]


def test_f21_staff_phone_is_validated_in_user_admin(w):
    api, h = w.api(w.u.sa), {"X-Tenant-Id": str(w.A.id)}
    assert api.put(f"/api/v1/access/users/{w.u.ro.id}", json={"phone": "123"},
                   headers=h).status_code == 422
    r = api.put(f"/api/v1/access/users/{w.u.ro.id}", json={"phone": "+254 711 000 222"},
                headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["phone"] == "0711000222"


# =========================================================================== #
# F22 - consent codes
# =========================================================================== #
def _consent_code(w, phone="0712345678", client_id=None, text=None):
    body = {"phone": phone, "client_id": client_id if client_id is not None else w.b.id}
    if text:
        body["consent_text"] = text
    r = w.api(w.u.ro).post("/api/v1/clients/request-consent-otp", json=body)
    assert r.status_code == 200, r.text
    return w.sent[-1][1].split("Share code ")[1][:6]


def test_f22_code_goes_only_to_a_number_on_file(w):
    r = w.api(w.u.ro).post("/api/v1/clients/request-consent-otp",
                           json={"phone": "0799888777", "client_id": w.b.id})
    assert r.status_code == 422 and r.json()["detail"] == "phone_not_on_record"


def test_f22_code_for_one_client_cannot_consent_for_another(w):
    other = Borrower(tenant_id=w.A.id, first_name="Peter", last_name="Otieno",
                     national_id="87654321", phone="0712345678",
                     branch_id=w.seed.branch.id, officer_staff_id=w.seed.staff.id)
    w.db.add(other)
    w.db.commit()
    code = _consent_code(w)
    r = w.api(w.u.ro).post("/api/v1/clients/verify-consent-otp",
                           json={"code": code, "phone": "0712345678", "client_id": other.id})
    assert r.status_code == 422
    assert w.db.query(ClientConsentLog).count() == 0


def test_f22_log_keeps_the_wording_that_was_sent(w):
    text = "Mularcredit checking your CRB record and processing your data."
    code = _consent_code(w, text=text)
    sms_row = w.db.query(SmsLog).filter_by(trigger_type="consent").one()
    assert code not in sms_row.message
    r = w.api(w.u.ro).post("/api/v1/clients/verify-consent-otp",
                           json={"code": code, "phone": "+254712345678", "client_id": w.b.id})
    assert r.status_code == 200, r.text
    log = w.db.query(ClientConsentLog).one()
    assert log.consent_text == text and log.consent_version == "otp_v2"
    kc = w.db.query(KycConsent).filter_by(borrower_id=w.b.id).one()
    assert kc.consent_data_processing and kc.consent_version == "otp_v2"


def test_f22_unbound_code_from_the_generic_endpoint_cannot_give_consent(w):
    """An officer must not be able to mint a consent code on the generic OTP
    endpoint (delivered to their own phone) and use it as client consent."""
    w.u.ro.phone = "0711000222"
    w.db.commit()
    api = w.api(w.u.ro)
    r = api.post("/api/v1/auth/otp/request", json={"purpose": "consent"})
    assert r.status_code == 422
    # even a stray unbound consent token is refused
    from app.services import otp
    code = otp.generate(w.db, w.u.ro, purpose="consent")
    r = api.post("/api/v1/clients/verify-consent-otp",
                 json={"code": code, "phone": "0712345678", "client_id": w.b.id})
    assert r.status_code == 422
    assert w.db.query(ClientConsentLog).count() == 0



# =========================================================================== #
# Per-DCP settings (KYC Settings screen)
# =========================================================================== #
def test_settings_are_saved_by_the_dcps_own_admin_and_validated(w):
    api = w.api(w.u.sysadmin)                       # DCP system admin, not Finyl
    assert api.put("/api/v1/settings/validation-prefs",
                   json={"mpesa_confirmation_mode": "nobody"}).status_code == 422
    assert api.put("/api/v1/settings/validation-prefs",
                   json={"identity_edit_approvals": 3}).status_code == 422
    r = api.put("/api/v1/settings/validation-prefs", json={
        "mpesa_confirmation_mode": "single_person", "identity_edit_approvals": 1,
        "allow_review_override": False, "crb_max_age_days": 60, "min_credit_score": 400})
    assert r.status_code == 200, r.text
    assert r.json()["crb_max_age_days"] == 60 and r.json()["min_credit_score"] == 400
    r = api.put("/api/v1/settings/validation-prefs", json={"min_credit_score": -1})
    assert r.json()["min_credit_score"] is None
    assert w.api(w.u.ro).get("/api/v1/settings/validation-prefs").status_code == 403


def test_single_person_mode_lets_the_officer_confirm(w):
    assert _confirm(w, w.u.ro).status_code == 403            # default: second person
    _prefs(w, mpesa_confirmation_mode="single_person")
    r = _confirm(w, w.u.ro)
    assert r.status_code == 200, r.text
    w.db.refresh(w.b)
    assert w.b.mpesa_validated_by == w.u.ro.id
    # the name must still match, whatever the mode
    w.b.mpesa_validated = False
    w.db.commit()
    assert _confirm(w, w.u.ro, name="PETER OTIENO").status_code == 422


def test_one_approver_for_identity_edits_when_the_dcp_chooses(w):
    _prefs(w, identity_edit_approvals=1)
    r = _edit(w, w.u.ro, {"phone": {"new_value": "0799000111"}})
    assert r.json()["approvals_required"] == 1 and r.json()["next_stage"] == "final"
    rid = r.json()["id"]
    r = w.api(w.u.bm).post(f"/api/v1/clients/edit-requests/{rid}/approve", json={})
    assert r.json()["status"] == "approved"
    # a request raised under the two-approver setting keeps needing two
    _prefs(w, identity_edit_approvals=2)
    rid = _edit(w, w.u.ro, {"last_name": {"new_value": "Kamau"}}).json()["id"]
    _prefs(w, identity_edit_approvals=1)
    r = w.api(w.u.bm).post(f"/api/v1/clients/edit-requests/{rid}/approve", json={})
    assert r.json()["status"] == "pending" and r.json()["next_stage"] == "final"


def test_review_override_can_be_switched_off(w):
    w.b.profile_status = "pending_approval"
    w.db.commit()
    w.seed.make_kyc_ready(w.b)
    w.db.add(KycMismatchEscalation(tenant_id=w.A.id, client_id=w.b.id,
                                   mismatch_type="name_mismatch", logged_by=w.u.ro.id,
                                   status="open"))
    _prefs(w, allow_review_override=False)
    r = w.api(w.u.bm).post(f"/api/v1/approvals/clients/{w.b.id}",
                           json={"action": "approve", "note": "Checked ID in person"})
    assert r.status_code == 409 and "resolved first" in r.json()["detail"]


def test_open_loan_limit_is_set_per_product(w):
    from app.models import ImpactSurvey
    w.seed.make_kyc_ready(w.b)
    for cycle in (2, 3, 4):                  # repeat borrowers need a survey per cycle
        w.db.add(ImpactSurvey(tenant_id=w.A.id, survey_id=f"S{cycle}", borrower_id=w.b.id,
                              loan_cycle_number=cycle))
    p = w.seed.product
    p.rules = dict(p.rules or {}, max_open_loans=2)
    w.db.commit()
    assert _apply(w).status_code == 200
    assert _apply(w).status_code == 200
    r = _apply(w)
    assert r.status_code == 409 and "allows 2 at a time" in r.json()["detail"]
    p.rules = dict(p.rules, max_open_loans=0)                # no limit
    w.db.commit()
    assert _apply(w).status_code == 200


def test_product_borrowing_rules_are_kept_on_edit(w):
    sa, h = w.api(w.u.sa), {"X-Tenant-Id": str(w.A.id)}
    p = w.seed.product
    body = {"name": p.name, "code": p.code, "requires_guarantor": True,
            "requires_next_of_kin": False, "business_op_age_required": True,
            "rules": dict(p.rules or {}, max_open_loans=3, min_business_age_months=12)}
    assert w.api(w.u.sa).put(f"/api/v1/lending/products/{p.id}", json=body,
                             headers=h).status_code == 200
    # an edit that does not mention the switches leaves them as they are
    r = sa.put(f"/api/v1/lending/products/{p.id}", headers=h,
               json={"name": p.name, "code": p.code, "rules": body["rules"]})
    assert r.status_code == 200
    w.db.refresh(p)
    assert p.requires_guarantor and not p.requires_next_of_kin and p.business_op_age_required
    assert sa.put(f"/api/v1/lending/products/{p.id}", headers=h,
                  json={"name": p.name, "code": p.code, "min_age": 16}).status_code == 422
