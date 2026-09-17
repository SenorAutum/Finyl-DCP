"""Promise-to-pay lifecycle + collection-efficiency computation (Phase 2)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import PromiseToPay, CollectionEfficiency, Loan


def create(db: Session, *, tenant_id: int, loan_id: int, logged_by: int,
           ptp_date: date, amount: Decimal, contact_method: str = "call",
           notes: str | None = None) -> PromiseToPay:
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == tenant_id).first()
    if not loan:
        raise ValueError("loan_not_found")
    ptp = PromiseToPay(
        tenant_id=tenant_id, loan_id=loan_id, client_id=loan.borrower_id,
        logged_by=logged_by, ptp_date=ptp_date, amount=amount,
        contact_method=contact_method, status="pending", notes=notes,
    )
    db.add(ptp)
    db.commit()
    return ptp


def update_status(db: Session, *, tenant_id: int, ptp_id: int, status: str | None = None,
                  honored_amount: Decimal | None = None, ptp_date: date | None = None,
                  notes: str | None = None) -> PromiseToPay:
    ptp = (db.query(PromiseToPay)
           .filter(PromiseToPay.id == ptp_id, PromiseToPay.tenant_id == tenant_id).first())
    if not ptp:
        raise ValueError("ptp_not_found")
    now = datetime.now(timezone.utc)
    if notes is not None:
        ptp.notes = notes
    if ptp_date is not None:
        ptp.ptp_date = ptp_date
    if status:
        ptp.status = status
        if status in ("honored", "partial"):
            ptp.honored_at = now
            if honored_amount is not None:
                ptp.honored_amount = honored_amount
        elif status == "broken":
            ptp.broken_at = now
    db.commit()
    return ptp


def _month_start(d: date) -> date:
    return d.replace(day=1)


def compute_efficiency(db: Session, *, tenant_id: int, officer_user_id: int,
                       period_month: date) -> CollectionEfficiency:
    """Aggregate an officer's PTP outcomes for a month and upsert the row."""
    start = _month_start(period_month)
    # next month
    nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12
           else start.replace(month=start.month + 1))
    rows = (db.query(PromiseToPay)
            .filter(PromiseToPay.tenant_id == tenant_id,
                    PromiseToPay.logged_by == officer_user_id,
                    PromiseToPay.ptp_date >= start,
                    PromiseToPay.ptp_date < nxt).all())
    logged = len(rows)
    honored = sum(1 for r in rows if r.status == "honored")
    broken = sum(1 for r in rows if r.status == "broken")
    partial = sum(1 for r in rows if r.status == "partial")
    promised = sum((Decimal(str(r.amount or 0)) for r in rows), Decimal("0"))
    collected = sum((Decimal(str(r.honored_amount or 0)) for r in rows), Decimal("0"))
    calls = sum(1 for r in rows if r.contact_method == "call")
    visits = sum(1 for r in rows if r.contact_method == "site_visit")
    eff = (collected / promised * 100).quantize(Decimal("0.01")) if promised else Decimal("0")

    row = (db.query(CollectionEfficiency)
           .filter(CollectionEfficiency.tenant_id == tenant_id,
                   CollectionEfficiency.officer_user_id == officer_user_id,
                   CollectionEfficiency.period_month == start).first())
    if not row:
        row = CollectionEfficiency(tenant_id=tenant_id, officer_user_id=officer_user_id,
                                   period_month=start)
        db.add(row)
    row.ptps_logged = logged
    row.ptps_honored = honored
    row.ptps_broken = broken
    row.ptps_partial = partial
    row.amount_promised = promised
    row.amount_collected = collected
    row.efficiency_pct = eff
    row.calls_made = calls
    row.visits_made = visits
    row.computed_at = datetime.now(timezone.utc)
    db.commit()
    return row
