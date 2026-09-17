"""Executive dashboard endpoints: KPIs, trends, product×region matrix, staff performance."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_module, get_scope, UserScope
from app.models import EclProvisionConfig
from app.services import analytics

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

FILTER_KEYS = ("region_id", "branch_id", "product_id", "staff_id", "date_from", "date_to")


def _filters(request: Request) -> dict:
    return {k: request.query_params.get(k) for k in FILTER_KEYS if request.query_params.get(k)}


def _scoped_filters(request: Request, scope: UserScope) -> dict:
    """Merge the request's filter bar with a MANDATORY data-scope narrowing derived
    from the caller's role. Company-scope roles (and super_admin viewing any tenant)
    see everything; branch/regional managers are pinned to their branch/region; a
    relationship officer only ever sees their own portfolio. Scope always wins over
    a user-supplied filter — an officer cannot widen past their portfolio."""
    f = _filters(request)
    if scope.company_wide:
        return f
    if scope.role == "regional_manager" and scope.region_id:
        f["region_id"] = scope.region_id
    elif scope.role == "branch_manager" and scope.branch_id:
        f["branch_id"] = scope.branch_id
    elif scope.staff_id:  # relationship_officer / loan_officer / call_agent
        f["staff_id"] = scope.staff_id
    return f


def _ecl_config(db: Session, tenant_id: int) -> dict:
    """Resolve the tenant's IFRS 9 ECL stage rates, falling back to defaults."""
    row = (db.query(EclProvisionConfig)
           .filter(EclProvisionConfig.tenant_id == tenant_id).first())
    if not row:
        return dict(analytics.ECL_DEFAULTS)
    return {"stage1_rate": float(row.stage1_rate),
            "stage2_rate": float(row.stage2_rate),
            "stage3_rate": float(row.stage3_rate)}


@router.get("/overview")
def overview(request: Request, tenant_id: int = Depends(require_module("dashboard")),
             scope: UserScope = Depends(get_scope), db: Session = Depends(get_db)):
    """Single round-trip payload: KPI cards + charts + matrix + staff table.

    ADDITIVE (Phase 2): the filter bar is now merged with a MANDATORY data-scope
    narrowing (`_scoped_filters`). A relationship officer sees only their portfolio,
    a branch/regional manager only their branch/region; company-scope roles (and a
    super-admin viewing a tenant) still see the full book. Scope always wins over a
    user-supplied filter, so a scoped user cannot widen past their allowed data."""
    f = _scoped_filters(request, scope)
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
        # ADDITIVE (IFRS 9): expected-credit-loss provisioning over the open book.
        "ecl": analytics.ecl_provisioning(floans, _ecl_config(db, tenant_id)),
    }


@router.get("/executive")
def executive(request: Request, tenant_id: int = Depends(require_module("dashboard")),
              scope: UserScope = Depends(get_scope), db: Session = Depends(get_db)):
    """Executive dashboard surface (plan §Frontend line 449): headline KPIs, officer
    rankings, product performance and the CRM lead-conversion rate — all narrowed to
    the caller's data scope. Branch executives get a branch-pinned view; company-scope
    roles get the whole tenant. Super-admins are view-only by construction here (this
    is a read endpoint that performs no mutations)."""
    f = _scoped_filters(request, scope)
    loans = analytics.load_loans_df(db, tenant_id)
    reps = analytics.load_repayments_df(db, tenant_id)
    floans = analytics.apply_filters(loans, f)
    freps = reps[reps["loan_id"].isin(floans["id"])]
    return analytics.executive_surface(db, tenant_id, floans, freps, f)
