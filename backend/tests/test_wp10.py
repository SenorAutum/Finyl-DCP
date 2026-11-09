"""
WP10 - open items from earlier packages: guarantors, wallet unlock, DCP roles
as approvers, times with a time zone, missed reports and the payoff amount in
reminder SMS.
"""
import importlib.util
import json
import os
import shutil
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import (AuditLog, Borrower, ClientMobileWallet, CustomRole, Guarantor,
                        ApprovalThreshold, ReportSchedule, RolePermissionOverride, Staff, Tenant,
                        User)

BACKEND = Path(__file__).resolve().parents[1]
NBO = ZoneInfo("Africa/Nairobi")


@pytest.fixture()
def w(seed, db, monkeypatch, tmp_path):
    import app.main as main_mod
    from app.services import scheduler, storage

    monkeypatch.setattr(scheduler, "SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(storage, "ROOT", str(tmp_path))

    def mk(role, email, tenant=seed.tenant, **kw):
        u = User(email=email, hashed_password="x", full_name=email.split("@")[0], role=role,
                 tenant_id=tenant.id if tenant else None, **kw)
        db.add(u)
        db.flush()
        return u

    # A second officer, with a client of their own in the same branch.
    staff2 = Staff(tenant_id=seed.tenant.id, branch_id=seed.branch.id, name="Officer Two",
                   role="loan_officer")
    db.add(staff2)
    db.flush()
    other = Borrower(tenant_id=seed.tenant.id, first_name="Paul", last_name="Otieno",
                     national_id="87654321", phone="0722000111", branch_id=seed.branch.id,
                     region_id=seed.region.id, officer_staff_id=staff2.id)
    db.add(other)
    tenant_b = Tenant(name="Other DCP", code="ODCP")
    db.add(tenant_b)
    db.flush()
    from app.models import TenantModule
    db.add(TenantModule(tenant_id=tenant_b.id, module_key="lending", enabled=True))

    u = SimpleNamespace(
        ro=mk("relationship_officer", "ro10@a.dcp", staff_id=seed.staff.id, branch_id=seed.branch.id),
        ro2=mk("relationship_officer", "ro10b@a.dcp", staff_id=staff2.id, branch_id=seed.branch.id),
        bm=mk("branch_manager", "bm10@a.dcp", branch_id=seed.branch.id),
        bm2=mk("branch_manager", "bm10b@a.dcp", branch_id=seed.branch.id),
        admin=mk("system_admin", "admin10@a.dcp"),
        other_tenant=mk("tenant_admin", "admin10@b.dcp", tenant=tenant_b),
    )
    seed.borrower.created_by_user_id = u.ro.id
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app)

    yield SimpleNamespace(seed=seed, db=db, A=seed.tenant, B=tenant_b, u=u, api=api,
                          other=other, H={"X-Tenant-Id": str(seed.tenant.id)})
    main_mod.app.dependency_overrides.clear()


def _guarantor(w, user=None, client=None, **kw):
    body = {"client_id": (client or w.seed.borrower).id, "full_name": "Peter Kamau Mwangi",
            "national_id": "11223344", "phone": "0733111222", "relationship": "Brother", **kw}
    return w.api(user or w.u.ro).post("/api/v1/guarantors", json=body, headers=w.H)


# =========================================================================== #
# Guarantors: data scope
# =========================================================================== #
def test_guarantors_follow_the_clients_data_scope(w):
    mine = _guarantor(w)
    assert mine.status_code == 200, mine.text
    gid = mine.json()["id"]
    theirs = _guarantor(w, user=w.u.ro2, client=w.other)
    assert theirs.status_code == 200, theirs.text
    tid = theirs.json()["id"]

    ro = w.api(w.u.ro)
    # Another officer's client: not listed, not readable, not changeable.
    assert ro.get(f"/api/v1/guarantors?client_id={w.other.id}", headers=w.H).status_code == 403
    assert ro.get(f"/api/v1/guarantors/{tid}", headers=w.H).status_code == 403
    assert ro.put(f"/api/v1/guarantors/{tid}", json={"address": "x"}, headers=w.H).status_code == 403
    assert ro.delete(f"/api/v1/guarantors/{tid}", headers=w.H).status_code == 403
    assert _guarantor(w, client=w.other).status_code == 403
    # Own client: fine.
    assert ro.get(f"/api/v1/guarantors/{gid}", headers=w.H).status_code == 200
    # Branch manager sees both.
    assert w.api(w.u.bm).get(f"/api/v1/guarantors/{tid}", headers=w.H).status_code == 200
    # Another DCP: as if it did not exist.
    hb = {"X-Tenant-Id": str(w.B.id)}
    assert w.api(w.u.other_tenant).get(f"/api/v1/guarantors/{gid}", headers=hb).status_code == 404


def test_a_client_cannot_guarantee_themselves(w):
    r = _guarantor(w, phone="+254 712 345 678")          # the client's own number
    assert r.status_code == 422 and "client's own" in r.json()["detail"]
    r = _guarantor(w, national_id="12345678")             # the client's own ID
    assert r.status_code == 422 and "National ID" in r.json()["detail"]
    r = _guarantor(w, phone="12345")
    assert r.status_code == 422 and "not a valid Kenyan mobile number" in r.json()["detail"]


# =========================================================================== #
# Guarantors: M-Pesa confirmation makes the guarantor usable
# =========================================================================== #
def test_guarantor_confirmed_by_a_second_person_unlocks_guarantor_products(w):
    from app.services import eligibility
    gid = _guarantor(w).json()["id"]
    w.seed.product.requires_guarantor = True
    w.db.commit()

    def problems():
        try:
            eligibility.require_product_rules(w.db, tenant_id=w.A.id, client=w.seed.borrower,
                                              product=w.seed.product)
            return None
        except Exception as exc:        # HTTPException
            return str(getattr(exc, "detail", exc))
    assert "validated guarantor" in problems()

    body = {"registered_name": "PETER K MWANGI",
            "evidence": "KES 1 send-money test at 10:15; M-Pesa showed PETER K MWANGI"}
    # The old "validate" no longer marks anything as validated.
    v = w.api(w.u.ro).post(f"/api/v1/guarantors/{gid}/validate", json={}, headers=w.H)
    assert v.status_code == 200 and v.json()["mpesa_validated"] is False
    # The officer who captured it may not confirm it.
    assert w.api(w.u.ro).post(f"/api/v1/guarantors/{gid}/confirm-mpesa", json=body,
                              headers=w.H).status_code == 403
    # A wrong name is refused.
    bad = w.api(w.u.bm).post(f"/api/v1/guarantors/{gid}/confirm-mpesa",
                             json={**body, "registered_name": "JOHN DOE"}, headers=w.H)
    assert bad.status_code == 422 and "guarantor's names" in bad.json()["detail"]
    ok = w.api(w.u.bm).post(f"/api/v1/guarantors/{gid}/confirm-mpesa", json=body, headers=w.H)
    assert ok.status_code == 200, ok.text
    assert ok.json()["mpesa_validated"] is True and ok.json()["kyc_status"] == "validated"
    w.db.expire_all()
    assert "guarantor" not in problems()       # only the unrelated rules remain
    assert w.db.query(AuditLog).filter_by(action="guarantor.mpesa_confirmed").count() == 1

    # A new phone number removes the confirmation: it was for the old number.
    up = w.api(w.u.ro).put(f"/api/v1/guarantors/{gid}", json={"phone": "0733999888"}, headers=w.H)
    assert up.status_code == 200
    assert up.json()["mpesa_validated"] is False and up.json()["kyc_status"] == "pending"
    w.db.expire_all()
    assert "validated guarantor" in problems()


# =========================================================================== #
# Guarantors: real document uploads
# =========================================================================== #
def test_guarantor_documents_are_uploaded_downloaded_and_removed(w):
    gid = _guarantor(w).json()["id"]
    ro = w.api(w.u.ro)
    r = ro.post(f"/api/v1/guarantors/{gid}/documents?doc_types=national_id",
                files=[("files", ("id front.jpg", b"\xff\xd8 fake jpeg", "image/jpeg"))],
                headers=w.H)
    assert r.status_code == 200, r.text
    doc = r.json()["documents"][0]
    assert doc["doc_type"] == "national_id" and "storage_path" not in doc
    dl = ro.get(f"/api/v1/guarantors/{gid}/documents/{doc['id']}/download", headers=w.H)
    assert dl.status_code == 200 and dl.content == b"\xff\xd8 fake jpeg"
    assert dl.headers["content-type"] == "application/octet-stream"
    # Another officer cannot download it.
    assert w.api(w.u.ro2).get(f"/api/v1/guarantors/{gid}/documents/{doc['id']}/download",
                              headers=w.H).status_code == 403
    path = w.db.query(Guarantor).get(gid).documents[0].storage_path
    assert os.path.exists(path)
    rm = w.api(w.u.ro).delete(f"/api/v1/guarantors/{gid}/documents/{doc['id']}", headers=w.H)
    assert rm.status_code == 200, rm.text
    assert not os.path.exists(path)


# =========================================================================== #
# Wallet unlock
# =========================================================================== #
def test_a_locked_wallet_can_be_unlocked_with_a_reason_by_someone_else(w):
    wal = ClientMobileWallet(tenant_id=w.A.id, client_id=w.seed.borrower.id,
                             mobile_number="0712345678", operator="safaricom", active=True,
                             wallet_locked=True, locked_at=datetime.utcnow(), locked_by="Someone")
    w.db.add(wal)
    w.db.commit()
    url = f"/api/v1/clients/{w.seed.borrower.id}/wallets/{wal.id}/unlock"
    reason = {"reason": "Client lost the line; new SIM seen at branch today"}
    # Officers do not hold the permission.
    assert w.api(w.u.ro).post(url, json=reason, headers=w.H).status_code == 403
    # A reason is required.
    short = w.api(w.u.bm).post(url, json={"reason": "lost"}, headers=w.H)
    assert short.status_code == 422
    # Whoever captured the client may not unlock it.
    w.seed.borrower.created_by_user_id = w.u.bm2.id
    w.db.commit()
    assert w.api(w.u.bm2).post(url, json=reason, headers=w.H).status_code == 403
    ok = w.api(w.u.bm).post(url, json=reason, headers=w.H)
    assert ok.status_code == 200, ok.text
    w.db.expire_all()
    assert w.db.get(ClientMobileWallet, wal.id).wallet_locked is False
    a = w.db.query(AuditLog).filter_by(action="client.wallet_unlocked").one()
    assert a.details["reason"].startswith("Client lost the line") and a.details["locked_by"] == "Someone"
    # Unlocking twice says so.
    assert w.api(w.u.bm).post(url, json=reason, headers=w.H).status_code == 409
    # The wallet can now be removed.
    assert w.api(w.u.bm).delete(f"/api/v1/clients/{w.seed.borrower.id}/wallets/{wal.id}",
                                headers=w.H).status_code == 200


# =========================================================================== #
# DCP-created roles as approvers
# =========================================================================== #
def test_a_dcp_role_can_be_switched_on_as_approver_only_with_a_limit(w):
    from app.services import rbac
    w.db.add(CustomRole(tenant_id=w.A.id, role_key="credit_lead", label="Credit Lead"))
    w.db.add(CustomRole(tenant_id=w.A.id, role_key="clerk", label="Clerk"))
    for p in ("loans.approve", "loans.view_all", "clients.view_all"):
        w.db.add(RolePermissionOverride(tenant_id=w.A.id, role="credit_lead", permission_key=p,
                                        granted=True))
    w.db.commit()
    admin = w.api(w.u.admin)
    cfg = admin.get("/api/v1/settings/approver-config", headers=w.H)
    assert cfg.status_code == 200, cfg.text
    cfg = cfg.json()
    loan_roles = {r["role"]: r for t in cfg["approval_types"] if t["approval_type"] == "loan"
                  for r in t["roles"]}
    assert loan_roles["credit_lead"]["custom"] is True
    assert loan_roles["credit_lead"]["enabled"] is False           # off until switched on
    assert loan_roles["credit_lead"]["has_limit"] is False
    assert "clerk" not in loan_roles                               # lacks loans.approve
    assert "relationship_officer" not in loan_roles

    body = {"tenant_id": w.A.id, "approval_type": "loan", "role": "credit_lead", "enabled": True}
    r = admin.post("/api/v1/settings/approver-config", json=body, headers=w.H)
    assert r.status_code == 400 and "loan approval limit" in r.json()["detail"]
    r = admin.post("/api/v1/settings/approver-config",
                   json={**body, "role": "clerk"}, headers=w.H)
    assert r.status_code == 400 and "permission" in r.json()["detail"]

    w.db.add(ApprovalThreshold(tenant_id=w.A.id, scope_type="role", scope_key="credit_lead",
                               threshold_type="loan_approval", amount=50000))
    w.db.commit()
    assert admin.post("/api/v1/settings/approver-config", json=body, headers=w.H).status_code == 200
    assert rbac.approver_enabled(w.db, w.A.id, "loan", "credit_lead") is True
    lead = User(email="lead@a.dcp", hashed_password="x", full_name="Lead", role="credit_lead",
                tenant_id=w.A.id)
    assert rbac.loan_approval_limit(w.db, w.A.id, lead) == 50000.0

    # Taking the permission away switches the approver off as well.
    w.db.query(RolePermissionOverride).filter_by(role="credit_lead",
                                                  permission_key="loans.approve").delete()
    w.db.commit()
    assert rbac.approver_enabled(w.db, w.A.id, "loan", "credit_lead") is False


def test_a_dcp_role_without_a_limit_never_approves_unlimited(w):
    from app.services import rbac
    lead = User(email="lead2@a.dcp", hashed_password="x", full_name="Lead", role="credit_lead",
                tenant_id=w.A.id)
    assert rbac.loan_approval_limit(w.db, w.A.id, lead) == 0.0
    # Built-in roles are unchanged (no threshold = no limit, as before).
    assert rbac.loan_approval_limit(w.db, w.A.id, w.u.bm) is None


def test_finyl_approver_screen_lists_dcp_roles_too(w, seed):
    w.db.add(CustomRole(tenant_id=w.A.id, role_key="checker_x", label="Checker X"))
    w.db.add(RolePermissionOverride(tenant_id=w.A.id, role="checker_x",
                                    permission_key="disburse.approve", granted=True))
    sa = User(email="sa10@finyl.app", hashed_password="x", full_name="SA", role="super_admin")
    w.db.add(sa)
    w.db.commit()
    c = w.api(sa)
    cfg = c.get(f"/api/v1/access/approver-config?tenant_id={w.A.id}").json()
    dis = next(t for t in cfg["approval_types"] if t["approval_type"] == "disbursement")
    assert any(r["role"] == "checker_x" and r["label"] == "Checker X" for r in dis["roles"])
    r = c.post("/api/v1/access/approver-config",
               json={"tenant_id": w.A.id, "approval_type": "disbursement", "role": "checker_x",
                     "enabled": True})
    assert r.status_code == 200, r.text
    assert c.get("/api/v1/access/approver-config?tenant_id=999999").status_code == 404


# =========================================================================== #
# Times with a time zone
# =========================================================================== #
def test_api_times_carry_their_zone():
    from fastapi.encoders import jsonable_encoder
    import app.main  # noqa: F401  (installs the encoder)
    out = jsonable_encoder({"naive": datetime(2026, 11, 9, 5, 0),
                            "aware": datetime(2026, 11, 9, 8, 0, tzinfo=NBO)})
    assert out == {"naive": "2026-11-09T05:00:00Z", "aware": "2026-11-09T05:00:00Z"}


def test_every_connection_runs_in_utc_and_reads_back_utc(w):
    from sqlalchemy import create_engine, event, text
    from app.core import database
    # The app's own connection hooks, on a connection to the test database.
    eng = create_engine(w.db.get_bind().url)
    event.listen(eng, "connect", database._set_search_path)
    event.listen(eng, "begin", database._set_search_path_txn)
    with eng.connect() as conn:
        conn.exec_driver_sql("SET TIME ZONE 'Africa/Nairobi'")   # a Kenyan server default
        conn.commit()
        assert conn.execute(text("SHOW TIME ZONE")).scalar() == "UTC"   # new transaction
    eng.dispose()
    # A naive UTC value goes in and comes back unchanged (code relies on that).
    sch = ReportSchedule(tenant_id=w.A.id, name="t", report_type="loan_book", frequency="weekly",
                         recipients="a@b.c", last_run_at=datetime(2026, 11, 9, 4, 0))
    w.db.add(sch)
    w.db.commit()
    w.db.expire_all()
    got = w.db.get(ReportSchedule, sch.id).last_run_at
    assert got == datetime(2026, 11, 9, 4, 0) and got.tzinfo is None


pg_only = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").startswith("postgres"),
                             reason="needs PostgreSQL")


def _load_runner():
    spec = importlib.util.spec_from_file_location("wp10_run_migrations",
                                                  BACKEND / "scripts" / "run_migrations.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pg_only
def test_migration_039_keeps_each_moment_and_leaves_no_zoneless_times(monkeypatch, tmp_path):
    import psycopg2
    from sqlalchemy.engine import make_url
    base = os.environ["TEST_DATABASE_URL"]
    name = f"wp10_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(base)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    try:
        monkeypatch.setattr(settings, "DATABASE_URL", url)
        monkeypatch.setattr(settings, "DB_SCHEMA", "finyl_dcp")
        monkeypatch.setattr(sys, "argv", ["run_migrations.py"])
        before = tmp_path / "before"
        before.mkdir()
        for f in sorted((BACKEND / "migrations").glob("*.sql")):
            if not f.name.startswith("039_"):
                shutil.copy(f, before / f.name)
        runner = _load_runner()
        monkeypatch.setattr(runner, "MIGRATIONS_DIR", before)
        assert runner.main() == 0

        # A tenant written at 05:00 UTC by a connection set to Kenyan time.
        conn = psycopg2.connect(url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Africa/Nairobi'")
            cur.execute("INSERT INTO finyl_dcp.tenants (name, code, created_at) "
                        "VALUES ('Z', 'ZZ', '2026-11-09 05:00:00')")
        shutil.copy(BACKEND / "migrations" / "039_times_with_time_zone.sql", before)
        assert runner.main() == 0
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Africa/Nairobi'")
            cur.execute("SELECT created_at AT TIME ZONE 'UTC', to_char(created_at, 'HH24:MI') "
                        "FROM finyl_dcp.tenants WHERE code = 'ZZ'")
            utc_value, local_clock = cur.fetchone()
            cur.execute("SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = 'finyl_dcp' "
                        "AND data_type = 'timestamp without time zone'")
            remaining = cur.fetchone()[0]
        conn.close()
        assert utc_value == datetime(2026, 11, 9, 5, 0)
        assert local_clock == "08:00"                     # shown correctly in Kenya
        assert remaining == 0
        assert runner.main() == 0                         # nothing left to apply
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


# =========================================================================== #
# A report missed while the servers were down is sent when they are back
# =========================================================================== #
def test_missed_weekly_report_is_caught_up_once(w, monkeypatch):
    from app.services import mailer, scheduler
    sent = []
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email",
                        lambda to, subject, body, attachments=None: sent.append(body) or True)
    mon = datetime(2026, 11, 9, 7, 0, tzinfo=NBO)
    utc = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)   # noqa: E731
    sch = ReportSchedule(tenant_id=w.A.id, name="Weekly book", report_type="loan_book",
                         frequency="weekly", recipients="ops@a.dcp", active=True,
                         created_at=utc(mon - timedelta(days=20)),
                         last_run_at=utc(mon - timedelta(days=7, minutes=-10)))
    w.db.add(sch)
    w.db.commit()
    # Servers were down all Monday; back on Tuesday 10:00.
    tue = mon + timedelta(days=1, hours=3)
    assert scheduler.run_report_schedules(now_local=tue) == {"sent": 1, "failed": 0}
    assert "was due on Monday 09 Nov 07:00 and is sent late" in sent[0]
    # Not sent again on the following runs that week.
    assert scheduler.run_report_schedules(now_local=tue + timedelta(hours=1)) == {"sent": 0, "failed": 0}
    assert scheduler.run_report_schedules(now_local=tue + timedelta(days=3)) == {"sent": 0, "failed": 0}
    # A schedule created on Wednesday waits for the next Monday.
    new = ReportSchedule(tenant_id=w.A.id, name="New", report_type="loan_book",
                         frequency="weekly", recipients="ops@a.dcp", active=True,
                         created_at=utc(mon + timedelta(days=2)))
    w.db.add(new)
    w.db.commit()
    assert not scheduler._schedule_due(new, mon + timedelta(days=4))
    assert scheduler._schedule_due(new, mon + timedelta(days=7, hours=1))


# =========================================================================== #
# Reminder SMS quote the amount that clears the loan today
# =========================================================================== #
def test_reminder_sms_quotes_the_payoff_amount(w):
    from app.services import sms
    today = date.today()
    loan = w.seed.make_loan(status="active", principal="10000.00", interest_rate="10.0",
                            outstanding_balance="11000.00",
                            disbursement_date=today - timedelta(days=2),
                            due_date=today + timedelta(days=26))
    w.db.commit()
    assert "payoff_amount" in sms.CANONICAL_PLACEHOLDERS
    ctx = sms._base_context(w.db, w.A.id, w.seed.borrower, loan)
    payoff = float(ctx["payoff_amount"].replace(",", ""))
    assert 10000 < payoff < 11000                       # most of the interest is rebated
    text, _ = sms.render_template(w.db, w.A.id, "repayment_reminder", ctx)
    assert f"To clear it today pay KES {ctx['payoff_amount']}" in text


def test_dashboard_figures_work_with_zoned_times(w):
    """The dashboard reads times through pandas and compares them with plain
    dates; zoned values must not break it (seen in the browser run)."""
    from app.models import Repayment
    from app.services import analytics
    today = date.today()
    loan = w.seed.make_loan(status="active", outstanding_balance="5000.00",
                            disbursement_date=today - timedelta(days=10),
                            due_date=today + timedelta(days=18))
    w.db.add(Repayment(tenant_id=w.A.id, loan_id=loan.id, amount=500,
                       payment_date=datetime.utcnow() - timedelta(days=1)))
    w.db.commit()
    reps = analytics.load_repayments_df(w.db, w.A.id)
    assert reps["payment_date"].dt.tz is None
    trend = analytics.monthly_trend(analytics.load_loans_df(w.db, w.A.id), reps)
    assert trend and sum(m.get("collections", m.get("collected", 0)) or 0 for m in trend) >= 0
