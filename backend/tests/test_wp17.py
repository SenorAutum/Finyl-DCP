"""
WP17 - CRM expansion: the client timeline, logging contact with a follow-up
task, segments, SMS campaigns (drafted by one person, approved by another,
respecting opt-outs and marketing consent) and the pipeline report.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import (Borrower, CallLog, CrmCampaign, CrmLead, CrmTask, KycConsent, SmsLog, SmsOptOut,
                        TenantModule)
from app.services import crm_plus as C
from app.services import ledger as L
from app.services import sms


@pytest.fixture()
def w(seed, db, monkeypatch):
    import app.main as main_mod

    db.add(TenantModule(tenant_id=seed.tenant.id, module_key="crm", enabled=True))
    bm1 = seed.make_user("branch_manager", "bm1@test.dcp", branch_id=seed.branch.id)
    bm2 = seed.make_user("branch_manager", "bm2@test.dcp", branch_id=seed.branch.id)
    ro = seed.make_user("relationship_officer", "ro17@test.dcp", staff_id=seed.staff.id)
    db.commit()
    sent = []

    def fake(phone, message):
        sent.append((phone, message))
        return {"status": "sent", "provider": "test", "provider_ref": f"R{len(sent)}", "provider_response": None, "error": None}
    monkeypatch.setattr(sms, "_dispatch_to_provider", fake)

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app, raise_server_exceptions=True)

    def client(first, phone, gender="female", sector="retail", consent=None, **kw):
        b = Borrower(tenant_id=seed.tenant.id, first_name=first, last_name="Test", national_id=phone[-8:], phone=phone,
                     gender=gender, business_sector=sector, branch_id=seed.branch.id, region_id=seed.region.id,
                     officer_staff_id=kw.pop("officer_staff_id", seed.staff.id), **kw)
        db.add(b)
        db.flush()
        if consent is not None:
            db.add(KycConsent(tenant_id=seed.tenant.id, borrower_id=b.id, consent_data_processing=True,
                              consent_marketing=consent, consent_version="t"))
        db.commit()
        return b

    yield SimpleNamespace(seed=seed, db=db, bm1=bm1, bm2=bm2, ro=ro, api=api, client=client, sent=sent, T=seed.tenant)
    main_mod.app.dependency_overrides.clear()


# =========================================================================== contact + tasks
def test_logging_a_call_with_a_next_step_creates_a_task_and_shows_on_the_timeline(w):
    b = w.seed.borrower
    r = w.api(w.ro).post("/api/v1/crm-plus/interactions", json={
        "borrower_id": b.id, "kind": "call", "subject": "Asked about a bigger loan", "notes": "Wants 30k for stock",
        "outcome": "interested", "next_step": {"title": "Visit the shop", "due_date": (L.today() + timedelta(days=2)).isoformat()}})
    assert r.status_code == 200, r.text
    task = w.db.get(CrmTask, r.json()["task_id"])
    assert task.assigned_user_id == w.ro.id and task.borrower_id == b.id

    # an older call from the call centre and a loan show too, newest first
    w.db.add(CallLog(tenant_id=w.T.id, agent_id=w.seed.staff.id, borrower_id=b.id, call_outcome="no_answer",
                     call_date=datetime.utcnow() - timedelta(days=3)))
    w.seed.make_loan(status="paid", application_date=L.today() - timedelta(days=40), loan_cycle_number=1)
    w.db.commit()
    d = w.api(w.ro).get(f"/api/v1/crm-plus/clients/{b.id}").json()
    kinds = [e["type"] for e in d["timeline"]]
    assert kinds[0] == "task" and "interaction" in kinds and "call" in kinds and "loan" in kinds
    assert d["figures"]["repaid"] == 1 and len(d["tasks"]) == 1

    mine = w.api(w.ro).get("/api/v1/crm-plus/tasks").json()
    assert mine["counts"]["open"] == 1 and mine["tasks"][0]["client"] == "Jane Wanjiku"
    assert w.api(w.ro).post(f"/api/v1/crm-plus/tasks/{task.id}/close", json={"status": "done", "note": "Visited"}).status_code == 200
    assert w.api(w.ro).get("/api/v1/crm-plus/tasks").json()["counts"]["open"] == 0


def test_contact_needs_one_client_or_lead_and_no_future_time(w):
    a = w.api(w.ro)
    assert a.post("/api/v1/crm-plus/interactions", json={"kind": "call", "notes": "x"}).status_code == 422
    future = (datetime.utcnow() + timedelta(days=1)).isoformat()
    assert a.post("/api/v1/crm-plus/interactions", json={"borrower_id": w.seed.borrower.id, "kind": "call", "notes": "x",
                                                         "happened_at": future}).status_code == 422
    lead = CrmLead(tenant_id=w.T.id, name="Mama Mboga", phone="0711000111", stage="lead")
    w.db.add(lead)
    w.db.commit()
    assert a.post("/api/v1/crm-plus/interactions", json={"lead_id": lead.id, "kind": "visit", "notes": "Met at the market"}).status_code == 200
    assert len(a.get(f"/api/v1/crm-plus/leads/{lead.id}/interactions").json()["interactions"]) == 1


def test_officer_cannot_see_another_officers_client(w):
    from app.models import Staff
    other = Staff(tenant_id=w.T.id, branch_id=w.seed.branch.id, name="Officer Two", role="loan_officer")
    w.db.add(other)
    w.db.flush()
    b = w.client("Faith", "0722000001", officer_staff_id=other.id)
    assert w.api(w.ro).get(f"/api/v1/crm-plus/clients/{b.id}").status_code == 404
    assert w.api(w.bm1).get(f"/api/v1/crm-plus/clients/{b.id}").status_code == 200


# =========================================================================== segments
def test_segments_filter_by_loan_state_gender_and_dormancy(w):
    women_new = w.client("Akinyi", "0722000011")
    man = w.client("Otieno", "0722000012", gender="male")
    repaid = w.client("Wambui", "0722000013")
    seed = w.seed
    seed.make_loan(status="active", due_date=L.today() - timedelta(days=5))       # Jane: in arrears
    ln = seed.make_loan(status="paid")
    ln.borrower_id = repaid.id
    from app.models import Repayment
    w.db.add(Repayment(tenant_id=w.T.id, loan_id=ln.id, amount=1000, payment_date=datetime.utcnow() - timedelta(days=90)))
    w.db.commit()

    ids = lambda rules: {b.id for b in C.evaluate(w.db, w.T.id, rules)}  # noqa: E731
    assert ids({"loan_state": "in_arrears"}) == {seed.borrower.id}
    assert ids({"loan_state": "never_borrowed"}) == {women_new.id, man.id}
    assert ids({"loan_state": "repaid_no_open", "dormant_days": 60}) == {repaid.id}
    assert ids({"loan_state": "repaid_no_open", "dormant_days": 120}) == set()
    assert man.id not in ids({"gender": "female"})
    with pytest.raises(Exception):
        C.clean_rules({"colour": "blue"})

    r = w.api(w.bm1).post("/api/v1/crm-plus/segments", json={"name": "Never borrowed", "rules": {"loan_state": "never_borrowed"}})
    assert r.status_code == 200 and r.json()["count"] == 2
    assert w.api(w.bm1).post("/api/v1/crm-plus/segments", json={"name": "never borrowed", "rules": {}}).status_code == 409
    # an officer may look but not build segments
    assert w.api(w.ro).post("/api/v1/crm-plus/segments", json={"name": "Mine", "rules": {}}).status_code == 403


# =========================================================================== campaigns
def test_marketing_campaign_needs_a_second_person_and_respects_consent_and_opt_out(w):
    yes = w.client("Achieng", "0722000021", consent=True)
    no = w.client("Mumbua", "0722000022", consent=False)
    out = w.client("Chebet", "0722000023", consent=True)
    w.db.add(SmsOptOut(tenant_id=w.T.id, phone=sms.normalise_phone(out.phone), active=True))
    w.db.commit()
    seg = w.api(w.bm1).post("/api/v1/crm-plus/segments", json={"name": "New", "rules": {"loan_state": "never_borrowed"}}).json()
    c = w.api(w.bm1).post("/api/v1/crm-plus/campaigns", json={"name": "December offer", "segment_id": seg["id"], "purpose": "marketing",
                                                              "message": "Hi {{first_name}}, {{dcp_name}} has a December offer."}).json()
    assert w.api(w.bm1).post(f"/api/v1/crm-plus/campaigns/{c['id']}/submit").json()["audience"] == 4   # Jane (the seed client) too
    # the person who drafted it cannot approve it
    assert w.api(w.bm1).post(f"/api/v1/crm-plus/campaigns/{c['id']}/decide", json={"approve": True}).status_code == 403
    r = w.api(w.bm2).post(f"/api/v1/crm-plus/campaigns/{c['id']}/decide", json={"approve": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": c["id"], "status": "sent", "sent": 1, "failed": 0, "skipped": 3}
    assert len(w.sent) == 1
    phone, text = w.sent[0]
    assert phone == sms.normalise_phone(yes.phone)
    assert text.startswith("Hi Achieng, Test DCP has a December offer.") and text.endswith("Reply STOP to opt out.")
    d = w.api(w.bm1).get(f"/api/v1/crm-plus/campaigns/{c['id']}").json()
    reasons = {x["borrower_id"]: x["reason"] for x in d["recipient_list"]}
    assert reasons[no.id] == "no marketing consent" and reasons[out.id] == "opted out" and reasons[yes.id] is None
    assert w.db.query(SmsLog).filter(SmsLog.trigger_type == "campaign").count() == 1
    # sending twice is impossible
    assert w.api(w.bm2).post(f"/api/v1/crm-plus/campaigns/{c['id']}/decide", json={"approve": True}).status_code == 409


def test_service_campaign_goes_to_everyone_not_opted_out_and_scheduled_ones_wait(w):
    w.client("Achieng", "0722000031", consent=False)
    w.client("Mumbua", "0722000032")
    seg = w.api(w.bm1).post("/api/v1/crm-plus/segments", json={"name": "New", "rules": {"loan_state": "never_borrowed"}}).json()
    later = (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z"
    c = w.api(w.bm1).post("/api/v1/crm-plus/campaigns", json={"name": "Branch moving", "segment_id": seg["id"], "purpose": "service",
                                                              "message": "Our CBD branch moves to Moi Avenue on Monday.", "send_at": later}).json()
    w.api(w.bm1).post(f"/api/v1/crm-plus/campaigns/{c['id']}/submit")
    r = w.api(w.bm2).post(f"/api/v1/crm-plus/campaigns/{c['id']}/decide", json={"approve": True}).json()
    assert r["status"] == "approved" and not w.sent
    assert C.send_due(w.db) == 0
    camp = w.db.get(CrmCampaign, c["id"])
    camp.send_at = datetime.utcnow() - timedelta(minutes=1)
    w.db.commit()
    assert C.send_due(w.db) == 1
    w.db.refresh(camp)
    assert camp.status == "sent" and camp.sent == 3 and camp.skipped == 0
    assert all("STOP" not in t for _, t in w.sent)


def test_rejected_campaign_can_be_fixed_and_resubmitted(w):
    w.client("Achieng", "0722000041")
    seg = w.api(w.bm1).post("/api/v1/crm-plus/segments", json={"name": "New", "rules": {"loan_state": "never_borrowed"}}).json()
    body = {"name": "Offer", "segment_id": seg["id"], "purpose": "service", "message": "Hello there friend"}
    c = w.api(w.bm1).post("/api/v1/crm-plus/campaigns", json=body).json()
    w.api(w.bm1).post(f"/api/v1/crm-plus/campaigns/{c['id']}/submit")
    assert w.api(w.bm2).post(f"/api/v1/crm-plus/campaigns/{c['id']}/decide", json={"approve": False}).status_code == 422
    assert w.api(w.bm2).post(f"/api/v1/crm-plus/campaigns/{c['id']}/decide",
                             json={"approve": False, "note": "Say which branch"}).json()["status"] == "rejected"
    # the segment in use by a pending campaign cannot be edited; a rejected one can
    assert w.api(w.bm1).put(f"/api/v1/crm-plus/campaigns/{c['id']}", json={**body, "message": "Hello from CBD branch"}).status_code == 200
    assert w.api(w.bm1).post(f"/api/v1/crm-plus/campaigns/{c['id']}/submit").json()["status"] == "pending"
    assert w.api(w.bm1).put(f"/api/v1/crm-plus/segments/{seg['id']}", json={"name": "New", "rules": {}}).status_code == 409
    assert w.api(w.bm1).delete(f"/api/v1/crm-plus/segments/{seg['id']}").status_code == 409


# =========================================================================== pipeline
def test_pipeline_report_counts_stages_officers_and_conversion(w):
    now = datetime.utcnow()
    for i, stage in enumerate(["lead", "contacted", "field_visit", "disbursed"]):
        ld = CrmLead(tenant_id=w.T.id, name=f"Lead {i}", phone=f"07110000{i:02d}", stage=stage,
                     assigned_staff_id=w.seed.staff.id, estimated_loan_amount=10000, created_at=now - timedelta(days=10))
        if stage == "disbursed":
            ld.converted_to_client_id = w.seed.borrower.id
            ld.conversion_triggered_at = now - timedelta(days=4)
        w.db.add(ld)
    w.db.commit()
    r = w.api(w.bm1).get("/api/v1/crm-plus/pipeline").json()
    assert r["total"] == 4 and r["converted"] == 1 and r["conversion_pct"] == 25.0
    assert r["avg_days_to_convert"] == 6.0 and r["pipeline_value"] == 30000
    assert {s["stage"]: s["count"] for s in r["by_stage"]}["contacted"] == 1
    assert r["by_officer"][0] == {"officer": "Officer One", "leads": 4, "converted": 1, "value": 40000.0, "conversion_pct": 25.0}
    assert sum(m["new"] for m in r["monthly"]) == 4
