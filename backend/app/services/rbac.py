"""
RBAC business helpers: approval-threshold resolution, escalation ladder and
maker-checker gating. Kept separate from the FastAPI dependency layer so the
rules are unit-testable and reusable across routers.
"""
from app.models import ApprovalThreshold

# Escalation ladder for loan approvals when the amount exceeds an actor's limit.
ESCALATION_LADDER = {
    "relationship_officer": "branch",
    "loan_officer": "branch",
    "branch_manager": "region",
    "regional_manager": "hq",
}


def _thresholds(db, tenant_id, threshold_type):
    return db.query(ApprovalThreshold).filter(
        ApprovalThreshold.tenant_id == tenant_id,
        ApprovalThreshold.threshold_type == threshold_type,
    ).all()


def loan_approval_limit(db, tenant_id, user) -> float | None:
    """The maximum loan amount `user` may approve. None == unlimited.

    Resolution order: branch override (branch_manager) / region override
    (regional_manager) if present, else the role-scoped threshold.
    """
    rows = _thresholds(db, tenant_id, "loan_approval")
    by_role, by_branch, by_region = {}, {}, {}
    for r in rows:
        if r.scope_type == "role":
            by_role[r.scope_key] = float(r.amount)
        elif r.scope_type == "branch":
            by_branch[str(r.scope_key)] = float(r.amount)
        elif r.scope_type == "region":
            by_region[str(r.scope_key)] = float(r.amount)

    if user.role == "branch_manager" and user.branch_id and str(user.branch_id) in by_branch:
        return by_branch[str(user.branch_id)]
    if user.role == "regional_manager" and user.region_id and str(user.region_id) in by_region:
        return by_region[str(user.region_id)]
    return by_role.get(user.role)


def money_threshold(db, tenant_id, threshold_type) -> float | None:
    """Amount above which disbursement/refund needs a second approver.

    Uses the tenant-level 'role'=all scoped row (scope_key 'all'); None == no
    maker-checker required.
    """
    rows = _thresholds(db, tenant_id, threshold_type)
    amounts = [float(r.amount) for r in rows if r.scope_key in ("all", "tenant")]
    if amounts:
        return min(amounts)
    # fall back to any configured row
    return min((float(r.amount) for r in rows), default=None)


def next_escalation_level(role: str) -> str | None:
    return ESCALATION_LADDER.get(role)


def requires_maker_checker(db, tenant_id, threshold_type, amount: float) -> bool:
    limit = money_threshold(db, tenant_id, threshold_type)
    return limit is not None and float(amount) > limit
