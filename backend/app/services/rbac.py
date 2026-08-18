"""
RBAC business helpers: approval-threshold resolution, escalation ladder and
maker-checker gating. Kept separate from the FastAPI dependency layer so the
rules are unit-testable and reusable across routers.
"""
from app.models import ApprovalThreshold, ApproverSetting
from app.core.permissions import APPROVAL_TYPE_PERMISSION, has_permission

# Escalation ladder for loan approvals when the amount exceeds an actor's limit.
ESCALATION_LADDER = {
    "relationship_officer": "branch",
    "loan_officer": "branch",
    "branch_manager": "region",
    "regional_manager": "hq",
}

# Ordered loan-approval tiers, lowest → highest. Each tier maps a role to the
# escalation-level label stored on the loan. Used to compute a DCP-aware ladder
# that skips tiers whose role is disabled as an approver for the tenant.
LOAN_TIERS = [
    ("branch_manager", "branch"),
    ("regional_manager", "region"),
    ("hq_credit_committee", "hq"),
]


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
    """Static (tenant-agnostic) ladder — kept for backward compatibility."""
    return ESCALATION_LADDER.get(role)


# ---------------------------------------------------------------------------
# Per-DCP configurable approver model
# ---------------------------------------------------------------------------
def _default_approver_enabled(approval_type: str, role: str) -> bool:
    """Backward-compatible default: whether `role` may approve `approval_type`
    absent any stored per-DCP override — i.e. it holds the underlying permission
    and is not the front-line originator."""
    if role == "relationship_officer":
        return False
    perm = APPROVAL_TYPE_PERMISSION.get(approval_type)
    if not perm:
        return False
    return has_permission(role, perm)


def approver_enabled(db, tenant_id, approval_type: str, role: str) -> bool:
    """Whether `role` is configured to act as an approver for `approval_type`
    at this DCP. Returns the stored toggle when a row exists, else the
    permission-derived default (keeping existing tenants unchanged)."""
    if role == "super_admin":
        return True
    row = (db.query(ApproverSetting)
           .filter(ApproverSetting.tenant_id == tenant_id,
                   ApproverSetting.approval_type == approval_type,
                   ApproverSetting.role == role)
           .first())
    if row is not None:
        return bool(row.enabled)
    return _default_approver_enabled(approval_type, role)


def enabled_loan_tiers(db, tenant_id) -> list[tuple[str, str]]:
    """Ordered loan-approval tiers (role, level) that are ENABLED for the tenant."""
    return [(role, level) for role, level in LOAN_TIERS
            if approver_enabled(db, tenant_id, "loan", role)]


def next_escalation_level_for_tenant(db, tenant_id, role: str) -> str | None:
    """DCP-aware escalation: the next ENABLED loan tier strictly above `role`.

    Falls back to the highest enabled tier when the actor is already at/above
    the top, and to the static ladder when no tiers are enabled at all.
    """
    tiers = enabled_loan_tiers(db, tenant_id)
    if not tiers:
        return next_escalation_level(role) or "hq"
    # Position of the actor within the FULL ladder (not just enabled tiers).
    full_order = [r for r, _ in LOAN_TIERS]
    actor_idx = full_order.index(role) if role in full_order else -1
    for r, level in tiers:
        if full_order.index(r) > actor_idx:
            return level
    # Actor is at or above every enabled tier → target the highest enabled tier.
    return tiers[-1][1]


def requires_maker_checker(db, tenant_id, threshold_type, amount: float) -> bool:
    """Whether a money-movement of `amount` needs a second (checker) approver.

    FAIL CLOSED (MPESA-06): when the tenant has NO maker-checker threshold
    configured at all (`money_threshold` -> None) we require maker-checker for
    ALL money movement rather than waving it through. A missing control must
    never silently disable dual authorisation on real money. Tenants that DO
    configure a threshold keep their exact behaviour (only amounts strictly
    above the limit are parked).
    """
    limit = money_threshold(db, tenant_id, threshold_type)
    if limit is None:
        return True
    return float(amount) > limit
