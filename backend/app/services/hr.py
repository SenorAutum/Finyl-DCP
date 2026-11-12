"""WP15 — HR helpers: employee numbers, leave entitlement and balances, team
relationships, and the default leave types (Employment Act 2007)."""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.money import D, money
from app.models import HrEmployee, HrLeaveBalance, HrLeaveRequest, HrLeaveType
from app.services import ledger as L
from app.services import payroll as P

# name, days a year, paid, accrues monthly, carry forward max, gender, needs document, calendar days
DEFAULT_LEAVE_TYPES = [
    ("Annual leave", 21, True, True, 10, None, False, False),
    ("Sick leave", 14, True, False, 0, None, True, False),
    ("Maternity leave", 90, True, False, 0, "female", True, True),
    ("Paternity leave", 14, True, False, 0, "male", False, False),
    ("Compassionate leave", 5, True, False, 0, None, False, False),
    ("Study leave", 10, True, False, 0, None, True, False),
    ("Unpaid leave", 0, False, False, 0, None, False, False),
]


def ensure_leave_types(db: Session, tenant_id: int):
    if db.query(HrLeaveType.id).filter(HrLeaveType.tenant_id == tenant_id).first():
        return
    for name, days, paid, accrues, carry, gender, doc, cal in DEFAULT_LEAVE_TYPES:
        db.add(HrLeaveType(tenant_id=tenant_id, name=name, days_per_year=days, paid=paid, accrues=accrues,
                           carry_forward_max=carry, gender=gender, needs_document=doc, calendar_days=cal,
                           active=True))
    db.flush()


def next_employee_no(db, tenant_id) -> str:
    s = P.settings_for(db, tenant_id, lock=True)
    while True:
        no = f"{s.employee_prefix or 'EMP'}-{s.next_employee_no:04d}"
        s.next_employee_no += 1
        if not db.query(HrEmployee.id).filter(HrEmployee.tenant_id == tenant_id, HrEmployee.employee_no == no).first():
            return no


def full_name(e: HrEmployee) -> str:
    return " ".join(x for x in (e.first_name, e.other_names, e.last_name) if x)


def employee_for_user(db, tenant_id, user) -> HrEmployee | None:
    return (db.query(HrEmployee).filter(HrEmployee.tenant_id == tenant_id, HrEmployee.user_id == user.id)
            .first()) if user else None


def team_ids(db, manager: HrEmployee | None) -> set[int]:
    """Everyone below this person (direct reports and their reports)."""
    if manager is None:
        return set()
    out, frontier = set(), {manager.id}
    while frontier:
        rows = {i for (i,) in db.query(HrEmployee.id).filter(HrEmployee.manager_employee_id.in_(frontier))}
        rows -= out | {manager.id}
        out |= rows
        frontier = rows
    return out


def leave_days(lt: HrLeaveType, start: date, end: date, st: dict):
    if end < start:
        raise HTTPException(422, "The last day cannot be before the first day")
    if lt.calendar_days:
        return D((end - start).days + 1)
    n = P.working_days(st, start, end)
    if n <= 0:
        raise HTTPException(422, "Those dates are weekends or public holidays only")
    return n


def _months_served(e: HrEmployee, year: int, upto: date) -> int:
    start = max(e.hire_date, date(year, 1, 1))
    end = min(upto, date(year, 12, 31))
    if e.termination_date:
        end = min(end, e.termination_date)
    if end < start:
        return 0
    m = (end.year - start.year) * 12 + end.month - start.month + (1 if end.day >= start.day else 0)
    return max(0, min(12, m))


def balances(db, e: HrEmployee, year: int | None = None) -> list[dict]:
    year = year or L.today().year
    ensure_leave_types(db, e.tenant_id)
    today = L.today()
    upto = today if year == today.year else date(year, 12, 31)
    out = []
    for lt in db.query(HrLeaveType).filter(HrLeaveType.tenant_id == e.tenant_id, HrLeaveType.active.is_(True)) \
            .order_by(HrLeaveType.id):
        if lt.gender and e.gender and lt.gender != e.gender:
            continue
        bal = db.query(HrLeaveBalance).filter(HrLeaveBalance.employee_id == e.id, HrLeaveBalance.leave_type_id == lt.id,
                                              HrLeaveBalance.year == year).first()
        carried = D(bal.carried) if bal else D(0)
        adj = D(bal.adjustment) if bal else D(0)
        per_year = D(lt.days_per_year or 0)
        unlimited = per_year == 0 and not lt.paid
        earned = (money(per_year * _months_served(e, year, upto) / D(12)) if lt.accrues else per_year)
        used = D(db.query(func.coalesce(func.sum(HrLeaveRequest.days), 0)).filter(
            HrLeaveRequest.employee_id == e.id, HrLeaveRequest.leave_type_id == lt.id,
            HrLeaveRequest.status == "approved", func.extract("year", HrLeaveRequest.start_date) == year).scalar() or 0)
        pending = D(db.query(func.coalesce(func.sum(HrLeaveRequest.days), 0)).filter(
            HrLeaveRequest.employee_id == e.id, HrLeaveRequest.leave_type_id == lt.id,
            HrLeaveRequest.status == "pending", func.extract("year", HrLeaveRequest.start_date) == year).scalar() or 0)
        out.append({"leave_type_id": lt.id, "name": lt.name, "paid": lt.paid, "unlimited": unlimited,
                    "entitlement": float(per_year), "earned": float(earned), "carried": float(carried),
                    "adjustment": float(adj), "used": float(used), "pending": float(pending),
                    "available": None if unlimited else float(money(earned + carried + adj - used - pending)),
                    "needs_document": lt.needs_document, "calendar_days": lt.calendar_days})
    return out


def check_request(db, e: HrEmployee, lt: HrLeaveType, start: date, end: date, days, exclude_id=None):
    if lt.tenant_id != e.tenant_id or not lt.active:
        raise HTTPException(404, "Leave type not found")
    if lt.gender and e.gender and lt.gender != e.gender:
        raise HTTPException(422, f"{lt.name} is for {lt.gender} employees")
    if e.status == "terminated":
        raise HTTPException(409, "This person has left")
    if start < e.hire_date:
        raise HTTPException(422, "Leave cannot start before the hire date")
    q = db.query(HrLeaveRequest.id).filter(HrLeaveRequest.employee_id == e.id,
                                           HrLeaveRequest.status.in_(("pending", "approved")),
                                           HrLeaveRequest.start_date <= end, HrLeaveRequest.end_date >= start)
    if exclude_id:
        q = q.filter(HrLeaveRequest.id != exclude_id)
    if q.first():
        raise HTTPException(409, "These dates overlap leave already requested or approved")
    bal = next((b for b in balances(db, e, start.year) if b["leave_type_id"] == lt.id), None)
    if bal and not bal["unlimited"] and D(days) > D(bal["available"] or 0):
        raise HTTPException(422, f"Only {bal['available']:g} days of {lt.name.lower()} are available")
