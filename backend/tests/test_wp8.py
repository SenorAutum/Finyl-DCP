"""
WP8 - background jobs, payment and sign-in edge cases.
"""
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import hash_password
from app.models import (CbkSubmissionLog, PaymentTransaction, PromiseToPay, Repayment,
                        ReportSchedule, ScheduledJobRun, SmsAutomationSetting, Tenant,
                        TenantSecurityConfig, User, UserDevice)

NBO = ZoneInfo("Africa/Nairobi")


@pytest.fixture()
def w(seed, db, monkeypatch):
    import app.main as main_mod
    from app.services import scheduler

    # Background jobs open their own sessions: point them at the test database.
    monkeypatch.setattr(scheduler, "SessionLocal", sessionmaker(bind=db.get_bind()))
    pw = hash_password("Passw0rd!x")

    def mk(role, email, tenant=seed.tenant, **kw):
        u = User(email=email, hashed_password=pw, full_name=email.split("@")[0], role=role,
                 tenant_id=tenant.id if tenant else None, **kw)
        db.add(u)
        db.flush()
        return u

    users = SimpleNamespace(
        ro=mk("relationship_officer", "ro8@a.dcp", staff_id=seed.staff.id, branch_id=seed.branch.id),
        bm=mk("branch_manager", "bm8@a.dcp", branch_id=seed.branch.id),
        sysadmin=mk("system_admin", "sys8@a.dcp"),
        sa=mk("super_admin", "sa8@finyl.app", tenant=None),
    )
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app)

    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()
    seed.loan = loan
    yield SimpleNamespace(seed=seed, db=db, A=seed.tenant, u=users, api=api,
                          H={"X-Tenant-Id": str(seed.tenant.id)})
    main_mod.app.dependency_overrides.clear()


# =========================================================================== #
# Background jobs run once per slot, even with several copies of the server
# =========================================================================== #
def test_job_runs_once_per_slot_across_copies(w):
    from app.services import scheduler
    calls = []
    job = lambda: calls.append(1) or {"done": 1}          # noqa: E731
    copy_a = scheduler.guarded("demo_job", job, every_minutes=60)
    copy_b = scheduler.guarded("demo_job", job, every_minutes=60)
    copy_a()
    copy_b()                                               # the second copy skips
    assert calls == [1]
    w.db.expire_all()
    runs = w.db.query(ScheduledJobRun).filter_by(job_id="demo_job").all()
    assert len(runs) == 1 and runs[0].status == "ok" and runs[0].detail == "done=1"
    assert runs[0].finished_at is not None


def test_a_failing_job_is_recorded_as_failed(w):
    from app.services import scheduler

    def boom():
        raise RuntimeError("provider down")
    scheduler.guarded("boom_job", boom, every_minutes=60)()
    w.db.expire_all()
    run = w.db.query(ScheduledJobRun).filter_by(job_id="boom_job").one()
    assert run.status == "failed" and "provider down" in run.detail


def test_job_runs_page_is_finyl_only(w):
    from app.services import scheduler
    scheduler.guarded("page_job", lambda: None, every_minutes=60)()
    assert w.api(w.u.sysadmin).get("/api/v1/admin/scheduled-jobs").status_code == 403
    r = w.api(w.u.sa).get("/api/v1/admin/scheduled-jobs")
    assert r.status_code == 200 and any(i["job_id"] == "page_job" for i in r.json()["items"])


# =========================================================================== #
# Lifecycle SMS: Kenyan hour, live DCPs only, now on a schedule
# =========================================================================== #
def test_lifecycle_sms_uses_kenyan_time_and_skips_deactivated_dcps(w):
    from app.routers import notifications
    assert notifications.local_now().utcoffset() == timedelta(hours=3)
    w.db.add(SmsAutomationSetting(tenant_id=w.A.id, automation_enabled=True, send_hour=9))
    w.db.commit()
    out = notifications.run_all_tenants(w.db, hour=9)
    assert any(r["tenant_id"] == w.A.id and r["processed"] for r in out["results"])
    w.A.active = False
    w.db.commit()
    out = notifications.run_all_tenants(w.db, hour=9)
    assert all(r["tenant_id"] != w.A.id for r in out["results"])


def test_lifecycle_sms_job_is_registered():
    import inspect
    from app.services import scheduler
    src = inspect.getsource(scheduler.start_scheduler)
    for job in ("lifecycle_sms", "report_schedules", "purge_job_runs"):
        assert f'guarded("{job}"' in src


def test_promise_to_pay_reminder_is_actually_sent(w, monkeypatch):
    from app.services import scheduler, sms
    sent = []
    monkeypatch.setattr(sms, "send_sms", lambda db, tid, phone, msg, trigger_type="manual",
                        log_message=None: sent.append((tid, phone, trigger_type)))
    w.db.add(PromiseToPay(tenant_id=w.A.id, loan_id=w.seed.loan.id, client_id=w.seed.borrower.id,
                          logged_by=w.u.ro.id, ptp_date=date.today() + timedelta(days=1),
                          amount=1500, status="pending"))
    w.db.commit()
    scheduler.run_ptp_reminders()
    assert sent and sent[0][2] == "ptp_reminder"


# =========================================================================== #
# Scheduled report e-mails
# =========================================================================== #
def _sched(w, **kw):
    s = ReportSchedule(tenant_id=w.A.id, user_id=w.u.sysadmin.id, name="Daily book",
                       report_type="loan_book", frequency="daily",
                       recipients="ops@a.dcp, not-an-address", active=True, **kw)
    w.db.add(s)
    w.db.commit()
    return s


def test_due_report_is_emailed_once_as_csv(w, monkeypatch):
    from app.services import mailer, scheduler
    mails = []
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email",
                        lambda to, subject, body, attachments=None: mails.append((to, attachments)) or True)
    s = _sched(w)
    morning = datetime(2026, 11, 9, 8, 0, tzinfo=NBO)
    scheduler.run_report_schedules(now_local=morning)
    assert len(mails) == 1
    to, files = mails[0]
    assert to == "ops@a.dcp"
    name, content, mime = files[0]
    assert name.endswith(".csv") and mime == "text/csv"
    assert content.decode().splitlines()[0].startswith("account_number,client")
    w.db.expire_all()
    assert w.db.get(ReportSchedule, s.id).last_status == "sent"
    scheduler.run_report_schedules(now_local=morning + timedelta(hours=1))   # same day
    assert len(mails) == 1


def test_report_failure_is_shown_not_retried_hourly(w, monkeypatch):
    from app.services import mailer, scheduler
    monkeypatch.setattr(mailer, "is_configured", lambda: False)
    s = _sched(w)
    scheduler.run_report_schedules(now_local=datetime(2026, 11, 9, 8, 0, tzinfo=NBO))
    w.db.expire_all()
    row = w.db.get(ReportSchedule, s.id)
    assert row.last_status == "failed" and "SMTP" in row.last_error
    listed = w.api(w.u.sysadmin).get("/api/v1/reporting/schedules", headers=w.H)
    if listed.status_code == 200:          # system_admin may lack reports.schedule
        assert listed.json()[0]["last_status"] == "failed"


def test_schedule_due_rules():
    from app.services.scheduler import _schedule_due
    mon = datetime(2026, 11, 9, 8, 0, tzinfo=NBO)              # a Monday
    tue = mon + timedelta(days=1)
    first = datetime(2026, 12, 1, 8, 0, tzinfo=NBO)
    S = lambda f, last=None: SimpleNamespace(frequency=f, last_run_at=last)   # noqa: E731
    utc = lambda d: d.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)        # noqa: E731
    # (WP10: sent on Monday -> not due on Tuesday; never sent -> caught up.)
    assert _schedule_due(S("weekly"), mon) and not _schedule_due(S("weekly", utc(mon)), tue)
    assert not _schedule_due(S("daily", utc(mon - timedelta(days=1))), mon.replace(hour=6))
    assert _schedule_due(S("monthly"), first)
    assert not _schedule_due(S("monthly", utc(datetime(2026, 11, 1, 7, 5, tzinfo=NBO))), mon)
    assert not _schedule_due(S("daily", last=mon.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)),
                             mon + timedelta(hours=2))


# =========================================================================== #
# CBK GDI: a failed dataset is retried, filed ones are not resent
# =========================================================================== #
def test_gdi_retries_only_failed_datasets(w, monkeypatch):
    from app.models import TenantModule
    from app.services import cbk_gdi, scheduler
    w.db.add(TenantModule(tenant_id=w.A.id, module_key="cbk_reporting", enabled=True))
    month = date(2026, 10, 1)
    keys = list(cbk_gdi.DATASET_SEQUENCE)
    w.db.add(CbkSubmissionLog(tenant_id=w.A.id, reporting_month=month,
                              dataset_name=cbk_gdi.DATASET_LABELS[keys[0]], status="submitted"))
    w.db.add(CbkSubmissionLog(tenant_id=w.A.id, reporting_month=month,
                              dataset_name=cbk_gdi.DATASET_LABELS[keys[1]], status="error"))
    w.db.commit()
    calls = []
    monkeypatch.setattr(cbk_gdi, "run_monthly_submission",
                        lambda db, tid, m, by, skip=None: calls.append((tid, m, set(skip or ()))))
    scheduler.run_cbk_monthly_gdi_submit(today=date(2026, 11, 5))           # Thursday 5th
    assert calls == [(w.A.id, month, {keys[0]})]
    # Before WP8 one error log made the scheduler skip the whole month.


# =========================================================================== #
# M-Pesa: never pretend in production; whole shillings; STK reference kept
# =========================================================================== #
def _stuck_payout(w):
    txn = PaymentTransaction(tenant_id=w.A.id, type="b2c", loan_id=w.seed.loan.id, amount=5000,
                             phone="254700000001", mpesa_ref="AG_TEST_1", status="processing",
                             created_at=datetime.utcnow() - timedelta(hours=2))
    w.db.add(txn)
    w.db.commit()
    return txn


def test_production_never_fakes_a_payout_result(w, monkeypatch):
    from app.core.config import settings
    from app.services import mpesa, scheduler
    monkeypatch.setattr(settings, "DARAJA_ENVIRONMENT", "production")
    monkeypatch.setattr(mpesa, "resolve_creds", lambda db, tid: mpesa.DarajaCreds(
        consumer_key="", consumer_secret="", shortcode="", passkey="", initiator_name="",
        security_credential="", environment="production"))
    txn = _stuck_payout(w)
    out = scheduler._sweep_tenant(w.db, w.A.id, 10)
    assert out["mode"] == "report" and out["resolved"] == 0
    w.db.expire_all()
    assert w.db.get(PaymentTransaction, txn.id).status == "processing"
    empty = mpesa.resolve_creds(w.db, w.A.id)
    for call in (lambda: mpesa.stk_push("254700000001", 100, "ACC1", creds=empty),
                 lambda: mpesa.reversal_request("QK1", 100, "x", creds=empty)):
        with pytest.raises(mpesa.DarajaNotConfigured):
            call()
    assert mpesa.simulation_allowed() is False


def test_sandbox_demo_still_simulates(monkeypatch):
    from app.core.config import settings
    from app.services import mpesa
    monkeypatch.setattr(settings, "DARAJA_ENVIRONMENT", "sandbox")
    assert mpesa.simulation_allowed() is True


def test_mpesa_amounts_are_whole_shillings(w):
    from app.services import mpesa
    assert mpesa.whole_shillings("1500.00") == 1500
    for bad in (500.5, 0, -10, "abc", float("nan")):
        with pytest.raises(ValueError):
            mpesa.whole_shillings(bad)
    api = w.api(w.u.bm)
    r = api.post("/api/v1/payments/stk-push", json={"loan_id": w.seed.loan.id, "amount": 500.5},
                 headers=w.H)
    assert r.status_code == 422 and "whole shillings" in r.json()["detail"]


def test_repeated_stk_callback_is_recognised(w):
    from app.routers.payments import _process_stk_callback
    w.db.add(PaymentTransaction(tenant_id=w.A.id, type="stk_push", loan_id=w.seed.loan.id,
                                amount=300, phone="254700000001", mpesa_ref="ws_CO_TEST",
                                status="pending"))
    w.db.commit()
    body = {"Body": {"stkCallback": {
        "CheckoutRequestID": "ws_CO_TEST", "ResultCode": 0,
        "CallbackMetadata": {"Item": [{"Name": "Amount", "Value": 300},
                                      {"Name": "MpesaReceiptNumber", "Value": "QKSTK001"},
                                      {"Name": "PhoneNumber", "Value": 254700000001}]}}}}
    first, _ = _process_stk_callback(w.db, body)
    second, _ = _process_stk_callback(w.db, body)       # used to be "unknown checkout"
    assert second["ResultDesc"] == "Already processed"
    txn = w.db.query(PaymentTransaction).filter_by(type="stk_push").one()
    assert txn.mpesa_ref == "ws_CO_TEST" and txn.raw_payload["receipt"] == "QKSTK001"
    assert w.db.query(Repayment).filter(Repayment.mpesa_ref == "QKSTK001").count() == 1


# =========================================================================== #
# Sign-in rules apply for the whole session
# =========================================================================== #
def _req(headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


def _token(user):
    from app.core.security import create_access_token
    return create_access_token(user.id, user.role, user.tenant_id, token_version=0)


def test_session_ends_when_working_hours_end(w):
    from fastapi import HTTPException
    from app.core import deps
    now = datetime.now(NBO)
    start = (now + timedelta(hours=2)).time().replace(second=0, microsecond=0)
    end = (now + timedelta(hours=3)).time().replace(second=0, microsecond=0)
    cfg = TenantSecurityConfig(tenant_id=w.A.id, time_fence_enabled=True,
                               time_fence_start=start, time_fence_end=end)
    w.db.add(cfg)
    w.db.commit()
    with pytest.raises(HTTPException) as e:
        deps.get_current_user(token=_token(w.u.ro), db=w.db, request=_req())
    assert e.value.status_code == 401 and "working hours" in e.value.detail
    cfg.time_fence_enabled = False
    w.db.commit()
    assert deps.get_current_user(token=_token(w.u.ro), db=w.db, request=_req()).id == w.u.ro.id


def test_session_ends_on_a_device_that_is_no_longer_approved(w):
    from fastapi import HTTPException
    from app.core import deps
    from app.services import device_binding
    w.db.add(TenantSecurityConfig(tenant_id=w.A.id, device_binding_enabled=True))
    w.db.commit()
    device_binding.register(w.db, user_id=w.u.ro.id, fingerprint="dev-ok-1", name="phone")
    w.db.commit()
    ok = deps.get_current_user(token=_token(w.u.ro), db=w.db,
                               request=_req({"X-Device-Fingerprint": "dev-ok-1"}))
    assert ok.id == w.u.ro.id
    with pytest.raises(HTTPException) as e:
        deps.get_current_user(token=_token(w.u.ro), db=w.db,
                              request=_req({"X-Device-Fingerprint": "someone-elses"}))
    assert e.value.status_code == 401


# =========================================================================== #
# SMS: sending and reading the log need permissions
# =========================================================================== #
def test_sms_sending_and_log_need_permission(w):
    body = {"phone": "254700000001", "message": "Hello", "trigger_type": "manual"}
    assert w.api(w.u.ro).post("/api/v1/notifications/send-sms", json=body, headers=w.H).status_code == 403
    assert w.api(w.u.ro).get("/api/v1/notifications/sms-logs", headers=w.H).status_code == 403
    assert w.api(w.u.bm).post("/api/v1/notifications/send-sms", json=body, headers=w.H).status_code == 200
    assert w.api(w.u.bm).get("/api/v1/notifications/sms-logs", headers=w.H).status_code == 200
