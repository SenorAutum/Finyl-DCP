"""
Accounting — Chart of Accounts + double-entry General Ledger export.

Read-only reporting surface built additively over the existing money artifacts
(disbursements, repayments and suspense movements). It never mutates financial
records; it maps them into balanced double-entry GL lines (total debits == total
credits) with Decimal-accurate amounts, and exports them in a CSV shape that
Xero / QuickBooks can import, or as JSON.

Gating: `require_permission("accounting.export")` only — no new module key.
"""
import csv
import io
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, require_permission
from app.core.money import D, money
from app.models import (Loan, PaymentTransaction, Repayment, User,
                        ChartOfAccount, SuspenseEntry)

router = APIRouter(prefix="/api/v1/accounting", tags=["accounting"])

# Default Chart of Accounts (mirrors migration 014 seed) — used to lazily seed
# tenants created after the migration ran.
DEFAULT_COA = [
    ("1000", "Loans Receivable", "asset"),
    ("1010", "Operational Float / Cash", "asset"),
    ("1900", "Suspense Account", "asset"),
    ("2000", "Excise Duty Payable", "liability"),
    ("4000", "Interest Income", "income"),
    ("4010", "Fee Income", "income"),
    ("4020", "Penalty Income", "income"),
    ("5000", "Loan Write-offs", "expense"),
]

# GL account handles
ACC_LOANS_RECEIVABLE = "1000"
ACC_CASH = "1010"
ACC_SUSPENSE = "1900"
ACC_INTEREST_INCOME = "4000"


def _seed_coa_if_empty(db: Session, tenant_id: int) -> None:
    """Seed the default Chart of Accounts for a tenant that has none."""
    exists = (db.query(ChartOfAccount)
              .filter(ChartOfAccount.tenant_id == tenant_id).first())
    if exists:
        return
    for code, name, typ in DEFAULT_COA:
        db.add(ChartOfAccount(tenant_id=tenant_id, code=code, name=name, type=typ))
    db.commit()


def _coa_map(db: Session, tenant_id: int) -> dict:
    rows = (db.query(ChartOfAccount)
            .filter(ChartOfAccount.tenant_id == tenant_id).all())
    return {r.code: r.name for r in rows}


@router.get("/chart-of-accounts")
def chart_of_accounts(tenant_id: int = Depends(get_tenant_id),
                      db: Session = Depends(get_db),
                      _: User = Depends(require_permission("accounting.export"))):
    """Return the tenant's Chart of Accounts (seeding defaults if empty)."""
    _seed_coa_if_empty(db, tenant_id)
    rows = (db.query(ChartOfAccount)
            .filter(ChartOfAccount.tenant_id == tenant_id)
            .order_by(ChartOfAccount.code.asc()).all())
    return {"tenant_id": tenant_id, "accounts": [{
        "code": r.code, "name": r.name, "type": r.type, "active": bool(r.active),
    } for r in rows]}


def _parse_date(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(422, f"{field} must be YYYY-MM-DD")


def _build_gl_lines(db: Session, tenant_id: int, dfrom: date, dto: date,
                    coa: dict) -> list[dict]:
    """Produce balanced double-entry GL lines for the window [dfrom, dto].

    Mapping (each event is internally balanced):
      * Disbursement (b2c, success): Dr Loans Receivable / Cr Cash
      * Repayment:                   Dr Cash / Cr Loans Receivable (principal)
                                              + Cr Interest Income (interest)
      * Suspense received:           Dr Cash / Cr Suspense
      * Suspense allocated/refunded: Dr Suspense / Cr Cash (the loan-recovery leg
                                     of an allocation is already in Repayments)
    """
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    lines: list[dict] = []

    def add(d: date, code: str, description: str, debit, credit, ref):
        lines.append({
            "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
            "account_code": code,
            "account_name": coa.get(code, code),
            "description": description,
            "debit": money(debit) if debit else D("0.00"),
            "credit": money(credit) if credit else D("0.00"),
            "reference": ref or "",
        })

    # Disbursements — money out to borrowers
    disbursements = (db.query(PaymentTransaction)
                     .filter(PaymentTransaction.tenant_id == tenant_id,
                             PaymentTransaction.type == "b2c",
                             PaymentTransaction.status == "success",
                             PaymentTransaction.created_at >= start,
                             PaymentTransaction.created_at <= end).all())
    for t in disbursements:
        d = t.created_at.date() if t.created_at else dfrom
        amt = D(t.amount or 0)
        ref = t.mpesa_ref or f"TXN-{t.id}"
        desc = f"Loan disbursement (loan #{t.loan_id})"
        add(d, ACC_LOANS_RECEIVABLE, desc, amt, 0, ref)
        add(d, ACC_CASH, desc, 0, amt, ref)

    # Repayments — money in from borrowers (interest/principal split)
    repayments = (db.query(Repayment)
                  .filter(Repayment.tenant_id == tenant_id,
                          Repayment.payment_date >= start,
                          Repayment.payment_date <= end).all())
    for r in repayments:
        d = r.payment_date.date() if r.payment_date else dfrom
        total = D(r.amount or 0)
        principal = D(r.principal_component or 0)
        interest = D(r.interest_component or 0)
        # Guard against a missing/zero split so the entry stays balanced.
        if money(principal + interest) != money(total):
            principal = total
            interest = D(0)
        ref = r.mpesa_ref or f"REP-{r.id}"
        desc = f"Loan repayment (loan #{r.loan_id})"
        add(d, ACC_CASH, desc, total, 0, ref)
        add(d, ACC_LOANS_RECEIVABLE, desc, 0, principal, ref)
        if interest > 0:
            add(d, ACC_INTEREST_INCOME, desc, 0, interest, ref)

    # Suspense movements
    suspense = (db.query(SuspenseEntry)
                .filter(SuspenseEntry.tenant_id == tenant_id).all())
    for s in suspense:
        amt = D(s.amount or 0)
        ref = s.mpesa_ref or f"SUS-{s.id}"
        # Received into suspense (created in window)
        if s.created_at and start <= s.created_at <= end:
            d = s.created_at.date()
            desc = f"Suspense receipt ({s.reason})"
            add(d, ACC_CASH, desc, amt, 0, ref)
            add(d, ACC_SUSPENSE, desc, 0, amt, ref)
        # Resolved out of suspense (allocated / refunded in window)
        if (s.status in ("allocated", "refunded") and s.resolved_at
                and start <= s.resolved_at <= end):
            d = s.resolved_at.date()
            desc = f"Suspense {s.status}"
            add(d, ACC_SUSPENSE, desc, amt, 0, ref)
            add(d, ACC_CASH, desc, 0, amt, ref)

    lines.sort(key=lambda x: (x["date"], x["account_code"]))
    return lines


@router.get("/gl-export")
def gl_export(from_: str = Query(None, alias="from"),
              to: str = Query(None),
              format: str = Query("csv"),
              tenant_id: int = Depends(get_tenant_id),
              db: Session = Depends(get_db),
              _: User = Depends(require_permission("accounting.export"))):
    """Export the General Ledger for [from, to] as balanced double-entry lines.

    ``format=csv`` (default) returns a Xero/QuickBooks-friendly file; ``format=json``
    returns the same lines plus totals for programmatic use."""
    if format not in ("csv", "json"):
        raise HTTPException(422, "format must be 'csv' or 'json'")
    dto = _parse_date(to, "to") if to else date.today()
    dfrom = _parse_date(from_, "from") if from_ else date(dto.year, 1, 1)
    if dfrom > dto:
        raise HTTPException(422, "'from' must not be after 'to'")

    _seed_coa_if_empty(db, tenant_id)
    coa = _coa_map(db, tenant_id)
    lines = _build_gl_lines(db, tenant_id, dfrom, dto, coa)

    total_debit = money(sum((ln["debit"] for ln in lines), D(0)))
    total_credit = money(sum((ln["credit"] for ln in lines), D(0)))
    balanced = total_debit == total_credit

    if format == "json":
        return {
            "tenant_id": tenant_id,
            "from": dfrom.isoformat(), "to": dto.isoformat(),
            "total_debit": float(total_debit),
            "total_credit": float(total_credit),
            "balanced": balanced,
            "line_count": len(lines),
            "lines": [{
                "date": ln["date"], "account_code": ln["account_code"],
                "account_name": ln["account_name"], "description": ln["description"],
                "debit": float(ln["debit"]), "credit": float(ln["credit"]),
                "reference": ln["reference"],
            } for ln in lines],
        }

    # CSV — Xero/QuickBooks generic journal shape
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Account Code", "Account Name", "Description",
                     "Debit", "Credit", "Reference"])
    for ln in lines:
        writer.writerow([ln["date"], ln["account_code"], ln["account_name"],
                         ln["description"], f"{ln['debit']:.2f}",
                         f"{ln['credit']:.2f}", ln["reference"]])
    filename = f"gl_export_{dfrom.isoformat()}_{dto.isoformat()}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "X-GL-Balanced": str(balanced).lower(),
                 "X-GL-Total-Debit": f"{total_debit:.2f}",
                 "X-GL-Total-Credit": f"{total_credit:.2f}"},
    )
