"""Collection-efficiency aggregation across officers (Phase 2 scheduler helper)."""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import PromiseToPay, CollectionEfficiency
from app.services import ptp as ptp_service


def compute_all_officers(db: Session, *, tenant_id: int, period_month: date) -> list[dict]:
    """Recompute efficiency for every officer who logged PTPs in the month."""
    start = period_month.replace(day=1)
    nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12
           else start.replace(month=start.month + 1))
    officer_ids = [r[0] for r in (db.query(PromiseToPay.logged_by)
                                  .filter(PromiseToPay.tenant_id == tenant_id,
                                          PromiseToPay.ptp_date >= start,
                                          PromiseToPay.ptp_date < nxt)
                                  .distinct().all())]
    out = []
    for oid in officer_ids:
        row = ptp_service.compute_efficiency(db, tenant_id=tenant_id,
                                             officer_user_id=oid, period_month=start)
        out.append({"officer_user_id": oid, "efficiency_pct": float(row.efficiency_pct or 0),
                    "ptps_logged": row.ptps_logged, "amount_collected": float(row.amount_collected or 0)})
    return out


def leaderboard(db: Session, *, tenant_id: int, period_month: date | None = None) -> list[dict]:
    q = db.query(CollectionEfficiency).filter(CollectionEfficiency.tenant_id == tenant_id)
    if period_month:
        q = q.filter(CollectionEfficiency.period_month == period_month.replace(day=1))
    rows = q.order_by(CollectionEfficiency.efficiency_pct.desc()).all()
    return [{"officer_user_id": r.officer_user_id, "period_month": r.period_month,
             "efficiency_pct": float(r.efficiency_pct or 0), "ptps_logged": r.ptps_logged,
             "ptps_honored": r.ptps_honored, "ptps_broken": r.ptps_broken,
             "amount_promised": float(r.amount_promised or 0),
             "amount_collected": float(r.amount_collected or 0)} for r in rows]
