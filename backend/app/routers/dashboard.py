"""Executive dashboard endpoints: KPIs, trends, product×region matrix, staff performance."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_module, get_scope, UserScope
from app.models import Borrower, EclProvisionConfig, Loan
from app.services import analytics


# Bucket a raw loan status into the five business categories the RO cares about.
def _loan_bucket(status: str) -> str:
    if status in ("pending", "underwriting", "processing"):
        return "pending"
    if status in ("approved",):
        return "approved"
    if status == "rejected":
        return "rejected"
    if status == "active":
        return "performing"
    if status in ("overdue", "defaulted"):
        return "non_performing"
    if status == "paid":
        return "closed"
    return "other"

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




@router.get("/my-portfolio")
def my_portfolio(tenant_id: int = Depends(require_module("dashboard")),
                 scope: UserScope = Depends(get_scope), db: Session = Depends(get_db)):
    """Relationship-officer home screen (plan §Frontend): the officer's OWN clients
    and the status of their loan book — never the company-wide executive view.

    Returns:
      * ``clients``          — the officer's clients with onboarding + KYC status
      * ``onboarding``       — counts of onboarded vs pending vs rejected profiles
      * ``loan_status``      — counts by business bucket: approved / rejected /
                               pending / performing / non_performing (+ closed)
      * ``kpis``             — headline numbers for the officer's book

    Data is pinned to ``scope.staff_id`` for a relationship/loan officer; company-
    scope roles (and a super-admin viewing a tenant) see the whole tenant so the
    same screen is useful to a manager reviewing a team.
    """
    staff_id = None if scope.company_wide else scope.staff_id

    # ---- Clients owned by this officer -------------------------------------
    cq = db.query(Borrower).filter(Borrower.tenant_id == tenant_id)
    if staff_id:
        cq = cq.filter(Borrower.officer_staff_id == staff_id)
    clients = cq.order_by(Borrower.created_at.desc()).all()

    onboarding = {"onboarded": 0, "pending": 0, "rejected": 0}
    client_rows = []
    client_ids = []
    for c in clients:
        client_ids.append(c.id)
        ps = (c.profile_status or "approved").lower()
        if ps == "approved":
            onboarding["onboarded"] += 1
        elif ps == "rejected":
            onboarding["rejected"] += 1
        else:
            onboarding["pending"] += 1
        client_rows.append({
            "id": c.id,
            "name": f"{c.first_name} {c.last_name}".strip(),
            "phone": c.phone,
            "profile_status": ps,
            "kyc_status": c.kyc_status or "draft",
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })

    # ---- Loan book for this officer ----------------------------------------
    lq = db.query(Loan).filter(Loan.tenant_id == tenant_id)
    if staff_id:
        lq = lq.filter(Loan.staff_id == staff_id)
    loans = lq.all()

    loan_status = {"approved": 0, "rejected": 0, "pending": 0,
                   "performing": 0, "non_performing": 0, "closed": 0, "other": 0}
    outstanding = 0.0
    # Map loan → owning client name for the per-client table.
    loans_by_client: dict[int, dict] = {}
    for l in loans:
        bucket = _loan_bucket((l.status or "").lower())
        loan_status[bucket] = loan_status.get(bucket, 0) + 1
        if bucket in ("performing", "non_performing"):
            outstanding += float(l.outstanding_balance or 0)
        cd = loans_by_client.setdefault(l.borrower_id, {"active": 0, "overdue": 0, "total": 0})
        cd["total"] += 1
        if bucket == "performing":
            cd["active"] += 1
        elif bucket == "non_performing":
            cd["overdue"] += 1

    # Enrich the client rows with their loan snapshot.
    for row in client_rows:
        snap = loans_by_client.get(row["id"], {"active": 0, "overdue": 0, "total": 0})
        row["loans_total"] = snap["total"]
        row["loans_active"] = snap["active"]
        row["loans_overdue"] = snap["overdue"]

    total_loans = len(loans)
    kpis = {
        "clients_total": len(clients),
        "clients_onboarded": onboarding["onboarded"],
        "clients_pending": onboarding["pending"],
        "loans_total": total_loans,
        "loans_performing": loan_status["performing"],
        "loans_non_performing": loan_status["non_performing"],
        "portfolio_at_risk_pct": round(
            100.0 * loan_status["non_performing"]
            / max(loan_status["performing"] + loan_status["non_performing"], 1), 1),
        "outstanding_balance": round(outstanding, 2),
    }

    return {
        "scope": "company" if scope.company_wide else "portfolio",
        "kpis": kpis,
        "onboarding": onboarding,
        "loan_status": loan_status,
        "clients": client_rows,
    }
