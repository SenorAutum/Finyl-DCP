"""WP15 — Kenyan payroll.

Statutory rules (defaults; every DCP can change them in HR -> Settings when the
law changes — nothing here needs a code change):

  NSSF (NSSF Act 2013, year 4 from 1 February 2026)
      6% employee + 6% employer of pensionable pay up to the upper earnings limit
      (KES 108,000): Tier I on the first KES 9,000, Tier II on the rest.
  SHIF (Social Health Insurance Act 2023)     2.75% of gross pay, at least KES 300.
  Affordable Housing Levy                     1.5% employee + 1.5% employer of gross pay.
  NITA                                        KES 50 per employee per month (employer).
  PAYE (Income Tax Act, unchanged by the Finance Act 2026)
      monthly bands 10% to 24,000; 25% next 8,333; 30% next 467,667;
      32.5% next 300,000; 35% above 800,000. Personal relief KES 2,400.
      Insurance relief 15% of premiums, at most KES 5,000 a month.
      NSSF, SHIF and the Housing Levy are deducted before PAYE (Tax Laws
      (Amendment) Act 2024); NSSF + registered pension at most KES 30,000.
      A KRA disability exemption certificate exempts the first KES 150,000.

Pay: a month's basic pay and fixed allowances are reduced in proportion to the
calendar days not employed (joined or left mid-month), and by basic / working
days in the month for each unpaid working day (unpaid leave, and absences if
the DCP chooses).
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.money import D, money
from app.models import (HrAttendance, HrEmployee, HrLeaveRequest, HrLeaveType, HrPayItem, HrPayrollRun,
                        HrPayslip, HrSettings, Staff)
from app.services import ledger as L

ZERO = D("0.00")

DEFAULT_STATUTORY = {
    "paye_bands": [[24000, 10], [8333, 25], [467667, 30], [300000, 32.5], [None, 35]],
    "personal_relief": 2400,
    "insurance_relief_rate": 15,
    "insurance_relief_cap": 5000,
    "pension_deduction_cap": 30000,
    "disability_exemption": 150000,
    "nssf_rate": 6,
    "nssf_lower_limit": 9000,
    "nssf_upper_limit": 108000,
    "shif_rate": 2.75,
    "shif_minimum": 300,
    "housing_levy_rate": 1.5,
    "nita_per_employee": 50,
    "public_holidays": ["01-01", "05-01", "06-01", "10-10", "10-20", "12-12", "12-25", "12-26"],
    "effective_from": "2026-02-01",
}
LABELS = {
    "paye_bands": "PAYE monthly bands [band width, rate %] (last band width empty = everything above)",
    "personal_relief": "Personal relief (KES a month)",
    "insurance_relief_rate": "Insurance relief (% of premiums)",
    "insurance_relief_cap": "Insurance relief limit (KES a month)",
    "pension_deduction_cap": "NSSF + pension deductible limit (KES a month)",
    "disability_exemption": "Disability exemption (KES a month)",
    "nssf_rate": "NSSF rate, each of employee and employer (%)",
    "nssf_lower_limit": "NSSF lower earnings limit (Tier I, KES)",
    "nssf_upper_limit": "NSSF upper earnings limit (KES)",
    "shif_rate": "SHIF rate (% of gross)",
    "shif_minimum": "SHIF minimum (KES)",
    "housing_levy_rate": "Housing Levy, each of employee and employer (%)",
    "nita_per_employee": "NITA levy (KES per employee per month)",
    "public_holidays": "Fixed public holidays (MM-DD), not counted as leave days",
    "effective_from": "These rates apply from",
}


def settings_for(db: Session, tenant_id: int, lock: bool = False) -> HrSettings:
    q = db.query(HrSettings).filter(HrSettings.tenant_id == tenant_id)
    s = (q.with_for_update().first() if lock else q.first())
    if s is None:
        s = HrSettings(tenant_id=tenant_id, statutory={}, deduct_absence=False, payroll_approval=True,
                       next_employee_no=1, employee_prefix="EMP")
        db.add(s)
        db.flush()
        if lock:
            s = q.with_for_update().first()
    return s


def statutory(s: HrSettings) -> dict:
    return {**DEFAULT_STATUTORY, **(s.statutory or {})}


def validate_statutory(values: dict) -> dict:
    out = {}
    for k, v in values.items():
        if k not in DEFAULT_STATUTORY:
            raise HTTPException(422, f"Unknown statutory setting '{k}'")
        if k == "paye_bands":
            if not isinstance(v, list) or not v:
                raise HTTPException(422, "PAYE bands must be a list of [width, rate]")
            bands = []
            for i, b in enumerate(v):
                width, rate = (b + [None, None])[:2] if isinstance(b, list) else (None, None)
                last = i == len(v) - 1
                if rate is None or not (0 <= float(rate) <= 100):
                    raise HTTPException(422, "Every PAYE band needs a rate between 0 and 100")
                if not last and (width is None or float(width) <= 0):
                    raise HTTPException(422, "Every PAYE band except the last needs a width above 0")
                bands.append([None if last else float(width), float(rate)])
            out[k] = bands
        elif k == "public_holidays":
            if not isinstance(v, list) or not all(isinstance(x, str) and len(x) == 5 and x[2] == "-" for x in v):
                raise HTTPException(422, "Public holidays are MM-DD, e.g. 12-25")
            out[k] = v
        elif k == "effective_from":
            out[k] = str(v)
        else:
            try:
                n = float(v)
            except (TypeError, ValueError):
                raise HTTPException(422, f"{LABELS.get(k, k)} must be a number")
            if n < 0:
                raise HTTPException(422, f"{LABELS.get(k, k)} cannot be negative")
            out[k] = n
    return out


# --------------------------------------------------------------------------- the calculation
def paye_on(taxable: Decimal, bands) -> Decimal:
    tax, left = ZERO, max(D(taxable), ZERO)
    for width, rate in bands:
        portion = left if width is None else min(left, D(width))
        tax += portion * D(rate) / D(100)
        left -= portion
        if left <= 0:
            break
    return money(tax)


def calculate(st: dict, *, basic, allowances: list[dict], deductions: list[dict], factor: Decimal,
              disability_exempt: bool = False) -> dict:
    """One month's pay for one person. ``factor`` = share of the month paid (0..1)."""
    basic = money(D(basic) * factor)
    items, taxable_gross, gross = [], basic, basic
    for a in allowances:
        amt = money(D(a["amount"]) * factor)
        if amt <= 0:
            continue
        gross += amt
        if a.get("taxable", True):
            taxable_gross += amt
        items.append({**a, "kind": "allowance", "amount": float(amt)})
    rate = D(st["nssf_rate"]) / D(100)
    lel, uel = D(st["nssf_lower_limit"]), D(st["nssf_upper_limit"])
    pensionable = taxable_gross
    tier1 = money(min(pensionable, lel) * rate)
    tier2 = money(max(ZERO, min(pensionable, uel) - lel) * rate)
    nssf = tier1 + tier2
    shif = money(max(taxable_gross * D(st["shif_rate"]) / D(100), D(st["shif_minimum"]))) if taxable_gross > 0 else ZERO
    housing = money(taxable_gross * D(st["housing_levy_rate"]) / D(100))
    pension = sum((money(d["amount"]) for d in deductions if d.get("pre_tax")), ZERO)
    pension_allowed = min(nssf + pension, D(st["pension_deduction_cap"]))
    taxable = money(max(ZERO, taxable_gross - pension_allowed - shif - housing))
    if disability_exempt:
        taxable = money(max(ZERO, taxable - D(st["disability_exemption"])))
    income_tax = paye_on(taxable, st["paye_bands"])
    premiums = sum((money(d["amount"]) for d in deductions if d.get("insurance")), ZERO)
    ins_relief = money(min(premiums * D(st["insurance_relief_rate"]) / D(100), D(st["insurance_relief_cap"])))
    relief = min(D(st["personal_relief"]), income_tax)
    ins_relief = min(ins_relief, income_tax - relief)
    paye = money(max(ZERO, income_tax - relief - ins_relief))
    other = ZERO
    for d in deductions:
        amt = money(d["amount"])
        if amt <= 0:
            continue
        other += amt
        items.append({**d, "kind": "deduction", "amount": float(amt)})
    net = money(gross - nssf - shif - housing - paye - other)
    return {"basic": basic, "items": items, "gross": gross, "taxable_gross": taxable_gross, "taxable_pay": taxable,
            "nssf_employee": nssf, "nssf_employer": nssf, "nssf_tier1": tier1, "nssf_tier2": tier2,
            "shif": shif, "housing_employee": housing, "housing_employer": housing, "pension": pension,
            "income_tax": income_tax, "personal_relief": money(relief), "insurance_relief": ins_relief, "paye": paye,
            "other_deductions": other, "nita": money(st["nita_per_employee"]), "net": net}


# --------------------------------------------------------------------------- days
def month_bounds(period: date) -> tuple[date, date]:
    first = period.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def working_days(st: dict, start: date, end: date) -> Decimal:
    """Monday to Friday, less fixed public holidays."""
    hol = set(st.get("public_holidays") or [])
    n, d = 0, start
    while d <= end:
        if d.weekday() < 5 and d.strftime("%m-%d") not in hol:
            n += 1
        d += timedelta(days=1)
    return D(n)


def unpaid_days(db, emp: HrEmployee, first: date, last: date, settings: HrSettings, st: dict) -> Decimal:
    """Unpaid working days in [first, last]: approved unpaid leave, and absences
    in the attendance register when the DCP deducts them."""
    days = ZERO
    unpaid_types = [t.id for t in db.query(HrLeaveType).filter(HrLeaveType.tenant_id == emp.tenant_id,
                                                                HrLeaveType.paid.is_(False))]
    if unpaid_types:
        for r in db.query(HrLeaveRequest).filter(HrLeaveRequest.employee_id == emp.id,
                                                  HrLeaveRequest.status == "approved",
                                                  HrLeaveRequest.leave_type_id.in_(unpaid_types),
                                                  HrLeaveRequest.start_date <= last, HrLeaveRequest.end_date >= first):
            days += working_days(st, max(r.start_date, first), min(r.end_date, last))
    if settings.deduct_absence:
        for a in db.query(HrAttendance).filter(HrAttendance.employee_id == emp.id, HrAttendance.day >= first,
                                               HrAttendance.day <= last, HrAttendance.status.in_(("absent", "half"))):
            days += D("1") if a.status == "absent" else D("0.5")
    return days


def items_for(db, emp: HrEmployee, first: date, last: date) -> tuple[list[dict], list[dict]]:
    allowances, deductions = [], []
    for it in db.query(HrPayItem).filter(HrPayItem.employee_id == emp.id, HrPayItem.active.is_(True)):
        if it.start_month and it.start_month > last:
            continue
        if it.end_month and it.end_month < first:
            continue
        row = {"name": it.name, "amount": D(it.amount), "taxable": bool(it.taxable), "pre_tax": bool(it.pre_tax),
               "insurance": bool(it.insurance), "account_id": it.account_id, "item_id": it.id}
        (allowances if it.kind == "allowance" else deductions).append(row)
    return allowances, deductions


def payslip_for(db, emp: HrEmployee, period: date, settings: HrSettings, st: dict) -> dict | None:
    first, last = month_bounds(period)
    start = max(emp.hire_date, first)
    end = min(emp.termination_date, last) if emp.termination_date else last
    if start > last or end < first or end < start:
        return None
    dim = calendar.monthrange(first.year, first.month)[1]
    employed = D((end - start).days + 1)
    # Joining or leaving mid-month: calendar days. Unpaid days: each working day
    # costs basic / working days in the month.
    wd_month = max(working_days(st, first, last), D(1))
    unpaid = min(unpaid_days(db, emp, start, end, settings, st), working_days(st, start, end))
    factor = max(D(0), employed / D(dim) - unpaid / wd_month)
    paid = employed
    allowances, deductions = items_for(db, emp, first, last)
    c = calculate(st, basic=emp.basic_salary or 0, allowances=allowances, deductions=deductions, factor=factor,
                  disability_exempt=bool(emp.disability_exempt))
    c.update(days_paid=paid, days_in_month=dim, unpaid_days=unpaid)
    for it in c["items"]:
        it["amount"] = float(it["amount"])
        it.pop("item_id", None)
    return c


# --------------------------------------------------------------------------- runs
SUM_KEYS = ("basic", "gross", "taxable_pay", "nssf_employee", "nssf_employer", "shif", "housing_employee",
            "housing_employer", "pension", "paye", "other_deductions", "nita", "net")


def build_run(db, run: HrPayrollRun) -> HrPayrollRun:
    """(Re)calculate every payslip of a draft run."""
    if run.status != "draft":
        raise HTTPException(409, "Only a draft payroll can be recalculated")
    settings = settings_for(db, run.tenant_id)
    st = statutory(settings)
    first, last = month_bounds(run.period)
    db.query(HrPayslip).filter(HrPayslip.run_id == run.id).delete()
    totals = {k: ZERO for k in SUM_KEYS}
    n = 0
    # Suspended staff stay on the payroll (suspension is with pay unless their pay items say otherwise).
    emps = (db.query(HrEmployee).filter(HrEmployee.tenant_id == run.tenant_id, HrEmployee.hire_date <= last)
            .order_by(HrEmployee.employee_no).all())
    for emp in emps:
        if emp.status == "terminated" and (emp.termination_date is None or emp.termination_date < first):
            continue
        c = payslip_for(db, emp, run.period, settings, st)
        if c is None or c["gross"] <= 0:
            continue
        db.add(HrPayslip(run_id=run.id, tenant_id=run.tenant_id, employee_id=emp.id, days_paid=c["days_paid"],
                         days_in_month=c["days_in_month"], unpaid_days=c["unpaid_days"], basic=c["basic"],
                         items=c["items"], gross=c["gross"], taxable_pay=c["taxable_pay"],
                         nssf_employee=c["nssf_employee"], nssf_employer=c["nssf_employer"], shif=c["shif"],
                         housing_employee=c["housing_employee"], housing_employer=c["housing_employer"],
                         pension=c["pension"], income_tax=c["income_tax"], personal_relief=c["personal_relief"],
                         insurance_relief=c["insurance_relief"], paye=c["paye"],
                         other_deductions=c["other_deductions"], nita=c["nita"], net=c["net"],
                         branch_id=emp.branch_id))
        for k in SUM_KEYS:
            totals[k] += D(c[k])
        n += 1
    run.employees = n
    run.totals = {k: float(money(v)) for k, v in totals.items()}
    run.totals["employer_cost"] = float(money(totals["gross"] + totals["nssf_employer"] + totals["housing_employer"]
                                              + totals["nita"]))
    db.flush()
    return run


def create_run(db, tenant_id: int, period: date, user) -> HrPayrollRun:
    period = period.replace(day=1)
    if db.query(HrPayrollRun.id).filter(HrPayrollRun.tenant_id == tenant_id, HrPayrollRun.period == period,
                                        HrPayrollRun.status != "cancelled").first():
        raise HTTPException(409, f"There is already a payroll for {period:%B %Y}")
    if period > L.today().replace(day=1) + timedelta(days=31):
        raise HTTPException(422, "Payroll can be prepared for this month or next month at most")
    run = HrPayrollRun(tenant_id=tenant_id, period=period, status="draft", employees=0, totals={},
                       created_by_user_id=getattr(user, "id", None))
    db.add(run)
    db.flush()
    return build_run(db, run)


def _pay_lines(db, run: HrPayrollRun, b: L.Books) -> list:
    """Accrual journal: expense by branch, every liability by kind."""
    by_branch = {}
    other_by_acct = {}
    tot = {k: ZERO for k in SUM_KEYS}
    for p in db.query(HrPayslip).filter(HrPayslip.run_id == run.id):
        br = by_branch.setdefault(p.branch_id, {"gross": ZERO, "employer": ZERO})
        br["gross"] += D(p.gross)
        br["employer"] += D(p.nssf_employer) + D(p.housing_employer) + D(p.nita)
        for k in SUM_KEYS:
            tot[k] += D(getattr(p, k))
        for it in p.items or []:
            if it.get("kind") == "deduction":
                key = it.get("account_id") or "default"
                other_by_acct[key] = other_by_acct.get(key, ZERO) + D(str(it["amount"]))
    desc = f"Payroll {run.period:%B %Y}"
    lines = []
    for br, v in by_branch.items():
        lines.append(L.line(b.account("salaries"), debit=v["gross"], branch_id=br, description=desc))
        lines.append(L.line(b.account("employer_contributions"), debit=v["employer"], branch_id=br,
                            description="Employer NSSF, Housing Levy, NITA"))
    lines += [L.line(b.account("paye_payable"), credit=tot["paye"], description="PAYE"),
              L.line(b.account("nssf_payable"), credit=tot["nssf_employee"] + tot["nssf_employer"], description="NSSF"),
              L.line(b.account("shif_payable"), credit=tot["shif"], description="SHIF"),
              L.line(b.account("housing_payable"), credit=tot["housing_employee"] + tot["housing_employer"],
                     description="Housing Levy"),
              L.line(b.account("nita_payable"), credit=tot["nita"], description="NITA"),
              L.line(b.account("net_pay_payable"), credit=tot["net"], description="Net salaries")]
    for key, amt in other_by_acct.items():
        if key == "default":
            lines.append(L.line(b.account("payroll_deductions"), credit=amt, description="Other deductions"))
        else:
            lines.append(L.Line(account_id=int(key), credit=money(amt), description="Deductions to this account"))
    return lines


def approve_run(db, run: HrPayrollRun, user) -> HrPayrollRun:
    if run.status != "draft":
        raise HTTPException(409, f"This payroll is {run.status}")
    settings = settings_for(db, run.tenant_id)
    if settings.payroll_approval and user.role != "super_admin" and run.created_by_user_id == user.id:
        raise HTTPException(403, "Someone other than the person who prepared the payroll must approve it")
    if not run.employees:
        raise HTTPException(422, "There is nobody to pay in this payroll")
    b = L.Books(db, run.tenant_id)
    _, last = month_bounds(run.period)
    j = L.post_journal(db, run.tenant_id, entry_date=last, description=f"Payroll {run.period:%B %Y}",
                       lines=_pay_lines(db, run, b), kind="auto", source_type="payroll", source_id=run.id,
                       user=user, reference=f"PAYROLL-{run.period:%Y-%m}", settings=b.settings, auto_date=True)
    run.journal_id, run.status = j.id, "approved"
    run.approved_by_user_id, run.approved_at = user.id, datetime.utcnow()
    db.flush()
    return run


def pay_run(db, run: HrPayrollRun, *, paid_on: date, account_id: int, user) -> HrPayrollRun:
    if run.status != "approved":
        raise HTTPException(409, "Approve the payroll before marking it paid")
    from app.services.payables import account_of
    acct = account_of(db, run.tenant_id, account_id, cash=True)
    b = L.Books(db, run.tenant_id)
    net = D(run.totals.get("net", 0))
    desc = f"Salaries paid, {run.period:%B %Y}"
    j = L.post_journal(db, run.tenant_id, entry_date=paid_on, description=desc,
                       lines=[L.line(b.account("net_pay_payable"), debit=net, description=desc),
                              L.line(acct, credit=net, description=desc)],
                       kind="auto", source_type="payroll_paid", source_id=run.id, user=user,
                       reference=f"PAYROLL-{run.period:%Y-%m}", settings=b.settings)
    run.payment_journal_id, run.status, run.paid_on, run.paid_from_account_id = j.id, "paid", paid_on, acct.id
    db.flush()
    return run


REMIT_ROLES = {"paye": "paye_payable", "nssf": "nssf_payable", "shif": "shif_payable", "housing": "housing_payable",
               "nita": "nita_payable", "other": "payroll_deductions"}
REMIT_LABELS = {"paye": "PAYE to KRA", "nssf": "NSSF", "shif": "SHIF to SHA", "housing": "Housing Levy to KRA",
                "nita": "NITA levy", "other": "Other deductions (SACCO, pension, insurance)"}


def statutory_balances(db, tenant_id) -> list[dict]:
    b = L.Books(db, tenant_id)
    out = []
    for k, role in REMIT_ROLES.items():
        a = b.account(role)
        out.append({"key": k, "label": REMIT_LABELS[k], "account": f"{a.code} {a.name}",
                    "owed": float(-L.account_balance(db, tenant_id, a.id))})
    return out


def remit(db, tenant_id, *, body: str, amount, paid_on: date, account_id: int, reference: str | None, user):
    if body not in REMIT_ROLES:
        raise HTTPException(422, f"Choose one of {', '.join(REMIT_ROLES)}")
    from app.services.payables import account_of
    acct = account_of(db, tenant_id, account_id, cash=True)
    amount = money(amount or 0)
    if amount <= 0:
        raise HTTPException(422, "Enter the amount paid")
    b = L.Books(db, tenant_id)
    owed = money(-L.account_balance(db, tenant_id, b.account(REMIT_ROLES[body]).id))
    if amount > owed:
        raise HTTPException(422, f"That is more than is owed ({owed:,.2f})")
    desc = f"{REMIT_LABELS[body]} paid" + (f" ({reference})" if reference else "")
    return L.post_journal(db, tenant_id, entry_date=paid_on, description=desc,
                          lines=[L.line(b.account(REMIT_ROLES[body]), debit=amount, description=desc),
                                 L.line(acct, credit=amount, description=desc)],
                          kind="auto", source_type="payroll_remittance", user=user, reference=reference,
                          settings=b.settings)


def sync_staff(db, emp: HrEmployee):
    """Keep the old staff record's salary (staff net-margin figures) in step."""
    if emp.staff_id:
        st = db.get(Staff, emp.staff_id)
        if st is not None and st.tenant_id == emp.tenant_id:
            st.salary = emp.basic_salary
            st.active = emp.status != "terminated"


def p9(db, emp: HrEmployee, year: int) -> dict:
    rows, tot = [], {}
    q = (db.query(HrPayslip, HrPayrollRun).join(HrPayrollRun, HrPayrollRun.id == HrPayslip.run_id)
         .filter(HrPayslip.employee_id == emp.id, HrPayrollRun.status.in_(("approved", "paid")),
                 func.extract("year", HrPayrollRun.period) == year).order_by(HrPayrollRun.period))
    keys = ("basic", "gross", "nssf_employee", "pension", "shif", "housing_employee", "taxable_pay", "income_tax",
            "personal_relief", "insurance_relief", "paye")
    for p, r in q:
        row = {"month": r.period.strftime("%B"), **{k: float(getattr(p, k)) for k in keys}}
        row["benefits"] = round(float(D(p.gross) - D(p.basic)), 2)
        rows.append(row)
        for k in list(keys) + ["benefits"]:
            tot[k] = round(tot.get(k, 0) + row[k], 2)
    return {"year": year, "rows": rows, "totals": tot}
