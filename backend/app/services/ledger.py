"""WP14 — the ledger: chart of accounts, posting, reversal, balances and statements.

Every amount that reaches the books goes through ``post_journal``: the lines must
balance (total debits == total credits), every account must belong to the DCP and
be active, and nothing may be dated on or before the DCP's lock date (the last
closed period). Auto journals (from loans, payments, payroll, bills, assets) carry
the record they came from (``source_type`` + ``source_id``) and a unique index
makes posting the same record twice impossible.

A posted journal is never edited or deleted: a mistake is undone by a reversal
journal (same lines, debits and credits swapped), and both stay in the books.

Accounting basis: interest, fees and penalties are recognised when the loan is
disbursed (fees) or when they are paid (interest, penalties). The month-end close
can add accrued interest (reversed the next day) and the IFRS 9 loan-loss
provision, so the month-end statements show what was earned and what is at risk.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.money import D, money
from app.models import AccJournal, AccJournalLine, AccSettings, ChartOfAccount, Tenant

ZERO = D("0.00")
NAIROBI = timedelta(hours=3)

ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")

# Groups used to lay out the statements. {subtype: (type, label, cash-flow section)}
SUBTYPES = {
    "cash": ("asset", "Cash, bank and M-Pesa", "cash"),
    "loan_book": ("asset", "Loans to customers", "lending"),
    "provision": ("asset", "Loan loss provision", "lending"),
    "receivable": ("asset", "Receivables", "operating"),
    "fixed_asset": ("asset", "Property and equipment", "investing"),
    "accum_dep": ("asset", "Accumulated depreciation", "investing"),
    "other_asset": ("asset", "Other assets", "operating"),
    "payable": ("liability", "Payables", "operating"),
    "tax": ("liability", "Taxes payable", "operating"),
    "borrowing": ("liability", "Borrowings", "financing"),
    "other_liability": ("liability", "Other liabilities", "operating"),
    "equity": ("equity", "Equity", "financing"),
    "interest_income": ("income", "Interest income", "operating"),
    "fee_income": ("income", "Fees and penalties", "operating"),
    "other_income": ("income", "Other income", "operating"),
    "credit_loss": ("expense", "Credit losses", "operating"),
    "staff_cost": ("expense", "Staff costs", "operating"),
    "operating": ("expense", "Operating expenses", "operating"),
    "depreciation": ("expense", "Depreciation", "operating"),
    "finance_cost": ("expense", "Finance costs", "financing"),
}
DEFAULT_SUBTYPE = {"asset": "other_asset", "liability": "other_liability", "equity": "equity",
                   "income": "other_income", "expense": "operating"}

# (code, name, type, subtype, is_cash)
DEFAULT_ACCOUNTS = [
    ("1000", "Loans Receivable", "asset", "loan_book", False),
    ("1005", "Loan Loss Provision", "asset", "provision", False),
    ("1010", "Bank Account", "asset", "cash", True),
    ("1020", "Fees Receivable", "asset", "receivable", False),
    ("1030", "Interest Receivable (accrued)", "asset", "receivable", False),
    ("1100", "M-Pesa Paybill (collections)", "asset", "cash", True),
    ("1110", "M-Pesa B2C (disbursements)", "asset", "cash", True),
    ("1130", "Cash on Hand", "asset", "cash", True),
    ("1200", "Staff Advances and Other Receivables", "asset", "receivable", False),
    ("1300", "Prepayments and Deposits", "asset", "other_asset", False),
    ("1400", "VAT Input (recoverable)", "asset", "other_asset", False),
    ("1500", "Furniture and Fittings", "asset", "fixed_asset", False),
    ("1510", "Computers and Equipment", "asset", "fixed_asset", False),
    ("1520", "Motor Vehicles", "asset", "fixed_asset", False),
    ("1590", "Accumulated Depreciation", "asset", "accum_dep", False),
    ("1900", "Suspense Account", "asset", "other_asset", False),
    ("2000", "Excise Duty Payable", "liability", "tax", False),
    ("2100", "Accounts Payable (suppliers)", "liability", "payable", False),
    ("2110", "Accrued Expenses", "liability", "other_liability", False),
    ("2120", "Staff Claims Payable", "liability", "payable", False),
    ("2200", "PAYE Payable", "liability", "tax", False),
    ("2210", "NSSF Payable", "liability", "other_liability", False),
    ("2220", "SHIF Payable", "liability", "other_liability", False),
    ("2230", "Housing Levy Payable", "liability", "tax", False),
    ("2240", "NITA Payable", "liability", "other_liability", False),
    ("2250", "Net Salaries Payable", "liability", "payable", False),
    ("2260", "Other Payroll Deductions Payable", "liability", "other_liability", False),
    ("2300", "Borrowings", "liability", "borrowing", False),
    ("2310", "Interest Payable", "liability", "other_liability", False),
    ("2400", "Withholding Tax Payable", "liability", "tax", False),
    ("2410", "VAT Payable", "liability", "tax", False),
    ("3000", "Share Capital", "equity", "equity", False),
    ("3100", "Retained Earnings", "equity", "equity", False),
    ("3900", "Opening Balance Equity", "equity", "equity", False),
    ("4000", "Interest Income", "income", "interest_income", False),
    ("4010", "Fee Income", "income", "fee_income", False),
    ("4020", "Penalty Income", "income", "fee_income", False),
    ("4030", "Recoveries on Written-off Loans", "income", "other_income", False),
    ("4090", "Other Income", "income", "other_income", False),
    ("5000", "Loan Write-offs", "expense", "credit_loss", False),
    ("5010", "Impairment (Loan Loss Provision)", "expense", "credit_loss", False),
    ("6000", "Salaries and Wages", "expense", "staff_cost", False),
    ("6010", "Employer Statutory Contributions", "expense", "staff_cost", False),
    ("6020", "Staff Welfare and Training", "expense", "staff_cost", False),
    ("6100", "Rent and Rates", "expense", "operating", False),
    ("6110", "Utilities", "expense", "operating", False),
    ("6120", "SMS and Communication", "expense", "operating", False),
    ("6130", "Travel and Transport", "expense", "operating", False),
    ("6140", "M-Pesa and Bank Charges", "expense", "operating", False),
    ("6150", "Platform and Software Fees", "expense", "operating", False),
    ("6160", "Professional and Legal Fees", "expense", "operating", False),
    ("6170", "Marketing and Advertising", "expense", "operating", False),
    ("6180", "Office Supplies and Printing", "expense", "operating", False),
    ("6185", "Repairs and Maintenance", "expense", "operating", False),
    ("6190", "Depreciation", "expense", "depreciation", False),
    ("6195", "CRB and Verification Costs", "expense", "operating", False),
    ("6200", "Interest Expense", "expense", "finance_cost", False),
    ("6900", "Other Expenses", "expense", "operating", False),
]
# Finyl's own workspace also earns from DCPs and lends to them.
INTERNAL_ACCOUNTS = [
    ("1210", "Receivable from DCPs", "asset", "receivable", False),
    ("1220", "Loans to DCPs", "asset", "receivable", False),
    ("4100", "Platform Subscription Income", "income", "fee_income", False),
    ("4110", "DCP Onboarding Fees", "income", "fee_income", False),
    ("4120", "DCP Branch Fees", "income", "fee_income", False),
    ("4130", "SMS Revenue", "income", "fee_income", False),
    ("4140", "Interest from DCP Facilities", "income", "interest_income", False),
    ("4190", "Other Platform Income", "income", "other_income", False),
]

# What the system posts to, by role. A DCP may point any role at another account
# of the same type (Accounting -> Settings -> Posting accounts).
POSTING_ROLES = {
    "loan_book": ("1000", "asset", "Loans to customers (principal)"),
    "provision": ("1005", "asset", "Loan loss provision"),
    "fees_receivable": ("1020", "asset", "Fees added to loan balances"),
    "interest_receivable": ("1030", "asset", "Accrued interest (month-end)"),
    "bank": ("1010", "asset", "Bank (bank payments, cheques, transfers)"),
    "mpesa_collections": ("1100", "asset", "M-Pesa paybill (repayments)"),
    "mpesa_disbursement": ("1110", "asset", "M-Pesa B2C (loan payouts)"),
    "cash": ("1130", "asset", "Cash on hand"),
    "suspense": ("1900", "asset", "Unallocated payments (suspense)"),
    "vat_input": ("1400", "asset", "VAT paid on purchases"),
    "excise": ("2000", "liability", "Excise duty on fees"),
    "payables": ("2100", "liability", "Supplier bills owed"),
    "claims_payable": ("2120", "liability", "Staff expense claims owed"),
    "wht_payable": ("2400", "liability", "Withholding tax deducted"),
    "opening_equity": ("3900", "equity", "Opening balances"),
    "interest_income": ("4000", "income", "Interest paid by customers"),
    "fee_income": ("4010", "income", "Loan fees"),
    "penalty_income": ("4020", "income", "Penalties paid by customers"),
    "recoveries": ("4030", "income", "Money recovered on written-off loans"),
    "other_income": ("4090", "income", "Other income (e.g. gain on selling an asset)"),
    "other_expense": ("6900", "expense", "Other expenses (e.g. loss on selling an asset)"),
    "writeoff": ("5000", "expense", "Loans written off"),
    "impairment": ("5010", "expense", "Change in loan loss provision"),
    "platform_fees": ("6150", "expense", "Finyl platform invoices"),
    "sms_expense": ("6120", "expense", "SMS on Finyl invoices"),
    "platform_payment_account": ("1010", "asset", "Account Finyl invoices are paid from"),
}
INTERNAL_ROLES = {
    "dcp_receivable": ("1210", "asset", "Invoices to DCPs"),
    "dcp_loans": ("1220", "asset", "Loans to DCPs"),
    "inc_subscription": ("4100", "income", "Subscription (share of loan book)"),
    "inc_onboarding": ("4110", "income", "Onboarding fees"),
    "inc_branch": ("4120", "income", "Branch fees"),
    "inc_sms": ("4130", "income", "SMS charged to DCPs"),
    "inc_other": ("4190", "income", "Other charges to DCPs"),
    "inc_facility_interest": ("4140", "income", "Interest on loans to DCPs"),
}


# --------------------------------------------------------------------------- helpers
def nairobi_date(value) -> date | None:
    """A stored (naive UTC) timestamp -> the Kenyan calendar day."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value + NAIROBI).date()
    return value


def today() -> date:
    return (datetime.utcnow() + NAIROBI).date()


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def fy_start(d: date, start_month: int) -> date:
    start_month = start_month if 1 <= (start_month or 1) <= 12 else 1
    year = d.year if d.month >= start_month else d.year - 1
    return date(year, start_month, 1)


def _n(v) -> float:
    return float(money(v or 0))


# --------------------------------------------------------------------------- setup
def is_internal(db: Session, tenant_id: int) -> bool:
    t = db.get(Tenant, tenant_id)
    return bool(t and getattr(t, "is_internal", False))


def ensure_books(db: Session, tenant_id: int, *, lock: bool = False) -> AccSettings:
    """Seed the chart of accounts (adding any default account the DCP is missing
    and filling in groups on the old accounts) and the settings row."""
    internal = is_internal(db, tenant_id)
    existing = {a.code: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == tenant_id)}
    system_codes = {c for c, *_ in (list(POSTING_ROLES.values()) + (list(INTERNAL_ROLES.values()) if internal else []))}
    for code, name, typ, sub, cash in DEFAULT_ACCOUNTS + (INTERNAL_ACCOUNTS if internal else []):
        a = existing.get(code)
        if a is None:
            db.add(ChartOfAccount(tenant_id=tenant_id, code=code, name=name, type=typ, subtype=sub,
                                  is_cash=cash, is_system=code in system_codes, active=True))
        elif not a.subtype:
            a.subtype = sub if a.type == typ else DEFAULT_SUBTYPE.get(a.type, "other_asset")
            a.is_cash = bool(cash and a.type == typ)
            a.is_system = code in system_codes
    for a in existing.values():
        if not a.subtype:
            a.subtype = DEFAULT_SUBTYPE.get(a.type, "other_asset")
    q = db.query(AccSettings).filter(AccSettings.tenant_id == tenant_id)
    s = (q.with_for_update().first() if lock else q.first())
    if s is None:
        s = AccSettings(tenant_id=tenant_id, posting_map={}, fiscal_year_start_month=1,
                        auto_post=True, journal_approval_limit=0, payment_approval_limit=0,
                        next_journal_no=1, next_auto_no=1, next_bill_no=1, next_claim_no=1,
                        next_payment_no=1, next_asset_no=1)
        db.add(s)
        db.flush()
        if lock:
            s = q.with_for_update().first()
    else:
        db.flush()
    return s


def roles_for(db: Session, tenant_id: int) -> dict:
    return {**POSTING_ROLES, **(INTERNAL_ROLES if is_internal(db, tenant_id) else {})}


class Books:
    """Per-request cache of a DCP's accounts and posting roles."""

    def __init__(self, db: Session, tenant_id: int, settings: AccSettings | None = None):
        self.db, self.tenant_id = db, tenant_id
        self.settings = settings or ensure_books(db, tenant_id)
        self.accounts = {a.code: a for a in db.query(ChartOfAccount)
                         .filter(ChartOfAccount.tenant_id == tenant_id)}
        self.by_id = {a.id: a for a in self.accounts.values()}
        self.roles = roles_for(db, tenant_id)

    def code(self, role: str) -> str:
        default = self.roles[role][0]
        return (self.settings.posting_map or {}).get(role) or default

    def account(self, role: str) -> ChartOfAccount:
        code = self.code(role)
        a = self.accounts.get(code)
        if a is None:
            a = self.accounts.get(self.roles[role][0])
        if a is None:
            raise HTTPException(409, f"The posting account for '{role}' ({code}) is missing from the chart of accounts")
        return a


# --------------------------------------------------------------------------- numbering
def take_number(db: Session, tenant_id: int, kind: str) -> str:
    """Next number for a journal / bill / claim / payment / asset. The settings row
    is locked so two people never get the same number."""
    s = (db.query(AccSettings).filter(AccSettings.tenant_id == tenant_id).with_for_update().first()
         or ensure_books(db, tenant_id, lock=True))
    col, prefix, width = {
        "journal": ("next_journal_no", "JV", 6), "auto": ("next_auto_no", "AJ", 7),
        "bill": ("next_bill_no", "BILL", 6), "claim": ("next_claim_no", "EXP", 6),
        "payment": ("next_payment_no", "PAY", 6), "asset": ("next_asset_no", "FA", 5),
    }[kind]
    n = getattr(s, col) or 1
    setattr(s, col, n + 1)
    return f"{prefix}-{n:0{width}d}"


# --------------------------------------------------------------------------- posting
@dataclass
class Line:
    account_id: int
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    description: str | None = None
    branch_id: int | None = None
    loan_id: int | None = None
    borrower_id: int | None = None
    supplier_id: int | None = None
    asset_id: int | None = None
    employee_id: int | None = None


def line(acct: ChartOfAccount, debit=0, credit=0, **kw) -> Line:
    return Line(account_id=acct.id, debit=money(debit), credit=money(credit), **kw)


def _normalise(lines: list[Line]) -> list[Line]:
    out = []
    for ln in lines:
        dr, cr = money(ln.debit or 0), money(ln.credit or 0)
        if dr < 0:
            cr, dr = cr - dr, ZERO
        if cr < 0:
            dr, cr = dr - cr, ZERO
        net = dr - cr
        if net == 0:
            continue
        ln.debit, ln.credit = (net, ZERO) if net > 0 else (ZERO, -net)
        out.append(ln)
    return out


class LockedPeriod(HTTPException):
    pass


def check_open(settings: AccSettings, d: date):
    if settings.lock_date and d <= settings.lock_date:
        raise HTTPException(409, f"The books are closed up to {settings.lock_date.isoformat()}. "
                                 f"Use a date after that, or reopen the period first.")


def post_journal(db: Session, tenant_id: int, *, entry_date: date, description: str,
                 lines: list[Line], kind: str = "manual", source_type: str | None = None,
                 source_id: int | None = None, user=None, status: str = "posted",
                 branch_id: int | None = None, reference: str | None = None,
                 settings: AccSettings | None = None, auto_date: bool = False) -> AccJournal:
    """Write one balanced journal. ``auto_date``: a record dated in a closed period
    is posted on the first open day instead (with the original date noted)."""
    settings = settings or ensure_books(db, tenant_id)
    lines = _normalise(lines)
    if not lines:
        raise HTTPException(422, "A journal needs at least one line with an amount")
    dr = sum((ln.debit for ln in lines), ZERO)
    cr = sum((ln.credit for ln in lines), ZERO)
    if dr != cr:
        raise HTTPException(422, f"The journal does not balance: debits {dr} vs credits {cr}")
    ids = {ln.account_id for ln in lines}
    accts = {a.id: a for a in db.query(ChartOfAccount).filter(ChartOfAccount.id.in_(ids))}
    for i in ids:
        a = accts.get(i)
        if a is None or a.tenant_id != tenant_id:
            raise HTTPException(422, "An account on this journal does not belong to this DCP")
        if not a.active and kind == "manual":
            raise HTTPException(422, f"Account {a.code} {a.name} is switched off")
    if settings.lock_date and entry_date <= settings.lock_date:
        if auto_date:
            description = f"{description} (dated {entry_date.isoformat()}, period closed)"[:300]
            entry_date = settings.lock_date + timedelta(days=1)
        elif status == "posted" or kind == "manual":
            check_open(settings, entry_date)
    j = AccJournal(tenant_id=tenant_id, number=take_number(db, tenant_id, "journal" if kind == "manual" else "auto"),
                   entry_date=entry_date, description=description[:300], kind=kind,
                   source_type=source_type, source_id=source_id, status=status, branch_id=branch_id,
                   total=dr, reference=(reference or None) and str(reference)[:60],
                   created_by_user_id=getattr(user, "id", None),
                   approved_by_user_id=getattr(user, "id", None) if status == "posted" and kind != "manual" else None,
                   posted_at=datetime.utcnow() if status == "posted" else None)
    db.add(j)
    db.flush()
    for ln in lines:
        db.add(AccJournalLine(journal_id=j.id, tenant_id=tenant_id, account_id=ln.account_id,
                              debit=ln.debit, credit=ln.credit,
                              description=(ln.description or None) and ln.description[:300],
                              branch_id=ln.branch_id if ln.branch_id is not None else branch_id,
                              loan_id=ln.loan_id, borrower_id=ln.borrower_id, supplier_id=ln.supplier_id,
                              asset_id=ln.asset_id, employee_id=ln.employee_id))
    db.flush()
    return j


def reverse_journal(db: Session, j: AccJournal, *, on: date, user, reason: str,
                    source_type: str | None = None, source_id: int | None = None) -> AccJournal:
    if j.status != "posted":
        raise HTTPException(409, f"Only a posted journal can be reversed (this one is {j.status})")
    if j.reversed_by_id:
        raise HTTPException(409, "This journal has already been reversed")
    settings = ensure_books(db, j.tenant_id)
    check_open(settings, on)
    lines = [Line(account_id=ln.account_id, debit=D(ln.credit), credit=D(ln.debit),
                  description=ln.description, branch_id=ln.branch_id, loan_id=ln.loan_id,
                  borrower_id=ln.borrower_id, supplier_id=ln.supplier_id, asset_id=ln.asset_id,
                  employee_id=ln.employee_id)
             for ln in db.query(AccJournalLine).filter(AccJournalLine.journal_id == j.id)]
    r = post_journal(db, j.tenant_id, entry_date=on, description=f"Reversal of {j.number}: {reason}",
                     lines=lines, kind="system", source_type=source_type or "reversal",
                     source_id=source_id if source_id is not None else j.id, user=user,
                     branch_id=j.branch_id, reference=j.number, settings=settings)
    r.reversal_of_id = j.id
    j.reversed_by_id = r.id
    j.status = "reversed"
    db.flush()
    return r


# --------------------------------------------------------------------------- balances
COUNTED = ("posted", "reversed")


def _lines_query(db, tenant_id, date_from=None, date_to=None, branch_id=None):
    q = (db.query(AccJournalLine.account_id,
                  func.coalesce(func.sum(AccJournalLine.debit), 0),
                  func.coalesce(func.sum(AccJournalLine.credit), 0))
         .join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
         .filter(AccJournalLine.tenant_id == tenant_id, AccJournal.status.in_(COUNTED)))
    if date_from:
        q = q.filter(AccJournal.entry_date >= date_from)
    if date_to:
        q = q.filter(AccJournal.entry_date <= date_to)
    if branch_id:
        q = q.filter(AccJournalLine.branch_id == branch_id)
    return q.group_by(AccJournalLine.account_id)


def movements(db, tenant_id, date_from=None, date_to=None, branch_id=None) -> dict[int, tuple[Decimal, Decimal]]:
    return {aid: (D(dr), D(cr)) for aid, dr, cr in _lines_query(db, tenant_id, date_from, date_to, branch_id)}


def natural(acct: ChartOfAccount, dr, cr) -> Decimal:
    """Balance with the sign people expect: assets/expenses debit, the rest credit."""
    return money(D(dr) - D(cr)) if acct.type in ("asset", "expense") else money(D(cr) - D(dr))


def account_balance(db, tenant_id, account_id, as_at: date | None = None) -> Decimal:
    q = (db.query(func.coalesce(func.sum(AccJournalLine.debit - AccJournalLine.credit), 0))
         .join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
         .filter(AccJournalLine.tenant_id == tenant_id, AccJournalLine.account_id == account_id,
                 AccJournal.status.in_(COUNTED)))
    if as_at:
        q = q.filter(AccJournal.entry_date <= as_at)
    return money(q.scalar() or 0)


def _accounts(db, tenant_id) -> list[ChartOfAccount]:
    return (db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == tenant_id)
            .order_by(ChartOfAccount.code).all())


def trial_balance(db, tenant_id, date_from: date, date_to: date, branch_id=None) -> dict:
    opening = movements(db, tenant_id, None, date_from - timedelta(days=1), branch_id)
    period = movements(db, tenant_id, date_from, date_to, branch_id)
    rows, tdr, tcr = [], ZERO, ZERO
    for a in _accounts(db, tenant_id):
        o = opening.get(a.id, (ZERO, ZERO))
        p = period.get(a.id, (ZERO, ZERO))
        close = money(o[0] + p[0] - o[1] - p[1])
        if not any((o[0], o[1], p[0], p[1])):
            continue
        dr, cr = (close, ZERO) if close >= 0 else (ZERO, -close)
        tdr += dr
        tcr += cr
        rows.append({"account_id": a.id, "code": a.code, "name": a.name, "type": a.type,
                     "subtype": a.subtype, "opening": _n(o[0] - o[1]), "debits": _n(p[0]),
                     "credits": _n(p[1]), "closing_debit": _n(dr), "closing_credit": _n(cr)})
    return {"from": date_from.isoformat(), "to": date_to.isoformat(), "rows": rows,
            "total_debit": _n(tdr), "total_credit": _n(tcr), "balanced": money(tdr) == money(tcr)}


def _group(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out


def profit_and_loss(db, tenant_id, date_from: date, date_to: date, branch_id=None) -> dict:
    mv = movements(db, tenant_id, date_from, date_to, branch_id)
    income, expense = [], []
    for a in _accounts(db, tenant_id):
        if a.type not in ("income", "expense") or a.id not in mv:
            continue
        amt = natural(a, *mv[a.id])
        if amt == 0:
            continue
        row = {"account_id": a.id, "code": a.code, "name": a.name,
               "subtype": a.subtype or DEFAULT_SUBTYPE[a.type], "amount": _n(amt)}
        (income if a.type == "income" else expense).append(row)

    def sections(rows, order):
        g = _group(rows, "subtype")
        return [{"key": k, "label": SUBTYPES.get(k, ("", k))[1], "rows": g[k],
                 "total": round(sum(r["amount"] for r in g[k]), 2)} for k in order if g.get(k)] + \
               [{"key": k, "label": SUBTYPES.get(k, ("", k))[1], "rows": v,
                 "total": round(sum(r["amount"] for r in v), 2)} for k, v in g.items() if k not in order]

    inc = sections(income, ["interest_income", "fee_income", "other_income"])
    exp = sections(expense, ["credit_loss", "staff_cost", "operating", "depreciation", "finance_cost"])
    ti = round(sum(s["total"] for s in inc), 2)
    te = round(sum(s["total"] for s in exp), 2)
    return {"from": date_from.isoformat(), "to": date_to.isoformat(), "income": inc, "expenses": exp,
            "total_income": ti, "total_expenses": te, "net_profit": round(ti - te, 2)}


def _profit_between(db, tenant_id, date_from, date_to) -> Decimal:
    # profit = income (credit balances) - expenses (debit balances) = sum(credit - debit)
    q = (db.query(func.coalesce(func.sum(AccJournalLine.credit - AccJournalLine.debit), 0))
         .join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
         .join(ChartOfAccount, ChartOfAccount.id == AccJournalLine.account_id)
         .filter(AccJournalLine.tenant_id == tenant_id, AccJournal.status.in_(COUNTED),
                 ChartOfAccount.type.in_(("income", "expense"))))
    if date_from:
        q = q.filter(AccJournal.entry_date >= date_from)
    if date_to:
        q = q.filter(AccJournal.entry_date <= date_to)
    return money(q.scalar() or 0)


def balance_sheet(db, tenant_id, as_at: date, start_month: int = 1) -> dict:
    mv = movements(db, tenant_id, None, as_at)
    groups = {"asset": [], "liability": [], "equity": []}
    for a in _accounts(db, tenant_id):
        if a.type not in groups or a.id not in mv:
            continue
        amt = natural(a, *mv[a.id])
        if amt == 0:
            continue
        groups[a.type].append({"account_id": a.id, "code": a.code, "name": a.name,
                               "subtype": a.subtype or DEFAULT_SUBTYPE[a.type], "amount": _n(amt)})
    fy = fy_start(as_at, start_month)
    prior = _profit_between(db, tenant_id, None, fy - timedelta(days=1))
    current = _profit_between(db, tenant_id, fy, as_at)
    if prior:
        groups["equity"].append({"account_id": None, "code": "", "name": "Retained earnings (earlier years)",
                                 "subtype": "equity", "amount": _n(prior)})
    groups["equity"].append({"account_id": None, "code": "", "name": f"Profit this financial year (from {fy.isoformat()})",
                             "subtype": "equity", "amount": _n(current)})

    def sections(rows, order):
        g = _group(rows, "subtype")
        keys = [k for k in order if g.get(k)] + [k for k in g if k not in order]
        return [{"key": k, "label": SUBTYPES.get(k, ("", k))[1], "rows": g[k],
                 "total": round(sum(r["amount"] for r in g[k]), 2)} for k in keys]

    assets = sections(groups["asset"], ["cash", "loan_book", "provision", "receivable", "other_asset",
                                        "fixed_asset", "accum_dep"])
    liabilities = sections(groups["liability"], ["payable", "tax", "other_liability", "borrowing"])
    equity = sections(groups["equity"], ["equity"])
    ta = round(sum(s["total"] for s in assets), 2)
    tl = round(sum(s["total"] for s in liabilities), 2)
    te = round(sum(s["total"] for s in equity), 2)
    return {"as_at": as_at.isoformat(), "assets": assets, "liabilities": liabilities, "equity": equity,
            "total_assets": ta, "total_liabilities": tl, "total_equity": te,
            "balanced": round(ta - tl - te, 2) == 0}


def cash_flow(db, tenant_id, date_from: date, date_to: date) -> dict:
    """Direct method: every journal that moved money in a cash/bank/M-Pesa account,
    with the money split over the other accounts on that journal."""
    accts = {a.id: a for a in _accounts(db, tenant_id)}
    cash_ids = {i for i, a in accts.items() if a.is_cash}
    q = (db.query(AccJournalLine)
         .join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
         .filter(AccJournalLine.tenant_id == tenant_id, AccJournal.status.in_(COUNTED),
                 AccJournal.entry_date >= date_from, AccJournal.entry_date <= date_to,
                 AccJournalLine.journal_id.in_(
                     db.query(AccJournalLine.journal_id).filter(AccJournalLine.tenant_id == tenant_id,
                                                                AccJournalLine.account_id.in_(cash_ids or {-1})))))
    by_journal = defaultdict(list)
    for ln in q:
        by_journal[ln.journal_id].append(ln)
    buckets = defaultdict(lambda: defaultdict(lambda: ZERO))   # section -> label -> amount
    for lines in by_journal.values():
        cash_net = sum((D(l.debit) - D(l.credit) for l in lines if l.account_id in cash_ids), ZERO)
        others = [l for l in lines if l.account_id not in cash_ids]
        weight = sum((D(l.credit) - D(l.debit) for l in others), ZERO)
        if cash_net == 0 or weight == 0:
            continue          # a transfer between two money accounts
        for l in others:
            share = money(cash_net * (D(l.credit) - D(l.debit)) / weight)
            a = accts[l.account_id]
            sub = a.subtype or DEFAULT_SUBTYPE[a.type]
            section = SUBTYPES.get(sub, ("", "", "operating"))[2]
            label = {"loan_book": "Loans paid out and repaid (principal)",
                     "provision": "Loans paid out and repaid (principal)"}.get(sub) or \
                SUBTYPES.get(sub, ("", a.name))[1]
            buckets[section][label] += share
    order = [("operating", "Operating activities"), ("lending", "Lending to customers"),
             ("investing", "Investing activities"), ("financing", "Financing activities")]
    sections, net = [], ZERO
    for key, title in order:
        rows = [{"label": k, "amount": _n(v)} for k, v in sorted(buckets[key].items()) if money(v) != 0]
        tot = sum((money(v) for v in buckets[key].values()), ZERO)
        net += tot
        sections.append({"key": key, "label": title, "rows": rows, "total": _n(tot)})
    opening = sum((account_balance(db, tenant_id, i, date_from - timedelta(days=1)) for i in cash_ids), ZERO)
    closing = sum((account_balance(db, tenant_id, i, date_to) for i in cash_ids), ZERO)
    return {"from": date_from.isoformat(), "to": date_to.isoformat(), "sections": sections,
            "net_change": _n(net), "opening_cash": _n(opening), "closing_cash": _n(closing),
            "check": _n(closing - opening - net)}


def account_ledger(db, tenant_id, account_id: int, date_from: date, date_to: date,
                   limit: int = 2000) -> dict:
    a = db.get(ChartOfAccount, account_id)
    if a is None or a.tenant_id != tenant_id:
        raise HTTPException(404, "Account not found")
    opening = account_balance(db, tenant_id, a.id, date_from - timedelta(days=1))
    rows = (db.query(AccJournalLine, AccJournal)
            .join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
            .filter(AccJournalLine.tenant_id == tenant_id, AccJournalLine.account_id == a.id,
                    AccJournal.status.in_(COUNTED), AccJournal.entry_date >= date_from,
                    AccJournal.entry_date <= date_to)
            .order_by(AccJournal.entry_date, AccJournal.id, AccJournalLine.id).limit(limit + 1).all())
    run = opening
    out = []
    for ln, j in rows[:limit]:
        run = money(run + D(ln.debit) - D(ln.credit))
        out.append({"journal_id": j.id, "number": j.number, "date": j.entry_date.isoformat(),
                    "description": ln.description or j.description, "reference": j.reference,
                    "debit": _n(ln.debit), "credit": _n(ln.credit), "balance": _n(run),
                    "loan_id": ln.loan_id, "reconciled": bool(ln.reconciled), "line_id": ln.id})
    sign = 1 if a.type in ("asset", "expense") else -1
    return {"account": {"id": a.id, "code": a.code, "name": a.name, "type": a.type},
            "from": date_from.isoformat(), "to": date_to.isoformat(),
            "opening": _n(opening), "closing": _n(run), "rows": out, "truncated": len(rows) > limit,
            "natural_closing": _n(run * sign)}


def journal_dict(db, j: AccJournal, with_lines: bool = True) -> dict:
    d = {"id": j.id, "number": j.number, "date": j.entry_date.isoformat(), "description": j.description,
         "kind": j.kind, "source_type": j.source_type, "source_id": j.source_id, "status": j.status,
         "total": _n(j.total), "reference": j.reference, "branch_id": j.branch_id,
         "created_by_user_id": j.created_by_user_id, "approved_by_user_id": j.approved_by_user_id,
         "reversal_of_id": j.reversal_of_id, "reversed_by_id": j.reversed_by_id,
         "reject_reason": j.reject_reason,
         "created_at": j.created_at.isoformat() if j.created_at else None}
    if with_lines:
        accts = {}
        lines = db.query(AccJournalLine).filter(AccJournalLine.journal_id == j.id).order_by(AccJournalLine.id).all()
        for a in db.query(ChartOfAccount).filter(ChartOfAccount.id.in_({l.account_id for l in lines} or {-1})):
            accts[a.id] = a
        d["lines"] = [{"id": l.id, "account_id": l.account_id, "code": accts[l.account_id].code,
                       "name": accts[l.account_id].name, "debit": _n(l.debit), "credit": _n(l.credit),
                       "description": l.description, "branch_id": l.branch_id, "loan_id": l.loan_id,
                       "supplier_id": l.supplier_id, "asset_id": l.asset_id, "employee_id": l.employee_id}
                      for l in lines]
    return d
