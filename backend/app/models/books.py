"""WP14 — ERP accounting (migration 043).

Real double-entry books for each DCP, and for Finyl's own workspace:
  * AccSettings      — per-DCP accounting settings (posting map, period lock, numbering)
  * AccJournal/Line  — the ledger. Auto journals come from loans, payments and the
                       other modules (one journal per source record); manual journals
                       are entered by the accountant and approved by a second person.
  * payables         — suppliers, bills, staff expense claims, payments out
  * reconciliation   — bank / M-Pesa statements and their lines, matched to the ledger
  * budgets          — per financial year, optionally per branch, per account per month
  * fixed assets     — categories, the register, monthly depreciation runs
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, ForeignKey, Index, Integer, Numeric, String,
                        Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.core.timeutil import UTCDateTime


class AccSettings(Base):
    __tablename__ = "acc_settings"

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    fiscal_year_start_month = Column(Integer, nullable=False, default=1)
    books_start = Column(Date)                 # loans/payments before this are not posted
    lock_date = Column(Date)                   # nothing may be posted on or before this date
    auto_post = Column(Boolean, nullable=False, default=True)
    posting_map = Column(JSONB, nullable=False, default=dict)   # {role: account code}
    journal_approval_limit = Column(Numeric(16, 2), nullable=False, default=0)
    payment_approval_limit = Column(Numeric(16, 2), nullable=False, default=0)
    next_journal_no = Column(Integer, nullable=False, default=1)
    next_auto_no = Column(Integer, nullable=False, default=1)
    next_bill_no = Column(Integer, nullable=False, default=1)
    next_claim_no = Column(Integer, nullable=False, default=1)
    next_payment_no = Column(Integer, nullable=False, default=1)
    next_asset_no = Column(Integer, nullable=False, default=1)
    last_auto_post_at = Column(UTCDateTime)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(UTCDateTime, default=datetime.utcnow)


class AccJournal(Base):
    __tablename__ = "acc_journals"
    __table_args__ = (
        Index("uq_acc_journal_number", "tenant_id", "number", unique=True),
        Index("uq_acc_journal_source", "tenant_id", "source_type", "source_id", unique=True,
              postgresql_where=text("source_id IS NOT NULL")),
        Index("ix_acc_journals_tenant_date", "tenant_id", "entry_date"),
        Index("ix_acc_journals_status", "tenant_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    number = Column(String(30), nullable=False)
    entry_date = Column(Date, nullable=False)
    description = Column(String(300), nullable=False)
    kind = Column(String(10), nullable=False, default="manual")     # auto | manual | system
    source_type = Column(String(30))
    source_id = Column(Integer)
    # pending (waiting for a second person) | posted | reversed | rejected
    status = Column(String(12), nullable=False, default="posted")
    branch_id = Column(Integer, ForeignKey("branches.id"))
    total = Column(Numeric(16, 2), nullable=False, default=0)
    reference = Column(String(60))
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    approved_by_user_id = Column(Integer, ForeignKey("users.id"))
    posted_at = Column(UTCDateTime)
    reversal_of_id = Column(Integer, ForeignKey("acc_journals.id"))
    reversed_by_id = Column(Integer, ForeignKey("acc_journals.id"))
    reject_reason = Column(Text)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class AccJournalLine(Base):
    __tablename__ = "acc_journal_lines"
    __table_args__ = (
        Index("ix_acc_lines_journal", "journal_id"),
        Index("ix_acc_lines_account", "tenant_id", "account_id"),
        Index("ix_acc_lines_loan", "loan_id", postgresql_where=text("loan_id IS NOT NULL")),
    )

    id = Column(Integer, primary_key=True)
    journal_id = Column(Integer, ForeignKey("acc_journals.id", ondelete="CASCADE"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("chart_of_accounts.id"), nullable=False)
    debit = Column(Numeric(16, 2), nullable=False, default=0)
    credit = Column(Numeric(16, 2), nullable=False, default=0)
    description = Column(String(300))
    branch_id = Column(Integer, ForeignKey("branches.id"))
    loan_id = Column(Integer, ForeignKey("loans.id"))
    borrower_id = Column(Integer, ForeignKey("borrowers.id"))
    supplier_id = Column(Integer)
    asset_id = Column(Integer)
    employee_id = Column(Integer)
    reconciled = Column(Boolean, nullable=False, default=False)


# --------------------------------------------------------------------------- payables
class AccSupplier(Base):
    __tablename__ = "acc_suppliers"
    __table_args__ = (Index("uq_acc_supplier_name", "tenant_id", "name", unique=True),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(160), nullable=False)
    kra_pin = Column(String(20))
    phone = Column(String(20))
    email = Column(String(160))
    payment_method = Column(String(15), default="bank")      # bank | mpesa | cash | cheque
    mpesa_number = Column(String(30))                         # phone, till or paybill#account
    bank_name = Column(String(80))
    bank_account = Column(String(40))
    default_expense_account_id = Column(Integer, ForeignKey("chart_of_accounts.id"))
    wht_rate = Column(Numeric(5, 2), nullable=False, default=0)   # withholding tax %, e.g. 5
    notes = Column(Text)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class AccBill(Base):
    __tablename__ = "acc_bills"
    __table_args__ = (
        Index("uq_acc_bill_number", "tenant_id", "number", unique=True),
        Index("uq_acc_bill_source", "tenant_id", "source_type", "source_id", unique=True,
              postgresql_where=text("source_id IS NOT NULL")),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    number = Column(String(30), nullable=False)
    supplier_id = Column(Integer, ForeignKey("acc_suppliers.id"), nullable=False)
    supplier_ref = Column(String(60))
    bill_date = Column(Date, nullable=False)
    due_date = Column(Date)
    branch_id = Column(Integer, ForeignKey("branches.id"))
    description = Column(String(300))
    subtotal = Column(Numeric(16, 2), nullable=False, default=0)
    vat = Column(Numeric(16, 2), nullable=False, default=0)
    wht = Column(Numeric(16, 2), nullable=False, default=0)
    total = Column(Numeric(16, 2), nullable=False, default=0)     # what is owed to the supplier
    paid = Column(Numeric(16, 2), nullable=False, default=0)
    # pending (approval) | approved | part_paid | paid | rejected | void
    status = Column(String(12), nullable=False, default="pending")
    source_type = Column(String(30))
    source_id = Column(Integer)
    journal_id = Column(Integer, ForeignKey("acc_journals.id"))
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    approved_by_user_id = Column(Integer, ForeignKey("users.id"))
    void_reason = Column(Text)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class AccBillLine(Base):
    __tablename__ = "acc_bill_lines"

    id = Column(Integer, primary_key=True)
    bill_id = Column(Integer, ForeignKey("acc_bills.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(Integer, ForeignKey("chart_of_accounts.id"), nullable=False)
    description = Column(String(300))
    amount = Column(Numeric(16, 2), nullable=False, default=0)    # before VAT
    vat_rate = Column(Numeric(5, 2), nullable=False, default=0)
    vat = Column(Numeric(16, 2), nullable=False, default=0)
    branch_id = Column(Integer, ForeignKey("branches.id"))


class AccExpenseClaim(Base):
    __tablename__ = "acc_expense_claims"
    __table_args__ = (Index("uq_acc_claim_number", "tenant_id", "number", unique=True),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    number = Column(String(30), nullable=False)
    claimant_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    claim_date = Column(Date, nullable=False)
    description = Column(String(300), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"))
    total = Column(Numeric(16, 2), nullable=False, default=0)
    paid = Column(Numeric(16, 2), nullable=False, default=0)
    # submitted | approved | rejected | paid
    status = Column(String(12), nullable=False, default="submitted")
    approved_by_user_id = Column(Integer, ForeignKey("users.id"))
    reject_reason = Column(Text)
    journal_id = Column(Integer, ForeignKey("acc_journals.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class AccExpenseClaimLine(Base):
    __tablename__ = "acc_expense_claim_lines"

    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("acc_expense_claims.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(Integer, ForeignKey("chart_of_accounts.id"), nullable=False)
    description = Column(String(300))
    amount = Column(Numeric(16, 2), nullable=False, default=0)
    receipt_ref = Column(String(60))


class AccPaymentOut(Base):
    __tablename__ = "acc_payments_out"
    __table_args__ = (Index("uq_acc_payment_number", "tenant_id", "number", unique=True),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    number = Column(String(30), nullable=False)
    kind = Column(String(10), nullable=False)              # bill | claim
    bill_id = Column(Integer, ForeignKey("acc_bills.id"))
    claim_id = Column(Integer, ForeignKey("acc_expense_claims.id"))
    supplier_id = Column(Integer, ForeignKey("acc_suppliers.id"))
    payee = Column(String(160))
    amount = Column(Numeric(16, 2), nullable=False)
    paid_on = Column(Date, nullable=False)
    account_id = Column(Integer, ForeignKey("chart_of_accounts.id"), nullable=False)  # paid from
    method = Column(String(20))
    reference = Column(String(60))
    status = Column(String(12), nullable=False, default="pending")   # pending | posted | rejected
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    approved_by_user_id = Column(Integer, ForeignKey("users.id"))
    reject_reason = Column(Text)
    journal_id = Column(Integer, ForeignKey("acc_journals.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class AccAttachment(Base):
    __tablename__ = "acc_attachments"
    __table_args__ = (Index("ix_acc_attachments_entity", "tenant_id", "entity_type", "entity_id"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    entity_type = Column(String(20), nullable=False)     # bill | claim | journal | asset
    entity_id = Column(Integer, nullable=False)
    filename = Column(String(200), nullable=False)
    storage_path = Column(String(400), nullable=False)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- reconciliation
class AccBankStatement(Base):
    __tablename__ = "acc_bank_statements"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("chart_of_accounts.id"), nullable=False)
    filename = Column(String(200))
    period_from = Column(Date)
    period_to = Column(Date)
    closing_balance = Column(Numeric(16, 2))
    line_count = Column(Integer, nullable=False, default=0)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class AccBankLine(Base):
    __tablename__ = "acc_bank_lines"
    __table_args__ = (
        Index("uq_acc_bank_line", "tenant_id", "account_id", "fingerprint", unique=True),
        Index("ix_acc_bank_lines_status", "tenant_id", "account_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("acc_bank_statements.id", ondelete="CASCADE"),
                          nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("chart_of_accounts.id"), nullable=False)
    txn_date = Column(Date, nullable=False)
    description = Column(String(400))
    reference = Column(String(80))
    amount = Column(Numeric(16, 2), nullable=False)          # + money in, - money out
    fingerprint = Column(String(64), nullable=False)
    status = Column(String(12), nullable=False, default="unmatched")  # unmatched | matched | ignored
    journal_line_id = Column(Integer, ForeignKey("acc_journal_lines.id"))
    matched_by_user_id = Column(Integer, ForeignKey("users.id"))
    matched_at = Column(UTCDateTime)
    note = Column(Text)


# --------------------------------------------------------------------------- budgets
class AccBudget(Base):
    __tablename__ = "acc_budgets"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(120), nullable=False)
    fiscal_year = Column(Integer, nullable=False)          # the year the financial year starts in
    branch_id = Column(Integer, ForeignKey("branches.id"))
    status = Column(String(12), nullable=False, default="draft")    # draft | approved
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    approved_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class AccBudgetLine(Base):
    __tablename__ = "acc_budget_lines"
    __table_args__ = (UniqueConstraint("budget_id", "account_id", "month", name="uq_acc_budget_line"),)

    id = Column(Integer, primary_key=True)
    budget_id = Column(Integer, ForeignKey("acc_budgets.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(Integer, ForeignKey("chart_of_accounts.id"), nullable=False)
    month = Column(Integer, nullable=False)                # 1..12 = month of the financial year
    amount = Column(Numeric(16, 2), nullable=False, default=0)


# --------------------------------------------------------------------------- fixed assets
class AccAssetCategory(Base):
    __tablename__ = "acc_asset_categories"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_acc_asset_category"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(80), nullable=False)
    asset_account_id = Column(Integer, ForeignKey("chart_of_accounts.id"), nullable=False)
    accum_account_id = Column(Integer, ForeignKey("chart_of_accounts.id"), nullable=False)
    expense_account_id = Column(Integer, ForeignKey("chart_of_accounts.id"), nullable=False)
    method = Column(String(20), nullable=False, default="straight_line")  # straight_line | reducing_balance
    useful_life_months = Column(Integer, nullable=False, default=36)
    rate_pct = Column(Numeric(6, 2), nullable=False, default=0)            # yearly, reducing balance


class AccAsset(Base):
    __tablename__ = "acc_assets"
    __table_args__ = (Index("uq_acc_asset_number", "tenant_id", "number", unique=True),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    number = Column(String(30), nullable=False)
    name = Column(String(160), nullable=False)
    category_id = Column(Integer, ForeignKey("acc_asset_categories.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"))
    custodian = Column(String(120))
    serial_no = Column(String(80))
    purchase_date = Column(Date, nullable=False)
    in_service_date = Column(Date, nullable=False)
    cost = Column(Numeric(16, 2), nullable=False)
    residual_value = Column(Numeric(16, 2), nullable=False, default=0)
    method = Column(String(20), nullable=False, default="straight_line")
    useful_life_months = Column(Integer, nullable=False, default=36)
    rate_pct = Column(Numeric(6, 2), nullable=False, default=0)
    accumulated_depreciation = Column(Numeric(16, 2), nullable=False, default=0)
    # active | fully_depreciated | disposed
    status = Column(String(20), nullable=False, default="active")
    # how it was paid for: paid (from a bank/cash account) | bill (on a supplier bill,
    # already in the books) | opening (owned before the books started)
    funding = Column(String(12), nullable=False, default="paid")
    supplier_id = Column(Integer, ForeignKey("acc_suppliers.id"))
    acquisition_journal_id = Column(Integer, ForeignKey("acc_journals.id"))
    disposed_on = Column(Date)
    disposal_proceeds = Column(Numeric(16, 2))
    disposal_journal_id = Column(Integer, ForeignKey("acc_journals.id"))
    notes = Column(Text)
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class AccDepreciationRun(Base):
    __tablename__ = "acc_depreciation_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "period", name="uq_acc_depreciation_period"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    period = Column(Date, nullable=False)                  # last day of the month depreciated
    total = Column(Numeric(16, 2), nullable=False, default=0)
    journal_id = Column(Integer, ForeignKey("acc_journals.id"))
    run_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime, default=datetime.utcnow)


class AccDepreciationLine(Base):
    __tablename__ = "acc_depreciation_lines"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("acc_depreciation_runs.id", ondelete="CASCADE"), nullable=False)
    asset_id = Column(Integer, ForeignKey("acc_assets.id"), nullable=False)
    amount = Column(Numeric(16, 2), nullable=False)
