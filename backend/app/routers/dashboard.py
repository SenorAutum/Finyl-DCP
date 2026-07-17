"""Executive dashboard endpoints: KPIs, trends, product×region matrix, staff performance."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_module
from app.services import analytics

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

FILTER_KEYS = ("region_id", "branch_id", "product_id", "staff_id", "date_from", "date_to")


def _filters(request: Request) -> dict:
    return {k: request.query_params.get(k) for k in FILTER_KEYS if request.query_params.get(k)}


@router.get("/overview")
def overview(request: Request, tenant_id: int = Depends(require_module("dashboard")),
             db: Session = Depends(get_db)):
    """Single round-trip payload: KPI cards + charts + matrix + staff table."""
    f = _filters(request)
    loans = analytics.load_loans_df(db, tenant_id)
    reps = analytics.load_repayments_df(db, tenant_id)
    floans = analytics.apply_filters(loans, f)
    freps = reps[reps["loan_id"].isin(floans["id"])]
    return {
        "kpis": analytics.portfolio_kpis(floans, freps),
        "trend": analytics.monthly_trend(floans, freps),
        "status_mix": analytics.status_mix(floans),
        "product_region_matrix": analytics.product_region_matrix(floans),
        "staff_performance": analytics.staff_performance(db, tenant_id, floans, freps),
    }
