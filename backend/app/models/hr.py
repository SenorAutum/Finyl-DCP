"""WP15 — ERP human resources and Kenyan payroll (migration 044).

Employees (ID number, KRA PIN and bank account encrypted at rest), recurring pay
items, leave, attendance, documents, appraisals, and monthly payroll runs whose
payslips post to the books (services/payroll.py).
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.crypto import EncryptedText
from app.core.database import Base
from app.core.timeutil import UTCDateTime


class HrSettings(Base):
    __tablename__ = "hr_settings"

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    statutory = Column(JSONB, nullable=False, default=dict)   # overrides of payroll.DEFAULT_STATUTORY
    deduct_absence = Column(Boolean, nullable=False, default=False)  # absent days in attendance are unpaid
    payroll_approval = Column(Boolean, nullable=False, default=True)  # a second person approves payroll
    next_employee_no = Column(Integer, nullable=False, default=1)
    employee_prefix = Column(String(10), nullable=False, default="EMP")
    updated_by_user_id = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(UTCDateTime, default=datetime.utcnow)


class HrEmployee(Base):
    __tablename__ = "hr_employees"
    __table_args__ = (
        Index("uq_hr_employee_no", "tenant_id", "employee_no", unique=True),
        Index("uq_hr_employee_user", "tenant_id", "user_id", unique=True,
              postgresql_where=text("user_id IS NOT NULL")),
        Index("ix_hr_employees_status", "tenant_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    employee_no = Column(String(20), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))            # their login, for self-service
    staff_id = Column(Integer, ForeignKey("staff.id"))           # their field/office staff record
    first_name = Column(String(80), nullable=False)
    last_name = Column(String(80), nullable=False)
    other_names = Column(String(80))
    national_id = Column(EncryptedText)
    kra_pin = Column(EncryptedText)
    nssf_no = Column(String(30))
    shif_no = Column(String(30))
    phone = Column(String(20))
    email = Column(String(160))
    date_of_birth = Column(Date)
    gender = Column(String(10))                                 # female | male | other
    branch_id = Column(Integer, ForeignKey("branches.id"))
    department = Column(String(80))
    job_title = Column(String(120))
    employment_type = Column(String(15), nullable=False, default="permanent")  # permanent | contract | casual | intern
    hire_date = Column(Date, nullable=False)
    probation_end = Column(Date)
    contract_end = Column(Date)
    status = Column(String(15), nullable=False, default="active")  # active | suspended | terminated
    termination_date = Column(Date)
    termination_reason = Column(Text)
    basic_salary = Column(Numeric(14, 2), nullable=False, default=0)   # monthly KES
    pay_method = Column(String(10), nullable=False, default="bank")    # bank | mpesa | cash
    bank_name = Column(String(80))
    bank_branch = Column(String(80))
    bank_account = Column(EncryptedText)
    mpesa_phone = Column(String(20))
    manager_employee_id = Column(Integer, ForeignKey("hr_employees.id"))
    next_of_kin_name = Column(String(120))
    next_of_kin_phone = Column(String(20))
    disability_exempt = Column(Boolean, nullable=False, default=False)  # KRA disability exemption certificate
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class HrPayItem(Base):
    __tablename__ = "hr_pay_items"
    __table_args__ = (Index("ix_hr_pay_items_employee", "employee_id"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(12), nullable=False)          # allowance | deduction
    name = Column(String(80), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    taxable = Column(Boolean, nullable=False, default=True)      # allowances
    pre_tax = Column(Boolean, nullable=False, default=False)     # deductions: registered pension (before PAYE)
    insurance = Column(Boolean, nullable=False, default=False)   # deductions: life/health premium (insurance relief)
    account_id = Column(Integer, ForeignKey("chart_of_accounts.id"))  # deductions: where it goes (e.g. Staff advances)
    start_month = Column(Date)
    end_month = Column(Date)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class HrLeaveType(Base):
    __tablename__ = "hr_leave_types"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_hr_leave_type"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(60), nullable=False)
    days_per_year = Column(Numeric(6, 2), nullable=False, default=0)
    paid = Column(Boolean, nullable=False, default=True)
    accrues = Column(Boolean, nullable=False, default=True)     # earned month by month (annual leave)
    carry_forward_max = Column(Numeric(6, 2), nullable=False, default=0)
    gender = Column(String(10))                                 # only for this gender (maternity, paternity)
    needs_document = Column(Boolean, nullable=False, default=False)
    calendar_days = Column(Boolean, nullable=False, default=False)  # count every day (maternity), not working days
    active = Column(Boolean, nullable=False, default=True)


class HrLeaveBalance(Base):
    __tablename__ = "hr_leave_balances"
    __table_args__ = (UniqueConstraint("employee_id", "leave_type_id", "year", name="uq_hr_leave_balance"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False)
    leave_type_id = Column(Integer, ForeignKey("hr_leave_types.id"), nullable=False)
    year = Column(Integer, nullable=False)
    carried = Column(Numeric(6, 2), nullable=False, default=0)
    adjustment = Column(Numeric(6, 2), nullable=False, default=0)
    note = Column(Text)


class HrLeaveRequest(Base):
    __tablename__ = "hr_leave_requests"
    __table_args__ = (Index("ix_hr_leave_requests", "tenant_id", "status"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False)
    leave_type_id = Column(Integer, ForeignKey("hr_leave_types.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    days = Column(Numeric(6, 2), nullable=False)
    reason = Column(Text)
    status = Column(String(12), nullable=False, default="pending")   # pending | approved | rejected | cancelled
    requested_by_user_id = Column(Integer, ForeignKey("users.id"))
    decided_by_user_id = Column(Integer, ForeignKey("users.id"))
    decided_at = Column(UTCDateTime)
    decision_note = Column(Text)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class HrAttendance(Base):
    __tablename__ = "hr_attendance"
    __table_args__ = (UniqueConstraint("employee_id", "day", name="uq_hr_attendance"),
                      Index("ix_hr_attendance_day", "tenant_id", "day"))

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False)
    day = Column(Date, nullable=False)
    status = Column(String(10), nullable=False)        # present | absent | half | leave | off | holiday
    clock_in = Column(String(5))
    clock_out = Column(String(5))
    note = Column(String(200))
    marked_by_user_id = Column(Integer, ForeignKey("users.id"))


class HrDocument(Base):
    __tablename__ = "hr_documents"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False)
    doc_type = Column(String(30), nullable=False)      # contract | id | kra_pin | certificate | offer_letter | warning | other
    filename = Column(String(200), nullable=False)
    storage_path = Column(String(400), nullable=False)
    expires_on = Column(Date)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class HrAppraisal(Base):
    __tablename__ = "hr_appraisals"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False)
    period = Column(String(30), nullable=False)        # e.g. "2026 H2" or "Q4 2026"
    reviewer_user_id = Column(Integer, ForeignKey("users.id"))
    goals = Column(JSONB, nullable=False, default=list)   # [{goal, weight, score (1-5), comment}]
    score = Column(Numeric(4, 2))
    strengths = Column(Text)
    improvements = Column(Text)
    status = Column(String(15), nullable=False, default="draft")   # draft | shared | acknowledged
    employee_comment = Column(Text)
    acknowledged_at = Column(UTCDateTime)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class HrPayrollRun(Base):
    __tablename__ = "hr_payroll_runs"
    __table_args__ = (Index("uq_hr_payroll_period", "tenant_id", "period", unique=True,
                            postgresql_where=text("status <> 'cancelled'")),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    period = Column(Date, nullable=False)                   # first day of the month paid
    # draft (prepared, can be recalculated) | approved (posted to the books) | paid | cancelled
    status = Column(String(12), nullable=False, default="draft")
    employees = Column(Integer, nullable=False, default=0)
    totals = Column(JSONB, nullable=False, default=dict)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    approved_by_user_id = Column(Integer, ForeignKey("users.id"))
    approved_at = Column(UTCDateTime)
    journal_id = Column(Integer, ForeignKey("acc_journals.id"))
    paid_on = Column(Date)
    paid_from_account_id = Column(Integer, ForeignKey("chart_of_accounts.id"))
    payment_journal_id = Column(Integer, ForeignKey("acc_journals.id"))
    note = Column(Text)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class HrPayslip(Base):
    __tablename__ = "hr_payslips"
    __table_args__ = (UniqueConstraint("run_id", "employee_id", name="uq_hr_payslip"),)

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("hr_payroll_runs.id", ondelete="CASCADE"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("hr_employees.id"), nullable=False)
    days_paid = Column(Numeric(5, 2), nullable=False)
    days_in_month = Column(Integer, nullable=False)
    unpaid_days = Column(Numeric(5, 2), nullable=False, default=0)
    basic = Column(Numeric(14, 2), nullable=False, default=0)
    items = Column(JSONB, nullable=False, default=list)     # [{kind, name, amount, taxable, pre_tax, insurance, account_id}]
    gross = Column(Numeric(14, 2), nullable=False, default=0)
    taxable_pay = Column(Numeric(14, 2), nullable=False, default=0)
    nssf_employee = Column(Numeric(14, 2), nullable=False, default=0)
    nssf_employer = Column(Numeric(14, 2), nullable=False, default=0)
    shif = Column(Numeric(14, 2), nullable=False, default=0)
    housing_employee = Column(Numeric(14, 2), nullable=False, default=0)
    housing_employer = Column(Numeric(14, 2), nullable=False, default=0)
    pension = Column(Numeric(14, 2), nullable=False, default=0)
    income_tax = Column(Numeric(14, 2), nullable=False, default=0)
    personal_relief = Column(Numeric(14, 2), nullable=False, default=0)
    insurance_relief = Column(Numeric(14, 2), nullable=False, default=0)
    paye = Column(Numeric(14, 2), nullable=False, default=0)
    other_deductions = Column(Numeric(14, 2), nullable=False, default=0)
    nita = Column(Numeric(14, 2), nullable=False, default=0)
    net = Column(Numeric(14, 2), nullable=False, default=0)
    branch_id = Column(Integer, ForeignKey("branches.id"))
