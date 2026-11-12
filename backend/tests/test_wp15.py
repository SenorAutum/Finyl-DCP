"""
WP15 - HR and Kenyan payroll: employees (sensitive numbers encrypted), leave with
balances and manager approval, attendance, appraisals, payroll with NSSF, SHIF,
Housing Levy, NITA and PAYE (editable rates), posting to the books, salary files,
statutory returns, P9 and self-service.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import ChartOfAccount, HrEmployee, HrPayrollRun, Staff, TenantModule, User
from app.services import ledger as L
from app.services import payroll as P


@pytest.fixture()
def w(seed, db):
    import app.main as main_mod

    for key in ("accounting", "hrm"):
        db.add(TenantModule(tenant_id=seed.tenant.id, module_key=key, enabled=True))
    hrm = seed.make_user("hr_manager", "hr@test.dcp")
    fm = seed.make_user("finance_manager", "fm15@test.dcp")
    ro = seed.make_user("relationship_officer", "ro15@test.dcp")
    bm = seed.make_user("branch_manager", "bm15@test.dcp")
    db.commit()
    L.ensure_books(db, seed.tenant.id)
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app, raise_server_exceptions=True)

    def hire(first, salary, user=None, manager=None, hired=None, **kw):
        body = {"first_name": first, "last_name": "Test", "hire_date": (hired or date(2025, 1, 1)).isoformat(),
                "basic_salary": salary, "kra_pin": "A123456789B", "national_id": "29876543", "nssf_no": "NS1",
                "shif_no": "SH1", "pay_method": "bank", "bank_name": "KCB", "bank_account": "1100223344",
                "user_id": getattr(user, "id", None), "manager_employee_id": manager, **kw}
        r = api(hrm).post("/api/v1/hr/employees", json=body)
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def bal(code):
        a = db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == seed.tenant.id, ChartOfAccount.code == code).one()
        return L.account_balance(db, seed.tenant.id, a.id)

    yield SimpleNamespace(seed=seed, db=db, T=seed.tenant, hrm=hrm, fm=fm, ro=ro, bm=bm, api=api, hire=hire, bal=bal)
    main_mod.app.dependency_overrides.clear()


def last_month():
    return (L.today().replace(day=1) - timedelta(days=1)).replace(day=1)


# =========================================================================== calculation
@pytest.mark.parametrize("basic,nssf,shif,housing,paye,net", [
    (50000, "3000.00", "1375.00", "750.00", "5845.85", "39029.15"),
    (15000, "900.00", "412.50", "225.00", "0.00", "13462.50"),
    (150000, "6480.00", "4125.00", "2250.00", "33526.85", "103618.15"),
])
def test_statutory_deductions_match_the_2026_rules(basic, nssf, shif, housing, paye, net):
    c = P.calculate(P.DEFAULT_STATUTORY, basic=basic, allowances=[], deductions=[], factor=Decimal(1))
    assert (c["nssf_employee"], c["shif"], c["housing_employee"], c["paye"], c["net"]) == \
        tuple(Decimal(x) for x in (nssf, shif, housing, paye, net))


def test_pension_insurance_and_non_taxable_allowance():
    c = P.calculate(P.DEFAULT_STATUTORY, basic=100000,
                    allowances=[{"name": "House", "amount": 20000, "taxable": True},
                                {"name": "Mileage refund", "amount": 5000, "taxable": False}],
                    deductions=[{"name": "Pension", "amount": 30000, "pre_tax": True},
                                {"name": "Life cover", "amount": 2000, "insurance": True}], factor=Decimal(1))
    assert c["gross"] == Decimal("125000.00") and c["taxable_gross"] == Decimal("120000.00")
    # NSSF 6,480 + pension 30,000 -> only 30,000 deductible
    assert c["taxable_pay"] == Decimal("120000.00") - Decimal("30000") - c["shif"] - c["housing_employee"]
    assert c["insurance_relief"] == Decimal("300.00")
    assert c["net"] == c["gross"] - c["nssf_employee"] - c["shif"] - c["housing_employee"] - c["paye"] - Decimal("32000")


# =========================================================================== employees
def test_employee_numbers_and_sensitive_fields_are_encrypted(w):
    eid = w.hire("Achieng", 60000)
    e = w.db.get(HrEmployee, eid)
    assert e.employee_no == "EMP-0001" and e.kra_pin == "A123456789B"
    raw = w.db.execute(text("SELECT national_id, bank_account, kra_pin FROM hr_employees WHERE id=:i"), {"i": eid}).one()
    assert all(v and "29876543" not in v and "1100223344" not in v for v in raw)
    assert w.api(w.ro).get("/api/v1/hr/employees").status_code == 403
    bad = w.api(w.hrm).post("/api/v1/hr/employees", json={"first_name": "X", "last_name": "Y", "hire_date": "2025-01-01",
                                                          "basic_salary": 1, "kra_pin": "123"})
    assert bad.status_code == 422


def test_staff_record_salary_follows_the_employee(w):
    eid = w.hire("Otieno", 45000, staff_id=w.seed.staff.id)
    assert w.db.get(Staff, w.seed.staff.id).salary == Decimal("45000.00")
    w.api(w.hrm).put(f"/api/v1/hr/employees/{eid}", json={"first_name": "Otieno", "last_name": "Test",
                                                         "hire_date": "2025-01-01", "basic_salary": 47000})
    assert w.db.get(Staff, w.seed.staff.id).salary == Decimal("47000.00")


# =========================================================================== leave
def test_leave_request_balance_and_manager_approval(w):
    boss = w.hire("Boss", 120000, user=w.bm)
    staff = w.hire("Wanjiku", 40000, user=w.ro, manager=boss, gender="female")
    types = {t["name"]: t["id"] for t in w.api(w.ro).get("/api/v1/hr/leave-types").json()["types"]}
    # A Monday-Friday week far enough ahead
    start = L.today() + timedelta(days=(7 - L.today().weekday()) + 7)
    r = w.api(w.ro).post("/api/v1/hr/leave/requests", json={"leave_type_id": types["Annual leave"],
                                                           "start_date": start.isoformat(),
                                                           "end_date": (start + timedelta(days=4)).isoformat()})
    assert r.status_code == 200 and r.json()["days"] in (4.0, 5.0)
    rid = r.json()["id"]
    assert w.api(w.ro).post(f"/api/v1/hr/leave/requests/{rid}/approve", json={}).status_code == 403   # not own
    overlap = w.api(w.ro).post("/api/v1/hr/leave/requests", json={"leave_type_id": types["Sick leave"],
                                                                 "start_date": start.isoformat(),
                                                                 "end_date": start.isoformat()})
    assert overlap.status_code == 409
    assert w.api(w.bm).post(f"/api/v1/hr/leave/requests/{rid}/approve", json={}).status_code == 200    # manager
    me = w.api(w.ro).get("/api/v1/hr/me").json()
    annual = next(b for b in me["leave"] if b["name"] == "Annual leave")
    assert annual["used"] == r.json()["days"]
    assert all(b["name"] != "Paternity leave" for b in me["leave"])
    too_long = w.api(w.ro).post("/api/v1/hr/leave/requests", json={
        "leave_type_id": types["Compassionate leave"], "start_date": (start + timedelta(days=14)).isoformat(),
        "end_date": (start + timedelta(days=28)).isoformat()})
    assert too_long.status_code == 422
    assert staff


def test_unpaid_leave_and_absence_reduce_pay(w):
    eid = w.hire("Kamau", 31000, hired=date(2024, 1, 1))
    types = {t["name"]: t["id"] for t in w.api(w.hrm).get("/api/v1/hr/leave-types").json()["types"]}
    m = last_month()
    tue = m + timedelta(days=(1 - m.weekday()) % 7 + 7)          # the second Tuesday: Tue + Wed are working days
    r = w.api(w.hrm).post("/api/v1/hr/leave/requests", json={"employee_id": eid, "leave_type_id": types["Unpaid leave"],
                                                             "start_date": tue.isoformat(),
                                                             "end_date": (tue + timedelta(days=1)).isoformat()})
    assert r.status_code == 200, r.text
    rid = w.api(w.hrm).get("/api/v1/hr/leave/requests").json()["requests"][0]["id"]
    assert w.api(w.fm).post(f"/api/v1/hr/leave/requests/{rid}/approve", json={}).status_code == 403   # no leave right
    w.db.query(User).filter(User.id == w.fm.id).update({"role": "director"})
    w.db.commit()
    assert w.api(w.fm).post(f"/api/v1/hr/leave/requests/{rid}/approve", json={}).status_code == 200
    run = w.api(w.hrm).post("/api/v1/hr/payroll/runs", json={"period": m.strftime("%Y-%m")}).json()["id"]
    slip = w.api(w.hrm).get(f"/api/v1/hr/payroll/runs/{run}").json()["payslips"][0]
    first, last = P.month_bounds(m)
    wd = float(P.working_days(P.DEFAULT_STATUTORY, first, last))
    assert slip["unpaid_days"] == 2 and slip["basic"] == round(31000 * (1 - 2 / wd), 2)


# =========================================================================== payroll
def test_payroll_prepare_approve_pay_and_remit(w):
    w.hire("Achieng", 50000, branch_id=w.seed.branch.id)
    b = w.hire("Baraka", 150000)
    w.api(w.hrm).post(f"/api/v1/hr/employees/{b}/pay-items", json={"kind": "allowance", "name": "House", "amount": 20000})
    w.api(w.hrm).post(f"/api/v1/hr/employees/{b}/pay-items", json={"kind": "deduction", "name": "SACCO", "amount": 5000,
                                                                   "taxable": False})
    m = last_month()
    r = w.api(w.hrm).post("/api/v1/hr/payroll/runs", json={"period": m.strftime("%Y-%m")})
    assert r.status_code == 200
    rid = r.json()["id"]
    assert w.api(w.hrm).post("/api/v1/hr/payroll/runs", json={"period": m.strftime("%Y-%m")}).status_code == 409
    run = w.api(w.hrm).get(f"/api/v1/hr/payroll/runs/{rid}").json()
    assert run["employees"] == 2 and run["status"] == "draft"
    t = run["totals"]
    assert t["gross"] == 220000.0 and t["nita"] == 100.0
    assert w.api(w.hrm).post(f"/api/v1/hr/payroll/runs/{rid}/approve").status_code == 403          # no right
    assert w.api(w.fm).post(f"/api/v1/hr/payroll/runs/{rid}/approve").status_code == 200
    assert w.bal("6000") == Decimal("220000.00")
    assert w.bal("6010") == Decimal(str(t["nssf_employer"] + t["housing_employer"] + t["nita"])).quantize(Decimal("0.01"))
    assert -w.bal("2200") == Decimal(str(t["paye"])).quantize(Decimal("0.01"))
    assert -w.bal("2260") == Decimal("5000.00")
    assert -w.bal("2250") == Decimal(str(t["net"])).quantize(Decimal("0.01"))
    assert w.api(w.hrm).post(f"/api/v1/hr/payroll/runs/{rid}/recalculate").status_code == 409
    bank = w.db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == w.T.id, ChartOfAccount.code == "1010").one()
    assert w.api(w.fm).post(f"/api/v1/hr/payroll/runs/{rid}/pay", json={"paid_on": L.today().isoformat(),
                                                                        "account_id": bank.id}).status_code == 200
    assert w.bal("2250") == 0
    csv = w.api(w.hrm).get(f"/api/v1/hr/payroll/runs/{rid}/salary-file.csv").text
    assert "1100223344" in csv and "KCB" in csv
    for kind in ("paye", "nssf", "shif", "housing"):
        rows = w.api(w.hrm).get(f"/api/v1/hr/payroll/runs/{rid}/returns/{kind}.csv").text.strip().splitlines()
        assert len(rows) == 3, kind
    owed = {b["key"]: b["owed"] for b in w.api(w.hrm).get("/api/v1/hr/payroll/statutory").json()["balances"]}
    assert owed["paye"] == t["paye"]
    pay = w.api(w.fm).post("/api/v1/hr/payroll/remit", json={"body": "paye", "amount": t["paye"],
                                                             "paid_on": L.today().isoformat(), "account_id": bank.id,
                                                             "reference": "KRA-PRN-1"})
    assert pay.status_code == 200 and w.bal("2200") == 0
    assert w.api(w.fm).post("/api/v1/hr/payroll/remit", json={"body": "nssf", "amount": 10**7, "paid_on": L.today().isoformat(),
                                                             "account_id": bank.id}).status_code == 422
    assert L.trial_balance(w.db, w.T.id, date(2000, 1, 1), L.today() + timedelta(days=40))["balanced"]


def test_self_service_sees_only_own_final_payslips_and_p9(w):
    mine = w.hire("Njeri", 80000, user=w.ro)
    other = w.hire("Other", 90000)
    m = last_month()
    rid = w.api(w.hrm).post("/api/v1/hr/payroll/runs", json={"period": m.strftime("%Y-%m")}).json()["id"]
    assert w.api(w.ro).get("/api/v1/hr/me").json()["payslips"] == []                      # draft: not shown
    w.api(w.fm).post(f"/api/v1/hr/payroll/runs/{rid}/approve")
    slips = w.api(w.ro).get("/api/v1/hr/me").json()["payslips"]
    assert len(slips) == 1
    slip = w.api(w.ro).get(f"/api/v1/hr/payroll/payslips/{slips[0]['id']}").json()
    assert slip["employee_id"] == mine and slip["net"] > 0
    run = w.api(w.hrm).get(f"/api/v1/hr/payroll/runs/{rid}").json()
    other_slip = next(p for p in run["payslips"] if p["employee_id"] == other)
    assert w.api(w.ro).get(f"/api/v1/hr/payroll/payslips/{other_slip['id']}").status_code == 404
    p9 = w.api(w.ro).get(f"/api/v1/hr/payroll/p9/{mine}", params={"year": m.year}).json()
    assert p9["totals"]["paye"] == slip["paye"]
    assert w.api(w.ro).get(f"/api/v1/hr/payroll/p9/{other}").status_code == 404


def test_statutory_rates_are_editable_per_dcp(w):
    r = w.api(w.hrm).put("/api/v1/hr/settings", json={"statutory": {"shif_rate": 3, "personal_relief": 2500}})
    assert r.status_code == 200 and r.json()["statutory"]["shif_rate"] == 3
    prev = w.api(w.hrm).post("/api/v1/hr/payroll/preview", json={"basic": 50000}).json()
    assert prev["shif"] == 1500.0
    assert w.api(w.hrm).put("/api/v1/hr/settings", json={"statutory": {"paye_bands": [[24000, 10], [None, 30]], "nssf_rate": -1}}).status_code == 422
    assert w.api(w.ro).put("/api/v1/hr/settings", json={"statutory": {"shif_rate": 1}}).status_code == 403
    r = w.api(w.hrm).put("/api/v1/hr/settings", json={"reset_statutory": True})
    assert r.json()["statutory"]["shif_rate"] == 2.75


def test_attendance_and_appraisal(w):
    eid = w.hire("Mutua", 30000, user=w.ro)
    day = L.today() - timedelta(days=1)
    assert w.api(w.hrm).put("/api/v1/hr/attendance", json={"day": day.isoformat(), "entries": [
        {"employee_id": eid, "status": "present", "clock_in": "08:05", "clock_out": "17:10"}]}).status_code == 200
    reg = w.api(w.hrm).get("/api/v1/hr/attendance", params={"day": day.isoformat()}).json()
    assert reg["rows"][0]["status"] == "present"
    assert w.api(w.hrm).put("/api/v1/hr/attendance", json={"day": (L.today() + timedelta(days=2)).isoformat(),
                                                           "entries": []}).status_code == 422
    a = w.api(w.hrm).post("/api/v1/hr/appraisals", json={"employee_id": eid, "period": "2026 H2", "goals": [
        {"goal": "Disburse KES 2m", "weight": 60, "score": 4}, {"goal": "PAR30 under 5%", "weight": 40, "score": 3}]})
    assert a.status_code == 200 and a.json()["score"] == 3.6
    aid = a.json()["id"]
    assert w.api(w.ro).get("/api/v1/hr/me").json()["appraisals"] == []                    # still a draft
    w.api(w.hrm).post(f"/api/v1/hr/appraisals/{aid}/share")
    assert w.api(w.ro).post(f"/api/v1/hr/appraisals/{aid}/acknowledge", json={"comment": "Agreed"}).status_code == 200
    assert w.api(w.hrm).put(f"/api/v1/hr/appraisals/{aid}", json={"employee_id": eid, "period": "xx", "goals": []}).status_code == 409


def test_leaver_is_paid_to_their_last_day_and_not_after(w):
    m = last_month()
    eid = w.hire("Leaver", 30000, hired=date(2024, 1, 1))
    w.api(w.hrm).post(f"/api/v1/hr/employees/{eid}/terminate", json={"date": (m + timedelta(days=9)).isoformat(),
                                                                      "reason": "Resigned with notice"})
    rid = w.api(w.hrm).post("/api/v1/hr/payroll/runs", json={"period": m.strftime("%Y-%m")}).json()["id"]
    slip = w.api(w.hrm).get(f"/api/v1/hr/payroll/runs/{rid}").json()["payslips"][0]
    assert slip["days_paid"] == 10
    nxt = (L.today().replace(day=1))
    rid2 = w.api(w.hrm).post("/api/v1/hr/payroll/runs", json={"period": nxt.strftime("%Y-%m")}).json()["id"]
    assert w.db.get(HrPayrollRun, rid2).employees == 0
