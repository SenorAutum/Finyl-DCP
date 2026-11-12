"""WP15 — human resources and payroll for each DCP (and Finyl's own staff).

/api/v1/hr/...   Gated by the 'hrm' module and these permissions:
  hr.view            employee records, leave, attendance, payroll (read)
  hr.manage          add/edit employees, pay items, documents, attendance, leave types, settings
  leave.approve      decide anyone's leave (a manager decides for their own team without it)
  payroll.run        prepare payroll, payslips, salary files, statutory returns
  payroll.approve    approve payroll prepared by someone else; record salary and statutory payments
  appraisals.manage  write and share appraisals
  hr.self            everyone: own profile, payslips, leave, appraisals
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_module, require_permission, write_audit
from app.core.money import D, money
from app.models import (Branch, ChartOfAccount, HrAppraisal, HrAttendance, HrDocument, HrEmployee,
                        HrLeaveBalance, HrLeaveRequest, HrLeaveType, HrPayItem, HrPayrollRun, HrPayslip, Staff,
                        Tenant, User)
from app.services import hr as H
from app.services import ledger as L
from app.services import payroll as P
from app.services import storage

router = APIRouter(prefix="/api/v1/hr", tags=["hr"])
MODULE = require_module("hrm")


def perm(*keys, mode="all"):
    return require_permission(*keys, mode=mode)


def _can(db, user, key) -> bool:
    if user.role == "super_admin":
        return True
    from app.services.authz import effective_permissions
    return key in effective_permissions(db, user.tenant_id, user.role)


def _d(value, field="date") -> date:
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        raise HTTPException(422, f"{field} must be a date (YYYY-MM-DD)")


def _n(v):
    return float(money(v or 0)) if v is not None else None


def _emp(db, tenant_id, emp_id, lock=False) -> HrEmployee:
    q = db.query(HrEmployee).filter(HrEmployee.id == emp_id, HrEmployee.tenant_id == tenant_id)
    e = (q.with_for_update() if lock else q).first()
    if e is None:
        raise HTTPException(404, "Employee not found")
    return e


def _mask(v, keep=3):
    v = v or ""
    return ("•" * max(0, len(v) - keep) + v[-keep:]) if v else None


def _emp_dict(e: HrEmployee, branches=None, managers=None, full=False):
    d = {"id": e.id, "employee_no": e.employee_no, "name": H.full_name(e), "first_name": e.first_name,
         "last_name": e.last_name, "other_names": e.other_names, "job_title": e.job_title,
         "department": e.department, "branch_id": e.branch_id, "branch": (branches or {}).get(e.branch_id),
         "employment_type": e.employment_type, "status": e.status, "hire_date": e.hire_date.isoformat(),
         "phone": e.phone, "email": e.email, "user_id": e.user_id, "staff_id": e.staff_id,
         "manager_employee_id": e.manager_employee_id, "manager": (managers or {}).get(e.manager_employee_id),
         "contract_end": e.contract_end.isoformat() if e.contract_end else None,
         "probation_end": e.probation_end.isoformat() if e.probation_end else None,
         "basic_salary": _n(e.basic_salary)}
    if full:
        d.update({"national_id": e.national_id, "kra_pin": e.kra_pin, "nssf_no": e.nssf_no, "shif_no": e.shif_no,
                  "date_of_birth": e.date_of_birth.isoformat() if e.date_of_birth else None, "gender": e.gender,
                  "termination_date": e.termination_date.isoformat() if e.termination_date else None,
                  "termination_reason": e.termination_reason, "pay_method": e.pay_method, "bank_name": e.bank_name,
                  "bank_branch": e.bank_branch, "bank_account": e.bank_account, "mpesa_phone": e.mpesa_phone,
                  "next_of_kin_name": e.next_of_kin_name, "next_of_kin_phone": e.next_of_kin_phone,
                  "disability_exempt": bool(e.disability_exempt)})
    return d


def _branches(db, tenant_id):
    return {b.id: b.name for b in db.query(Branch).filter(Branch.tenant_id == tenant_id)}


def _managers(db, tenant_id):
    return {e.id: H.full_name(e) for e in db.query(HrEmployee).filter(HrEmployee.tenant_id == tenant_id)}


# =========================================================================== overview
@router.get("/overview")
def overview(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(perm("hr.view"))):
    today = L.today()
    emps = db.query(HrEmployee).filter(HrEmployee.tenant_id == tenant_id).all()
    active = [e for e in emps if e.status != "terminated"]
    branches = _branches(db, tenant_id)
    by_branch, by_dept, by_type = {}, {}, {}
    for e in active:
        by_branch[branches.get(e.branch_id, "Head office")] = by_branch.get(branches.get(e.branch_id, "Head office"), 0) + 1
        by_dept[e.department or "—"] = by_dept.get(e.department or "—", 0) + 1
        by_type[e.employment_type] = by_type.get(e.employment_type, 0) + 1
    on_leave = (db.query(HrLeaveRequest, HrEmployee).join(HrEmployee, HrEmployee.id == HrLeaveRequest.employee_id)
                .filter(HrLeaveRequest.tenant_id == tenant_id, HrLeaveRequest.status == "approved",
                        HrLeaveRequest.start_date <= today, HrLeaveRequest.end_date >= today).all())
    pending = db.query(func.count(HrLeaveRequest.id)).filter(HrLeaveRequest.tenant_id == tenant_id,
                                                             HrLeaveRequest.status == "pending").scalar()
    soon = today + timedelta(days=60)
    contracts = [{"id": e.id, "name": H.full_name(e), "date": e.contract_end.isoformat()} for e in active
                 if e.contract_end and today <= e.contract_end <= soon]
    probation = [{"id": e.id, "name": H.full_name(e), "date": e.probation_end.isoformat()} for e in active
                 if e.probation_end and today <= e.probation_end <= today + timedelta(days=30)]
    docs = (db.query(HrDocument, HrEmployee).join(HrEmployee, HrEmployee.id == HrDocument.employee_id)
            .filter(HrDocument.tenant_id == tenant_id, HrDocument.expires_on.isnot(None),
                    HrDocument.expires_on <= soon).all())
    birthdays = [{"id": e.id, "name": H.full_name(e), "day": e.date_of_birth.day} for e in active
                 if e.date_of_birth and e.date_of_birth.month == today.month]
    last = (db.query(HrPayrollRun).filter(HrPayrollRun.tenant_id == tenant_id, HrPayrollRun.status != "cancelled")
            .order_by(HrPayrollRun.period.desc()).first())
    return {
        "headcount": len(active), "left": len(emps) - len(active),
        "by_branch": by_branch, "by_department": by_dept, "by_type": by_type,
        "monthly_basic": round(sum(float(e.basic_salary or 0) for e in active), 2),
        "on_leave_today": [{"id": e.id, "name": H.full_name(e), "until": r.end_date.isoformat()} for r, e in on_leave],
        "pending_leave": int(pending or 0), "contracts_ending": contracts, "probation_ending": probation,
        "documents_expiring": [{"employee_id": e.id, "name": H.full_name(e), "doc_type": d.doc_type,
                                "expires_on": d.expires_on.isoformat()} for d, e in docs],
        "birthdays": sorted(birthdays, key=lambda b: b["day"]),
        "last_payroll": {"id": last.id, "period": last.period.isoformat(), "status": last.status,
                         "totals": last.totals, "employees": last.employees} if last else None,
        "statutory": P.statutory_balances(db, tenant_id) if _can(db, _, "payroll.run") or _can(db, _, "payroll.approve") else None,
    }


# =========================================================================== employees
class EmployeeIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    other_names: str | None = None
    national_id: str | None = Field(default=None, max_length=20)
    kra_pin: str | None = Field(default=None, max_length=20)
    nssf_no: str | None = None
    shif_no: str | None = None
    phone: str | None = None
    email: str | None = None
    date_of_birth: str | None = None
    gender: str | None = None
    branch_id: int | None = None
    department: str | None = None
    job_title: str | None = None
    employment_type: str = "permanent"
    hire_date: str
    probation_end: str | None = None
    contract_end: str | None = None
    basic_salary: float = Field(ge=0)
    pay_method: str = "bank"
    bank_name: str | None = None
    bank_branch: str | None = None
    bank_account: str | None = None
    mpesa_phone: str | None = None
    manager_employee_id: int | None = None
    next_of_kin_name: str | None = None
    next_of_kin_phone: str | None = None
    disability_exempt: bool = False
    user_id: int | None = None
    staff_id: int | None = None


DATE_FIELDS = ("date_of_birth", "hire_date", "probation_end", "contract_end")


def _apply_employee(db, tenant_id, e: HrEmployee, data: dict):
    if data.get("employment_type") and data["employment_type"] not in ("permanent", "contract", "casual", "intern"):
        raise HTTPException(422, "employment_type must be permanent, contract, casual or intern")
    if data.get("pay_method") and data["pay_method"] not in ("bank", "mpesa", "cash"):
        raise HTTPException(422, "pay_method must be bank, mpesa or cash")
    if data.get("gender") and data["gender"] not in ("female", "male", "other"):
        raise HTTPException(422, "gender must be female, male or other")
    if data.get("kra_pin"):
        pin = data["kra_pin"].strip().upper()
        if len(pin) != 11 or not (pin[0].isalpha() and pin[-1].isalpha() and pin[1:10].isdigit()):
            raise HTTPException(422, "A KRA PIN looks like A123456789B")
        data["kra_pin"] = pin
    if data.get("branch_id"):
        br = db.get(Branch, data["branch_id"])
        if br is None or br.tenant_id != tenant_id:
            raise HTTPException(422, "Branch not found")
    if data.get("manager_employee_id"):
        m = db.get(HrEmployee, data["manager_employee_id"])
        if m is None or m.tenant_id != tenant_id or m.id == e.id:
            raise HTTPException(422, "Choose another employee of this DCP as the manager")
        if e.id and m.id in H.team_ids(db, e):
            raise HTTPException(422, "That person reports to this employee: choose someone above them")
    if data.get("user_id"):
        u = db.get(User, data["user_id"])
        if u is None or u.tenant_id != tenant_id:
            raise HTTPException(422, "That login does not belong to this DCP")
        other = db.query(HrEmployee.id).filter(HrEmployee.tenant_id == tenant_id, HrEmployee.user_id == u.id,
                                               HrEmployee.id != (e.id or -1)).first()
        if other:
            raise HTTPException(409, "That login is already linked to another employee")
    if data.get("staff_id"):
        st = db.get(Staff, data["staff_id"])
        if st is None or st.tenant_id != tenant_id:
            raise HTTPException(422, "That staff record does not belong to this DCP")
    for k in DATE_FIELDS:
        if k in data:
            data[k] = _d(data[k], k) if data[k] else None
    if "hire_date" in data and data["hire_date"] is None:
        raise HTTPException(422, "The hire date is needed")
    for k, v in data.items():
        setattr(e, k, v.strip() if isinstance(v, str) else v)
    if e.contract_end and e.contract_end < e.hire_date:
        raise HTTPException(422, "The contract cannot end before the hire date")


@router.get("/employees")
def employees(status: str | None = "current", q: str | None = None, branch_id: int | None = None,
              tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(perm("hr.view"))):
    qry = db.query(HrEmployee).filter(HrEmployee.tenant_id == tenant_id)
    if status == "current":
        qry = qry.filter(HrEmployee.status != "terminated")
    elif status:
        qry = qry.filter(HrEmployee.status == status)
    if branch_id:
        qry = qry.filter(HrEmployee.branch_id == branch_id)
    if q:
        like = f"%{q.strip()}%"
        qry = qry.filter(or_(HrEmployee.first_name.ilike(like), HrEmployee.last_name.ilike(like),
                             HrEmployee.employee_no.ilike(like), HrEmployee.job_title.ilike(like)))
    branches, managers = _branches(db, tenant_id), _managers(db, tenant_id)
    return {"employees": [_emp_dict(e, branches, managers) for e in qry.order_by(HrEmployee.employee_no)]}


@router.get("/link-options")
def link_options(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(perm("hr.manage"))):
    """Logins and staff records that can be linked to an employee."""
    linked_users = {u for (u,) in db.query(HrEmployee.user_id).filter(HrEmployee.tenant_id == tenant_id,
                                                                     HrEmployee.user_id.isnot(None))}
    linked_staff = {s for (s,) in db.query(HrEmployee.staff_id).filter(HrEmployee.tenant_id == tenant_id,
                                                                      HrEmployee.staff_id.isnot(None))}
    return {"users": [{"id": u.id, "name": u.full_name, "email": u.email, "role": u.role, "linked": u.id in linked_users}
                      for u in db.query(User).filter(User.tenant_id == tenant_id, User.active.is_(True)).order_by(User.full_name)],
            "staff": [{"id": s.id, "name": s.name, "role": s.role, "linked": s.id in linked_staff}
                      for s in db.query(Staff).filter(Staff.tenant_id == tenant_id).order_by(Staff.name)],
            "branches": [{"id": i, "name": n} for i, n in sorted(_branches(db, tenant_id).items(), key=lambda x: x[1])],
            "employees": [{"id": i, "name": n} for i, n in sorted(_managers(db, tenant_id).items(), key=lambda x: x[1])]}


@router.get("/employees/{emp_id}")
def employee(emp_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
             _: User = Depends(perm("hr.view"))):
    e = _emp(db, tenant_id, emp_id)
    d = _emp_dict(e, _branches(db, tenant_id), _managers(db, tenant_id), full=True)
    d["pay_items"] = [_item_dict(i) for i in db.query(HrPayItem).filter(HrPayItem.employee_id == e.id)
                      .order_by(HrPayItem.kind, HrPayItem.id)]
    d["leave"] = H.balances(db, e)
    d["leave_requests"] = [_leave_dict(r) for r in db.query(HrLeaveRequest).filter(HrLeaveRequest.employee_id == e.id)
                           .order_by(HrLeaveRequest.start_date.desc()).limit(50)]
    d["documents"] = [_doc_dict(x) for x in db.query(HrDocument).filter(HrDocument.employee_id == e.id)
                      .order_by(HrDocument.id.desc())]
    d["appraisals"] = [_appraisal_dict(a) for a in db.query(HrAppraisal).filter(HrAppraisal.employee_id == e.id)
                       .order_by(HrAppraisal.id.desc())]
    d["payslips"] = _payslip_list(db, e.id)
    d["team"] = [{"id": x.id, "name": H.full_name(x)} for x in db.query(HrEmployee)
                 .filter(HrEmployee.manager_employee_id == e.id, HrEmployee.status != "terminated")]
    return d


@router.post("/employees")
def create_employee(body: EmployeeIn, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(perm("hr.manage"))):
    e = HrEmployee(tenant_id=tenant_id, status="active")
    data = body.model_dump()
    _apply_employee(db, tenant_id, e, data)
    e.employee_no = H.next_employee_no(db, tenant_id)
    db.add(e)
    db.flush()
    P.sync_staff(db, e)
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.employee_created", entity_type="employee",
                entity_id=e.id, details={"employee_no": e.employee_no, "name": H.full_name(e)}, request=request)
    db.commit()
    return {"id": e.id, "employee_no": e.employee_no}


@router.put("/employees/{emp_id}")
def update_employee(emp_id: int, body: EmployeeIn, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(perm("hr.manage"))):
    e = _emp(db, tenant_id, emp_id, lock=True)
    before = {"basic_salary": float(e.basic_salary or 0), "job_title": e.job_title, "branch_id": e.branch_id}
    data = body.model_dump(exclude_unset=True)
    _apply_employee(db, tenant_id, e, data)
    P.sync_staff(db, e)
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.employee_updated", entity_type="employee",
                entity_id=e.id, details={"before": before, "changed": sorted(data)}, request=request)
    db.commit()
    return {"status": "saved"}


class TerminateIn(BaseModel):
    date: str
    reason: str = Field(min_length=5, max_length=1000)


@router.post("/employees/{emp_id}/terminate")
def terminate(emp_id: int, body: TerminateIn, request: Request, tenant_id: int = Depends(MODULE),
              db: Session = Depends(get_db), user: User = Depends(perm("hr.manage"))):
    e = _emp(db, tenant_id, emp_id, lock=True)
    if e.status == "terminated":
        raise HTTPException(409, "This person has already left")
    on = _d(body.date)
    if on < e.hire_date:
        raise HTTPException(422, "The last day cannot be before the hire date")
    e.status, e.termination_date, e.termination_reason = "terminated", on, body.reason
    P.sync_staff(db, e)
    for r in db.query(HrLeaveRequest).filter(HrLeaveRequest.employee_id == e.id, HrLeaveRequest.status == "pending"):
        r.status, r.decision_note = "cancelled", "Employee left"
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.employee_terminated", entity_type="employee",
                entity_id=e.id, details={"date": on.isoformat(), "reason": body.reason}, request=request)
    db.commit()
    return {"status": "terminated"}


@router.post("/employees/{emp_id}/status")
def set_status(emp_id: int, body: dict, request: Request, tenant_id: int = Depends(MODULE),
               db: Session = Depends(get_db), user: User = Depends(perm("hr.manage"))):
    e = _emp(db, tenant_id, emp_id, lock=True)
    st = body.get("status")
    if st not in ("active", "suspended"):
        raise HTTPException(422, "status must be active or suspended (use 'Record leaving' for someone who left)")
    if e.status == "terminated":
        raise HTTPException(409, "This person has left")
    e.status = st
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.employee_status", entity_type="employee",
                entity_id=e.id, details={"status": st, "note": body.get("note")}, request=request)
    db.commit()
    return {"status": st}


# --------------------------------------------------------------------------- pay items
class PayItemIn(BaseModel):
    kind: str
    name: str = Field(min_length=2, max_length=80)
    amount: float = Field(gt=0)
    taxable: bool = True
    pre_tax: bool = False
    insurance: bool = False
    account_id: int | None = None
    start_month: str | None = None
    end_month: str | None = None
    active: bool = True


def _item_dict(i: HrPayItem):
    return {"id": i.id, "kind": i.kind, "name": i.name, "amount": _n(i.amount), "taxable": i.taxable,
            "pre_tax": i.pre_tax, "insurance": i.insurance, "account_id": i.account_id,
            "start_month": i.start_month.isoformat()[:7] if i.start_month else None,
            "end_month": i.end_month.isoformat()[:7] if i.end_month else None, "active": i.active}


def _month(v):
    if not v:
        return None
    v = str(v)
    return _d(v + "-01" if len(v) == 7 else v, "month").replace(day=1)


def _check_item(db, tenant_id, body: PayItemIn):
    if body.kind not in ("allowance", "deduction"):
        raise HTTPException(422, "kind must be allowance or deduction")
    if body.kind == "allowance" and (body.pre_tax or body.insurance):
        raise HTTPException(422, "Pension and insurance flags are for deductions")
    if body.pre_tax and body.insurance:
        raise HTTPException(422, "A deduction is either a pension contribution or an insurance premium")
    if body.account_id:
        a = db.get(ChartOfAccount, body.account_id)
        if a is None or a.tenant_id != tenant_id or a.type not in ("asset", "liability"):
            raise HTTPException(422, "A deduction goes to a receivable (e.g. staff advances) or a payable account")


@router.post("/employees/{emp_id}/pay-items")
def add_item(emp_id: int, body: PayItemIn, request: Request, tenant_id: int = Depends(MODULE),
             db: Session = Depends(get_db), user: User = Depends(perm("hr.manage"))):
    e = _emp(db, tenant_id, emp_id)
    _check_item(db, tenant_id, body)
    i = HrPayItem(tenant_id=tenant_id, employee_id=e.id, **{**body.model_dump(), "start_month": _month(body.start_month),
                                                            "end_month": _month(body.end_month)})
    db.add(i)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.pay_item_added", entity_type="employee",
                entity_id=e.id, details={"kind": i.kind, "name": i.name, "amount": float(i.amount)}, request=request)
    db.commit()
    return {"id": i.id}


@router.put("/pay-items/{item_id}")
def edit_item(item_id: int, body: PayItemIn, request: Request, tenant_id: int = Depends(MODULE),
              db: Session = Depends(get_db), user: User = Depends(perm("hr.manage"))):
    i = db.get(HrPayItem, item_id)
    if i is None or i.tenant_id != tenant_id:
        raise HTTPException(404, "Pay item not found")
    _check_item(db, tenant_id, body)
    for k, v in body.model_dump().items():
        setattr(i, k, _month(v) if k in ("start_month", "end_month") else v)
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.pay_item_updated", entity_type="employee",
                entity_id=i.employee_id, details={"name": i.name, "amount": float(i.amount), "active": i.active},
                request=request)
    db.commit()
    return {"status": "saved"}


# =========================================================================== leave
def _leave_dict(r: HrLeaveRequest, names=None, types=None):
    return {"id": r.id, "employee_id": r.employee_id, "employee": (names or {}).get(r.employee_id),
            "leave_type_id": r.leave_type_id, "leave_type": (types or {}).get(r.leave_type_id),
            "start_date": r.start_date.isoformat(), "end_date": r.end_date.isoformat(), "days": float(r.days),
            "reason": r.reason, "status": r.status, "decision_note": r.decision_note,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None}


class LeaveTypeIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    days_per_year: float = Field(ge=0, le=366)
    paid: bool = True
    accrues: bool = False
    carry_forward_max: float = Field(default=0, ge=0)
    gender: str | None = None
    needs_document: bool = False
    calendar_days: bool = False
    active: bool = True


@router.get("/leave-types")
def leave_types(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(perm("hr.self"))):
    H.ensure_leave_types(db, tenant_id)
    db.commit()
    return {"types": [{"id": t.id, "name": t.name, "days_per_year": float(t.days_per_year), "paid": t.paid,
                       "accrues": t.accrues, "carry_forward_max": float(t.carry_forward_max), "gender": t.gender,
                       "needs_document": t.needs_document, "calendar_days": t.calendar_days, "active": t.active}
                      for t in db.query(HrLeaveType).filter(HrLeaveType.tenant_id == tenant_id).order_by(HrLeaveType.id)]}


@router.post("/leave-types")
def add_leave_type(body: LeaveTypeIn, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(perm("hr.manage"))):
    if body.gender not in (None, "", "female", "male"):
        raise HTTPException(422, "gender must be female, male or empty")
    if db.query(HrLeaveType.id).filter(HrLeaveType.tenant_id == tenant_id, HrLeaveType.name == body.name).first():
        raise HTTPException(409, "A leave type with this name exists")
    t = HrLeaveType(tenant_id=tenant_id, **{**body.model_dump(), "gender": body.gender or None})
    db.add(t)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.leave_type_added", entity_type="leave_type",
                entity_id=t.id, details=body.model_dump(), request=request)
    db.commit()
    return {"id": t.id}


@router.put("/leave-types/{type_id}")
def edit_leave_type(type_id: int, body: LeaveTypeIn, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(perm("hr.manage"))):
    t = db.get(HrLeaveType, type_id)
    if t is None or t.tenant_id != tenant_id:
        raise HTTPException(404, "Leave type not found")
    for k, v in body.model_dump().items():
        setattr(t, k, (v or None) if k == "gender" else v)
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.leave_type_updated", entity_type="leave_type",
                entity_id=t.id, details=body.model_dump(), request=request)
    db.commit()
    return {"status": "saved"}


class LeaveIn(BaseModel):
    employee_id: int | None = None
    leave_type_id: int
    start_date: str
    end_date: str
    reason: str | None = Field(default=None, max_length=1000)


def _can_decide(db, user, tenant_id, r: HrLeaveRequest) -> bool:
    me = H.employee_for_user(db, tenant_id, user)
    if me and me.id == r.employee_id:
        return False                                 # never your own leave
    if _can(db, user, "leave.approve"):
        return True
    return bool(me and r.employee_id in H.team_ids(db, me))


@router.get("/leave/requests")
def leave_requests(status: str | None = None, employee_id: int | None = None, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(perm("hr.self"))):
    q = db.query(HrLeaveRequest).filter(HrLeaveRequest.tenant_id == tenant_id)
    all_view = _can(db, user, "hr.view") or _can(db, user, "leave.approve")
    me = H.employee_for_user(db, tenant_id, user)
    if not all_view:
        allowed = H.team_ids(db, me) | ({me.id} if me else set())
        q = q.filter(HrLeaveRequest.employee_id.in_(allowed or {-1}))
    if status:
        q = q.filter(HrLeaveRequest.status == status)
    if employee_id:
        q = q.filter(HrLeaveRequest.employee_id == employee_id)
    rows = q.order_by(HrLeaveRequest.start_date.desc()).limit(500).all()
    names = _managers(db, tenant_id)
    types = {t.id: t.name for t in db.query(HrLeaveType).filter(HrLeaveType.tenant_id == tenant_id)}
    out = []
    for r in rows:
        d = _leave_dict(r, names, types)
        d["can_decide"] = r.status == "pending" and _can_decide(db, user, tenant_id, r)
        out.append(d)
    return {"requests": out, "me": me.id if me else None}


@router.post("/leave/requests")
def request_leave(body: LeaveIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                  user: User = Depends(perm("hr.self"))):
    me = H.employee_for_user(db, tenant_id, user)
    if body.employee_id and (me is None or body.employee_id != me.id):
        if not _can(db, user, "hr.manage"):
            raise HTTPException(403, "You can only apply for your own leave")
        e = _emp(db, tenant_id, body.employee_id)
    else:
        if me is None:
            raise HTTPException(409, "Your login is not linked to an employee record yet: ask HR to link it")
        e = me
    lt = db.get(HrLeaveType, body.leave_type_id)
    if lt is None:
        raise HTTPException(404, "Leave type not found")
    start, end = _d(body.start_date, "start_date"), _d(body.end_date, "end_date")
    st = P.statutory(P.settings_for(db, tenant_id))
    days = H.leave_days(lt, start, end, st)
    H.check_request(db, e, lt, start, end, days)
    r = HrLeaveRequest(tenant_id=tenant_id, employee_id=e.id, leave_type_id=lt.id, start_date=start, end_date=end,
                       days=days, reason=body.reason, status="pending", requested_by_user_id=user.id)
    db.add(r)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.leave_requested", entity_type="employee",
                entity_id=e.id, details={"type": lt.name, "start": start.isoformat(), "end": end.isoformat(),
                                         "days": float(days)}, request=request)
    db.commit()
    return {"id": r.id, "days": float(days)}


class DecideIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _leave_for(db, tenant_id, rid) -> HrLeaveRequest:
    r = db.query(HrLeaveRequest).filter(HrLeaveRequest.id == rid, HrLeaveRequest.tenant_id == tenant_id) \
        .with_for_update().first()
    if r is None:
        raise HTTPException(404, "Leave request not found")
    return r


@router.post("/leave/requests/{rid}/{action}")
def decide_leave(rid: int, action: str, body: DecideIn, request: Request, tenant_id: int = Depends(MODULE),
                 db: Session = Depends(get_db), user: User = Depends(perm("hr.self"))):
    r = _leave_for(db, tenant_id, rid)
    if action == "cancel":
        me = H.employee_for_user(db, tenant_id, user)
        own = me and me.id == r.employee_id
        if not (own or _can(db, user, "hr.manage")):
            raise HTTPException(403, "Only the employee or HR can cancel this leave")
        if r.status not in ("pending", "approved"):
            raise HTTPException(409, f"This request is {r.status}")
        if r.status == "approved" and r.start_date <= L.today() and not _can(db, user, "hr.manage"):
            raise HTTPException(409, "Leave that has started can only be changed by HR")
        r.status, r.decision_note = "cancelled", body.note
    elif action in ("approve", "reject"):
        if r.status != "pending":
            raise HTTPException(409, f"This request is {r.status}")
        if not _can_decide(db, user, tenant_id, r):
            raise HTTPException(403, "Only the employee's manager or someone with leave approval can decide this")
        if action == "reject" and not (body.note and len(body.note.strip()) >= 3):
            raise HTTPException(422, "Say why the leave is not approved")
        r.status = "approved" if action == "approve" else "rejected"
        r.decided_by_user_id, r.decided_at, r.decision_note = user.id, datetime.utcnow(), body.note
    else:
        raise HTTPException(404, "Unknown action")
    write_audit(db, tenant_id=tenant_id, user=user, action=f"hr.leave_{r.status}", entity_type="employee",
                entity_id=r.employee_id, details={"request": r.id, "note": body.note}, request=request)
    db.commit()
    return {"status": r.status}


class BalanceIn(BaseModel):
    employee_id: int
    leave_type_id: int
    year: int
    carried: float = 0
    adjustment: float = 0
    note: str | None = None


@router.put("/leave/balances")
def set_balance(body: BalanceIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                user: User = Depends(perm("hr.manage"))):
    e = _emp(db, tenant_id, body.employee_id)
    lt = db.get(HrLeaveType, body.leave_type_id)
    if lt is None or lt.tenant_id != tenant_id:
        raise HTTPException(404, "Leave type not found")
    if body.carried > float(lt.carry_forward_max or 0) and body.carried > 0:
        raise HTTPException(422, f"At most {float(lt.carry_forward_max):g} days of {lt.name.lower()} can be carried forward")
    b = db.query(HrLeaveBalance).filter(HrLeaveBalance.employee_id == e.id, HrLeaveBalance.leave_type_id == lt.id,
                                        HrLeaveBalance.year == body.year).first()
    if b is None:
        b = HrLeaveBalance(tenant_id=tenant_id, employee_id=e.id, leave_type_id=lt.id, year=body.year)
        db.add(b)
    b.carried, b.adjustment, b.note = body.carried, body.adjustment, body.note
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.leave_balance_set", entity_type="employee",
                entity_id=e.id, details=body.model_dump(), request=request)
    db.commit()
    return {"leave": H.balances(db, e, body.year)}


# =========================================================================== attendance
ATT_STATUSES = ("present", "absent", "half", "leave", "off", "holiday")


class AttEntry(BaseModel):
    employee_id: int
    status: str
    clock_in: str | None = None
    clock_out: str | None = None
    note: str | None = Field(default=None, max_length=200)


class AttIn(BaseModel):
    day: str
    entries: list[AttEntry]


@router.get("/attendance")
def attendance(day: str | None = None, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
               _: User = Depends(perm("hr.view"))):
    d = _d(day) if day else L.today()
    marks = {a.employee_id: a for a in db.query(HrAttendance).filter(HrAttendance.tenant_id == tenant_id,
                                                                     HrAttendance.day == d)}
    on_leave = {r.employee_id for r in db.query(HrLeaveRequest).filter(
        HrLeaveRequest.tenant_id == tenant_id, HrLeaveRequest.status == "approved",
        HrLeaveRequest.start_date <= d, HrLeaveRequest.end_date >= d)}
    branches = _branches(db, tenant_id)
    rows = []
    for e in db.query(HrEmployee).filter(HrEmployee.tenant_id == tenant_id, HrEmployee.hire_date <= d,
                                         or_(HrEmployee.termination_date.is_(None), HrEmployee.termination_date >= d)) \
            .order_by(HrEmployee.first_name):
        a = marks.get(e.id)
        rows.append({"employee_id": e.id, "name": H.full_name(e), "branch": branches.get(e.branch_id),
                     "status": a.status if a else ("leave" if e.id in on_leave else None),
                     "clock_in": a.clock_in if a else None, "clock_out": a.clock_out if a else None,
                     "note": a.note if a else None, "on_leave": e.id in on_leave})
    return {"day": d.isoformat(), "weekday": d.strftime("%A"), "rows": rows}


@router.put("/attendance")
def mark_attendance(body: AttIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                    user: User = Depends(perm("hr.manage"))):
    d = _d(body.day)
    if d > L.today():
        raise HTTPException(422, "Attendance cannot be marked for a future day")
    n = 0
    for en in body.entries:
        if en.status not in ATT_STATUSES:
            raise HTTPException(422, f"status must be one of {', '.join(ATT_STATUSES)}")
        for t in (en.clock_in, en.clock_out):
            if t and not (len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit()):
                raise HTTPException(422, "Times are HH:MM")
        _emp(db, tenant_id, en.employee_id)
        a = db.query(HrAttendance).filter(HrAttendance.employee_id == en.employee_id, HrAttendance.day == d).first()
        if a is None:
            a = HrAttendance(tenant_id=tenant_id, employee_id=en.employee_id, day=d)
            db.add(a)
        a.status, a.clock_in, a.clock_out, a.note, a.marked_by_user_id = en.status, en.clock_in, en.clock_out, en.note, user.id
        n += 1
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.attendance_marked", entity_type="hr",
                details={"day": d.isoformat(), "entries": n}, request=request)
    db.commit()
    return {"saved": n}


@router.get("/attendance/summary")
def attendance_summary(month: str | None = None, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                       _: User = Depends(perm("hr.view"))):
    first = _month(month) if month else L.today().replace(day=1)
    first, last = P.month_bounds(first)
    counts = {}
    for a in db.query(HrAttendance).filter(HrAttendance.tenant_id == tenant_id, HrAttendance.day >= first,
                                           HrAttendance.day <= last):
        c = counts.setdefault(a.employee_id, {s: 0 for s in ATT_STATUSES})
        c[a.status] += 1
    names = _managers(db, tenant_id)
    st = P.statutory(P.settings_for(db, tenant_id))
    return {"month": first.isoformat()[:7], "working_days": float(P.working_days(st, first, last)),
            "rows": [{"employee_id": i, "name": names.get(i), **c} for i, c in sorted(counts.items(), key=lambda x: names.get(x[0], ""))]}


# =========================================================================== documents
DOC_TYPES = ("contract", "offer_letter", "id", "kra_pin", "nssf", "shif", "certificate", "cv", "warning", "exit", "other")


def _doc_dict(x: HrDocument):
    return {"id": x.id, "doc_type": x.doc_type, "filename": x.filename,
            "expires_on": x.expires_on.isoformat() if x.expires_on else None,
            "created_at": x.created_at.isoformat() if x.created_at else None}


@router.post("/employees/{emp_id}/documents")
async def upload_document(emp_id: int, request: Request, file: UploadFile = File(...), doc_type: str = Form("other"),
                          expires_on: str | None = Form(None), tenant_id: int = Depends(MODULE),
                          db: Session = Depends(get_db), user: User = Depends(perm("hr.manage"))):
    e = _emp(db, tenant_id, emp_id)
    if doc_type not in DOC_TYPES:
        raise HTTPException(422, f"doc_type must be one of {', '.join(DOC_TYPES)}")
    data = await file.read()
    if not data:
        raise HTTPException(422, "The file is empty")
    if len(data) > storage.MAX_BYTES:
        raise HTTPException(413, "The file is too large")
    name = storage.sanitise(file.filename or "document")
    if not name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp", ".docx")):
        raise HTTPException(422, "Upload a PDF, a photo (JPG/PNG/WebP) or a Word file")
    _, path = storage.save_bytes(tenant_id, "hr", name, data)
    x = HrDocument(tenant_id=tenant_id, employee_id=e.id, doc_type=doc_type, filename=name, storage_path=path,
                   expires_on=_d(expires_on, "expires_on") if expires_on else None, uploaded_by_user_id=user.id)
    db.add(x)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.document_added", entity_type="employee", entity_id=e.id,
                details={"doc_type": doc_type, "filename": name}, request=request)
    db.commit()
    return {"id": x.id}


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                 user: User = Depends(perm("hr.self"))):
    x = db.get(HrDocument, doc_id)
    if x is None or x.tenant_id != tenant_id:
        raise HTTPException(404, "Document not found")
    me = H.employee_for_user(db, tenant_id, user)
    if not (_can(db, user, "hr.view") or (me and me.id == x.employee_id)):
        raise HTTPException(404, "Document not found")
    try:
        data = storage.read_bytes(x.storage_path)
    except OSError:
        raise HTTPException(404, "The file is no longer stored")
    kind = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp"}.get(x.filename.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    return Response(data, media_type=kind, headers={"Content-Disposition": f'inline; filename="{x.filename}"',
                                                    "X-Content-Type-Options": "nosniff"})


# =========================================================================== appraisals
class AppraisalIn(BaseModel):
    employee_id: int
    period: str = Field(min_length=2, max_length=30)
    goals: list[dict] = []
    strengths: str | None = None
    improvements: str | None = None


def _appraisal_dict(a: HrAppraisal, names=None):
    return {"id": a.id, "employee_id": a.employee_id, "employee": (names or {}).get(a.employee_id), "period": a.period,
            "goals": a.goals or [], "score": float(a.score) if a.score is not None else None, "strengths": a.strengths,
            "improvements": a.improvements, "status": a.status, "employee_comment": a.employee_comment,
            "reviewer_user_id": a.reviewer_user_id,
            "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None}


def _score(goals: list[dict]):
    clean, total_w, acc = [], 0.0, 0.0
    for g in goals:
        name = str(g.get("goal") or "").strip()
        if not name:
            continue
        w = float(g.get("weight") or 0)
        s = g.get("score")
        if s not in (None, ""):
            s = float(s)
            if not 1 <= s <= 5:
                raise HTTPException(422, "Scores are from 1 (poor) to 5 (excellent)")
        if w < 0:
            raise HTTPException(422, "Weights cannot be negative")
        clean.append({"goal": name[:300], "weight": w, "score": s if s not in ("",) else None,
                      "comment": str(g.get("comment") or "")[:1000]})
        if s not in (None, "") and w > 0:
            total_w += w
            acc += w * s
    return clean, (round(acc / total_w, 2) if total_w else None)


@router.get("/appraisals")
def appraisals(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
               user: User = Depends(perm("hr.view", "appraisals.manage", mode="any"))):
    names = _managers(db, tenant_id)
    return {"appraisals": [_appraisal_dict(a, names) for a in db.query(HrAppraisal)
                           .filter(HrAppraisal.tenant_id == tenant_id).order_by(HrAppraisal.id.desc()).limit(500)]}


@router.post("/appraisals")
def create_appraisal(body: AppraisalIn, request: Request, tenant_id: int = Depends(MODULE),
                     db: Session = Depends(get_db), user: User = Depends(perm("appraisals.manage"))):
    e = _emp(db, tenant_id, body.employee_id)
    me = H.employee_for_user(db, tenant_id, user)
    if me and me.id == e.id:
        raise HTTPException(403, "You cannot appraise yourself")
    goals, score = _score(body.goals)
    a = HrAppraisal(tenant_id=tenant_id, employee_id=e.id, period=body.period, reviewer_user_id=user.id, goals=goals,
                    score=score, strengths=body.strengths, improvements=body.improvements, status="draft")
    db.add(a)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.appraisal_created", entity_type="employee",
                entity_id=e.id, details={"period": body.period}, request=request)
    db.commit()
    return {"id": a.id, "score": score}


@router.put("/appraisals/{aid}")
def edit_appraisal(aid: int, body: AppraisalIn, request: Request, tenant_id: int = Depends(MODULE),
                   db: Session = Depends(get_db), user: User = Depends(perm("appraisals.manage"))):
    a = db.get(HrAppraisal, aid)
    if a is None or a.tenant_id != tenant_id:
        raise HTTPException(404, "Appraisal not found")
    if a.status != "draft":
        raise HTTPException(409, "A shared appraisal cannot be changed")
    a.goals, a.score = _score(body.goals)
    a.period, a.strengths, a.improvements = body.period, body.strengths, body.improvements
    db.commit()
    return {"score": float(a.score) if a.score is not None else None}


@router.post("/appraisals/{aid}/share")
def share_appraisal(aid: int, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                    user: User = Depends(perm("appraisals.manage"))):
    a = db.get(HrAppraisal, aid)
    if a is None or a.tenant_id != tenant_id:
        raise HTTPException(404, "Appraisal not found")
    if a.status != "draft":
        raise HTTPException(409, "Already shared")
    if a.score is None:
        raise HTTPException(422, "Score at least one goal before sharing")
    a.status = "shared"
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.appraisal_shared", entity_type="employee",
                entity_id=a.employee_id, details={"period": a.period, "score": float(a.score)}, request=request)
    db.commit()
    return {"status": "shared"}


class AckIn(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/appraisals/{aid}/acknowledge")
def acknowledge(aid: int, body: AckIn, request: Request, tenant_id: int = Depends(MODULE),
                db: Session = Depends(get_db), user: User = Depends(perm("hr.self"))):
    a = db.get(HrAppraisal, aid)
    me = H.employee_for_user(db, tenant_id, user)
    if a is None or a.tenant_id != tenant_id or not me or a.employee_id != me.id:
        raise HTTPException(404, "Appraisal not found")
    if a.status != "shared":
        raise HTTPException(409, "There is nothing to acknowledge")
    a.status, a.employee_comment, a.acknowledged_at = "acknowledged", body.comment, datetime.utcnow()
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.appraisal_acknowledged", entity_type="employee",
                entity_id=me.id, details={"appraisal": a.id}, request=request)
    db.commit()
    return {"status": "acknowledged"}


# =========================================================================== payroll
def _run_dict(r: HrPayrollRun, names=None):
    return {"id": r.id, "period": r.period.isoformat(), "label": r.period.strftime("%B %Y"), "status": r.status,
            "employees": r.employees, "totals": r.totals or {}, "created_by_user_id": r.created_by_user_id,
            "created_by": (names or {}).get(r.created_by_user_id), "approved_by": (names or {}).get(r.approved_by_user_id),
            "journal_id": r.journal_id, "payment_journal_id": r.payment_journal_id,
            "paid_on": r.paid_on.isoformat() if r.paid_on else None, "note": r.note}


def _slip_dict(p: HrPayslip, e: HrEmployee | None = None):
    d = {k: _n(getattr(p, k)) for k in ("basic", "gross", "taxable_pay", "nssf_employee", "nssf_employer", "shif",
                                         "housing_employee", "housing_employer", "pension", "income_tax",
                                         "personal_relief", "insurance_relief", "paye", "other_deductions", "nita", "net")}
    d.update({"id": p.id, "run_id": p.run_id, "employee_id": p.employee_id, "items": p.items or [],
              "days_paid": float(p.days_paid), "days_in_month": p.days_in_month, "unpaid_days": float(p.unpaid_days)})
    if e is not None:
        d.update({"employee": H.full_name(e), "employee_no": e.employee_no, "job_title": e.job_title,
                  "kra_pin": e.kra_pin, "nssf_no": e.nssf_no, "shif_no": e.shif_no, "pay_method": e.pay_method,
                  "bank_name": e.bank_name, "bank_account_masked": _mask(e.bank_account), "mpesa_phone": e.mpesa_phone})
    return d


def _payslip_list(db, emp_id, only_final=False):
    q = (db.query(HrPayslip, HrPayrollRun).join(HrPayrollRun, HrPayrollRun.id == HrPayslip.run_id)
         .filter(HrPayslip.employee_id == emp_id, HrPayrollRun.status != "cancelled"))
    if only_final:
        q = q.filter(HrPayrollRun.status.in_(("approved", "paid")))
    return [{"id": p.id, "run_id": r.id, "period": r.period.isoformat(), "label": r.period.strftime("%B %Y"),
             "status": r.status, "gross": _n(p.gross), "net": _n(p.net)} for p, r in q.order_by(HrPayrollRun.period.desc())]


def _run_for(db, tenant_id, rid, lock=False) -> HrPayrollRun:
    q = db.query(HrPayrollRun).filter(HrPayrollRun.id == rid, HrPayrollRun.tenant_id == tenant_id)
    r = (q.with_for_update() if lock else q).first()
    if r is None:
        raise HTTPException(404, "Payroll not found")
    return r


class RunIn(BaseModel):
    period: str          # YYYY-MM


@router.get("/payroll/runs")
def runs(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(perm("hr.view"))):
    rows = db.query(HrPayrollRun).filter(HrPayrollRun.tenant_id == tenant_id).order_by(HrPayrollRun.period.desc()).all()
    names = {u.id: u.full_name for u in db.query(User).filter(User.id.in_(
        {r.created_by_user_id for r in rows} | {r.approved_by_user_id for r in rows} or {-1}))}
    return {"runs": [_run_dict(r, names) for r in rows]}


@router.post("/payroll/runs")
def new_run(body: RunIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
            user: User = Depends(perm("payroll.run"))):
    r = P.create_run(db, tenant_id, _month(body.period), user)
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.payroll_prepared", entity_type="payroll",
                entity_id=r.id, details={"period": r.period.isoformat(), "net": r.totals.get("net")}, request=request)
    db.commit()
    return {"id": r.id}


@router.get("/payroll/runs/{rid}")
def run(rid: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(perm("hr.view"))):
    r = _run_for(db, tenant_id, rid)
    emps = {e.id: e for e in db.query(HrEmployee).filter(HrEmployee.tenant_id == tenant_id)}
    names = {u.id: u.full_name for u in db.query(User).filter(User.id.in_({r.created_by_user_id, r.approved_by_user_id} - {None} or {-1}))}
    d = _run_dict(r, names)
    d["payslips"] = [_slip_dict(p, emps.get(p.employee_id)) for p in db.query(HrPayslip).filter(HrPayslip.run_id == r.id)
                     .order_by(HrPayslip.employee_id)]
    d["requires_second_person"] = P.settings_for(db, tenant_id).payroll_approval
    return d


@router.post("/payroll/runs/{rid}/recalculate")
def recalc(rid: int, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
           user: User = Depends(perm("payroll.run"))):
    r = _run_for(db, tenant_id, rid, lock=True)
    P.build_run(db, r)
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.payroll_recalculated", entity_type="payroll",
                entity_id=r.id, details={"net": r.totals.get("net")}, request=request)
    db.commit()
    return {"status": r.status, "totals": r.totals}


@router.post("/payroll/runs/{rid}/approve")
def approve(rid: int, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
            user: User = Depends(perm("payroll.approve"))):
    r = _run_for(db, tenant_id, rid, lock=True)
    P.approve_run(db, r, user)
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.payroll_approved", entity_type="payroll",
                entity_id=r.id, details={"period": r.period.isoformat(), "net": r.totals.get("net")}, request=request)
    db.commit()
    return {"status": r.status, "journal_id": r.journal_id}


class PayIn(BaseModel):
    paid_on: str
    account_id: int


@router.post("/payroll/runs/{rid}/pay")
def pay(rid: int, body: PayIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
        user: User = Depends(perm("payroll.approve"))):
    r = _run_for(db, tenant_id, rid, lock=True)
    P.pay_run(db, r, paid_on=_d(body.paid_on, "paid_on"), account_id=body.account_id, user=user)
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.payroll_paid", entity_type="payroll", entity_id=r.id,
                details={"paid_on": body.paid_on}, request=request)
    db.commit()
    return {"status": r.status}


class CancelIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.post("/payroll/runs/{rid}/cancel")
def cancel(rid: int, body: CancelIn, request: Request, tenant_id: int = Depends(MODULE),
           db: Session = Depends(get_db), user: User = Depends(perm("payroll.run"))):
    r = _run_for(db, tenant_id, rid, lock=True)
    if r.status != "draft":
        raise HTTPException(409, "An approved payroll is in the books: it cannot be cancelled. Correct it in next month's payroll")
    r.status, r.note = "cancelled", body.reason
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.payroll_cancelled", entity_type="payroll",
                entity_id=r.id, details={"reason": body.reason}, request=request)
    db.commit()
    return {"status": "cancelled"}


@router.get("/payroll/payslips/{pid}")
def payslip(pid: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
            user: User = Depends(perm("hr.self"))):
    p = db.get(HrPayslip, pid)
    if p is None or p.tenant_id != tenant_id:
        raise HTTPException(404, "Payslip not found")
    r = db.get(HrPayrollRun, p.run_id)
    me = H.employee_for_user(db, tenant_id, user)
    own = me and me.id == p.employee_id and r.status in ("approved", "paid")
    if not (_can(db, user, "hr.view") or own):
        raise HTTPException(404, "Payslip not found")
    e = db.get(HrEmployee, p.employee_id)
    d = _slip_dict(p, e)
    t = db.get(Tenant, tenant_id)
    d.update({"period": r.period.isoformat(), "label": r.period.strftime("%B %Y"), "run_status": r.status,
              "company": t.name if t else "", "branch": _branches(db, tenant_id).get(e.branch_id)})
    return d


def _csv(rows, header, filename):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/payroll/runs/{rid}/salary-file.csv")
def salary_file(rid: int, method: str = "bank", tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                _: User = Depends(perm("payroll.run"))):
    r = _run_for(db, tenant_id, rid)
    if r.status not in ("approved", "paid"):
        raise HTTPException(409, "Approve the payroll first")
    rows = []
    for p, e in (db.query(HrPayslip, HrEmployee).join(HrEmployee, HrEmployee.id == HrPayslip.employee_id)
                 .filter(HrPayslip.run_id == r.id, HrEmployee.pay_method == method).order_by(HrEmployee.employee_no)):
        if method == "mpesa":
            rows.append([e.mpesa_phone or e.phone or "", f"{D(p.net):.2f}", H.full_name(e), e.employee_no])
        else:
            rows.append([H.full_name(e), e.bank_name or "", e.bank_branch or "", e.bank_account or "",
                         f"{D(p.net):.2f}", f"Salary {r.period:%b %Y}", e.employee_no])
    header = (["Phone", "Amount", "Name", "Reference"] if method == "mpesa" else
              ["Name", "Bank", "Branch", "Account", "Amount", "Narration", "Reference"])
    return _csv(rows, header, f"salaries_{method}_{r.period:%Y-%m}.csv")


@router.get("/payroll/runs/{rid}/returns/{kind}.csv")
def returns(rid: int, kind: str, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
            _: User = Depends(perm("payroll.run"))):
    r = _run_for(db, tenant_id, rid)
    q = (db.query(HrPayslip, HrEmployee).join(HrEmployee, HrEmployee.id == HrPayslip.employee_id)
         .filter(HrPayslip.run_id == r.id).order_by(HrEmployee.employee_no))
    rows = []
    if kind == "paye":
        header = ["KRA PIN", "Name", "Residential status", "Type of employee", "Basic salary", "Gross pay",
                  "NSSF + pension", "SHIF", "Housing Levy", "Taxable pay", "Tax charged", "Personal relief",
                  "Insurance relief", "PAYE"]
        for p, e in q:
            rows.append([e.kra_pin or "", H.full_name(e), "Resident", "Primary Employee", f"{D(p.basic):.2f}",
                         f"{D(p.gross):.2f}", f"{D(p.nssf_employee) + D(p.pension):.2f}", f"{D(p.shif):.2f}",
                         f"{D(p.housing_employee):.2f}", f"{D(p.taxable_pay):.2f}", f"{D(p.income_tax):.2f}",
                         f"{D(p.personal_relief):.2f}", f"{D(p.insurance_relief):.2f}", f"{D(p.paye):.2f}"])
    elif kind == "nssf":
        header = ["Payroll number", "Surname", "Other names", "ID number", "KRA PIN", "NSSF number", "Gross pay",
                  "Employee contribution", "Employer contribution", "Total"]
        for p, e in q:
            rows.append([e.employee_no, e.last_name, " ".join(x for x in (e.first_name, e.other_names) if x),
                         e.national_id or "", e.kra_pin or "", e.nssf_no or "", f"{D(p.gross):.2f}",
                         f"{D(p.nssf_employee):.2f}", f"{D(p.nssf_employer):.2f}",
                         f"{D(p.nssf_employee) + D(p.nssf_employer):.2f}"])
    elif kind == "shif":
        header = ["Payroll number", "First name", "Last name", "ID number", "KRA PIN", "SHIF number", "Phone",
                  "Gross pay", "Amount"]
        for p, e in q:
            rows.append([e.employee_no, e.first_name, e.last_name, e.national_id or "", e.kra_pin or "",
                         e.shif_no or "", e.phone or "", f"{D(p.gross):.2f}", f"{D(p.shif):.2f}"])
    elif kind == "housing":
        header = ["ID number", "Name", "KRA PIN", "Gross pay", "Employee levy", "Employer levy", "Total"]
        for p, e in q:
            rows.append([e.national_id or "", H.full_name(e), e.kra_pin or "", f"{D(p.gross):.2f}",
                         f"{D(p.housing_employee):.2f}", f"{D(p.housing_employer):.2f}",
                         f"{D(p.housing_employee) + D(p.housing_employer):.2f}"])
    else:
        raise HTTPException(404, "Returns are paye, nssf, shif and housing")
    return _csv(rows, header, f"{kind}_{r.period:%Y-%m}.csv")


@router.get("/payroll/p9/{emp_id}")
def p9(emp_id: int, year: int | None = None, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
       user: User = Depends(perm("hr.self"))):
    e = _emp(db, tenant_id, emp_id)
    me = H.employee_for_user(db, tenant_id, user)
    if not (_can(db, user, "hr.view") or (me and me.id == e.id)):
        raise HTTPException(404, "Employee not found")
    t = db.get(Tenant, tenant_id)
    out = P.p9(db, e, year or L.today().year)
    out.update({"employee": H.full_name(e), "kra_pin": e.kra_pin, "employee_no": e.employee_no,
                "employer": t.name if t else ""})
    return out


@router.get("/payroll/statutory")
def statutory_owed(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(perm("hr.view"))):
    return {"balances": P.statutory_balances(db, tenant_id)}


class RemitIn(BaseModel):
    body: str
    amount: float = Field(gt=0)
    paid_on: str
    account_id: int
    reference: str | None = Field(default=None, max_length=60)


@router.post("/payroll/remit")
def remit(body: RemitIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
          user: User = Depends(perm("payroll.approve"))):
    j = P.remit(db, tenant_id, body=body.body, amount=body.amount, paid_on=_d(body.paid_on, "paid_on"),
                account_id=body.account_id, reference=body.reference, user=user)
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.statutory_paid", entity_type="payroll",
                details={**body.model_dump(), "journal": j.number}, request=request)
    db.commit()
    return {"journal_id": j.id}


# =========================================================================== settings
class HrSettingsIn(BaseModel):
    statutory: dict | None = None
    reset_statutory: bool = False
    deduct_absence: bool | None = None
    payroll_approval: bool | None = None
    employee_prefix: str | None = Field(default=None, min_length=1, max_length=10)


def _settings_dict(s):
    return {"statutory": P.statutory(s), "defaults": P.DEFAULT_STATUTORY, "labels": P.LABELS,
            "overridden": sorted((s.statutory or {}).keys()), "deduct_absence": s.deduct_absence,
            "payroll_approval": s.payroll_approval, "employee_prefix": s.employee_prefix}


@router.get("/settings")
def get_settings(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(perm("hr.view"))):
    s = P.settings_for(db, tenant_id)
    db.commit()
    return _settings_dict(s)


@router.put("/settings")
def put_settings(body: HrSettingsIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                 user: User = Depends(perm("hr.manage"))):
    s = P.settings_for(db, tenant_id, lock=True)
    if body.reset_statutory:
        s.statutory = {}
    elif body.statutory:
        merged = {**(s.statutory or {}), **P.validate_statutory(body.statutory)}
        s.statutory = {k: v for k, v in merged.items() if v != P.DEFAULT_STATUTORY.get(k)}
    for k in ("deduct_absence", "payroll_approval", "employee_prefix"):
        v = getattr(body, k)
        if v is not None:
            setattr(s, k, v.strip().upper() if k == "employee_prefix" else v)
    s.updated_by_user_id, s.updated_at = user.id, datetime.utcnow()
    write_audit(db, tenant_id=tenant_id, user=user, action="hr.settings_updated", entity_type="hr",
                details=body.model_dump(exclude_none=True), request=request)
    db.commit()
    return _settings_dict(s)


@router.post("/payroll/preview")
def preview(body: dict, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
            _: User = Depends(perm("hr.view"))):
    """What a salary comes to under this DCP's rates (for the settings screen)."""
    st = P.statutory(P.settings_for(db, tenant_id))
    c = P.calculate(st, basic=D(str(body.get("basic") or 0)), allowances=[], deductions=[], factor=D(1))
    return {k: (float(v) if not isinstance(v, list) else v) for k, v in c.items()}


# =========================================================================== self-service
@router.get("/me")
def me(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), user: User = Depends(perm("hr.self"))):
    e = H.employee_for_user(db, tenant_id, user)
    if e is None:
        return {"linked": False}
    d = _emp_dict(e, _branches(db, tenant_id), _managers(db, tenant_id))
    d.pop("basic_salary", None)
    types = {t.id: t.name for t in db.query(HrLeaveType).filter(HrLeaveType.tenant_id == tenant_id)}
    team = H.team_ids(db, e)
    names = _managers(db, tenant_id)
    team_pending = (db.query(HrLeaveRequest).filter(HrLeaveRequest.employee_id.in_(team or {-1}),
                                                    HrLeaveRequest.status == "pending").all())
    return {"linked": True, "employee": d, "leave": H.balances(db, e),
            "leave_requests": [_leave_dict(r, None, types) for r in db.query(HrLeaveRequest)
                               .filter(HrLeaveRequest.employee_id == e.id).order_by(HrLeaveRequest.start_date.desc()).limit(50)],
            "payslips": _payslip_list(db, e.id, only_final=True),
            "appraisals": [_appraisal_dict(a) for a in db.query(HrAppraisal).filter(
                HrAppraisal.employee_id == e.id, HrAppraisal.status.in_(("shared", "acknowledged")))
                .order_by(HrAppraisal.id.desc())],
            "documents": [_doc_dict(x) for x in db.query(HrDocument).filter(HrDocument.employee_id == e.id)],
            "team": [{"id": i, "name": names.get(i)} for i in sorted(team)],
            "team_pending": [_leave_dict(r, names, types) for r in team_pending]}
