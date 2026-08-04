"""
Central permission registry and role -> permission mapping for Finyl-DCP RBAC.

The platform uses a permission-driven access model. Every protected action maps
to a string permission key (grouped by domain). Each user has exactly one `role`;
the role resolves to a set of permission keys. Both the backend (`require_permission`)
and the frontend (`can()` helper) enforce the SAME catalog, which is served to the
client at login time via `/auth/me`.

super_admin is the cross-tenant platform owner and implicitly holds every
permission (handled with the WILDCARD below, never enumerated).
"""

WILDCARD = "*"

# --- Permission catalog (grouped by domain) ---------------------------------
# {key: human-readable description} — the description is surfaced in the
# Roles & Permissions matrix UI.
PERMISSIONS = {
    # User & access management
    "users.view": "View user accounts",
    "users.manage": "Create / edit / deactivate / reactivate user accounts",
    "users.lock": "Lock / unlock accounts and force password resets",
    "roles.view": "View the role \u2192 permission matrix",
    "roles.assign": "Assign and revoke roles / permissions on users",
    # Org structure
    "org.view": "View branches and regions",
    "org.manage": "Create / edit branches and regions; assign users",
    # Governance / config
    "thresholds.view": "View approval thresholds",
    "thresholds.manage": "Define approval thresholds per role / branch / region",
    "audit.view": "View the audit trail / system logs",
    "backups.manage": "Trigger backups and data-integrity checks",
    "payments.upload": "Upload payment files / reconciliation batches",
    # Clients (origination)
    "clients.view_all": "View all clients in scope (branch / region / company)",
    "clients.view_portfolio": "View only own-portfolio clients",
    "clients.create": "Create new client profiles (KYC capture)",
    "clients.edit": "Edit client details (non-locked fields)",
    "clients.edit_locked": "Edit locked primary fields (phone, National ID, DOB)",
    "clients.approve": "Approve / reject client profiles",
    "docs.upload": "Upload supporting documents",
    "docs.verify": "Request document re-verification / flag incomplete",
    # Loans
    "loans.view_all": "View all loans in scope",
    "loans.view_portfolio": "View only own-portfolio loans",
    "loans.create": "Initiate / book loan applications",
    "loans.approve": "Approve / reject loans (subject to threshold)",
    "loans.adjust": "Approve loan amount adjustments",
    "loans.reassign": "Reassign clients / loans between officers",
    "loans.escalate": "Escalate loans above own threshold",
    "loans.writeoff": "Approve / recommend write-offs & restructuring",
    # Money movement (maker-checker)
    "disburse.execute": "Disburse approved loans (Daraja B2C)",
    "disburse.approve": "Approve a pending disbursement (checker)",
    "refund.execute": "Process refunds",
    "refund.approve": "Approve a pending refund (checker)",
    "reconcile.execute": "Reconcile incoming repayments to loan accounts",
    # Dashboards / reporting
    "dashboard.company": "View company-wide dashboards (all branches/regions)",
    "dashboard.region": "View regional dashboards (branches in own region)",
    "dashboard.branch": "View own-branch dashboard",
    "dashboard.portfolio": "View own-portfolio dashboard",
    "reports.export": "Download / export reports",
    "reports.schedule": "Schedule recurring report generation",
    "reports.template": "Build / save custom report templates",
    "reports.flag": "Flag data anomalies for Compliance",
}

# Convenience groupings reused in role definitions.
_VIEW_COMPANY = {"clients.view_all", "loans.view_all", "dashboard.company"}

# --- Role -> permission sets ------------------------------------------------
ROLE_PERMISSIONS = {
    # Cross-tenant platform owner: everything.
    "super_admin": {WILDCARD},

    # System Administrator — owns configuration & user/access management,
    # NOT loan decisioning or money movement.
    "system_admin": {
        "users.view", "users.manage", "users.lock",
        "roles.view", "roles.assign",
        "org.view", "org.manage",
        "thresholds.view", "thresholds.manage",
        "audit.view", "backups.manage", "payments.upload",
        "dashboard.company",  # read-only situational awareness
    },

    # Relationship Officer — front-line origination within own portfolio.
    "relationship_officer": {
        "clients.view_portfolio", "clients.create", "clients.edit",
        "docs.upload", "docs.verify",
        "loans.view_portfolio", "loans.create",
        "dashboard.portfolio",
    },

    # Branch Manager — oversight & approval for a single branch.
    "branch_manager": {
        "clients.view_all", "clients.edit", "clients.edit_locked", "clients.approve",
        "docs.upload", "docs.verify",
        "loans.view_all", "loans.approve", "loans.adjust", "loans.reassign",
        "loans.escalate", "loans.writeoff",
        "disburse.approve", "refund.approve",
        "org.view", "thresholds.view",
        "dashboard.branch",
    },

    # Regional Manager — oversight across branches in a region.
    "regional_manager": {
        "clients.view_all",
        "loans.view_all", "loans.approve", "loans.escalate", "loans.reassign",
        "loans.writeoff", "loans.adjust",
        "disburse.approve", "refund.approve",
        "org.view", "thresholds.view",
        "dashboard.region",
    },

    # Disbursement Officer — company read + disburse approved loans (maker).
    "disbursement_officer": {
        "clients.view_all", "loans.view_all", "dashboard.company",
        "disburse.execute", "disburse.approve",
    },

    # Finance / Reconciliation Officer — company read + reconcile + refunds.
    "reconciliation_officer": {
        "clients.view_all", "loans.view_all", "dashboard.company",
        "reconcile.execute", "refund.execute", "refund.approve",
        "payments.upload",
    },

    # HQ Operations — central reporting/monitoring, strictly READ-ONLY.
    "hq_operations": {
        "clients.view_all", "loans.view_all", "dashboard.company",
        "reports.export", "reports.schedule", "reports.template", "reports.flag",
        "audit.view",
    },
}

# --- Backward-compatible legacy roles ---------------------------------------
# tenant_admin keeps a broad superset so seeded data / existing demo logins keep
# full function (everything except platform ownership).
ROLE_PERMISSIONS["tenant_admin"] = (
    set(PERMISSIONS.keys())
)
# The old loan_officer maps onto the new relationship_officer rights.
ROLE_PERMISSIONS["loan_officer"] = set(ROLE_PERMISSIONS["relationship_officer"])
# call_agent works out of the call_center / complaints modules (module-flag gated);
# give it portfolio-level read so it can look up clients it is calling.
ROLE_PERMISSIONS["call_agent"] = {
    "clients.view_portfolio", "loans.view_portfolio", "dashboard.portfolio",
}

# Roles that see company-wide data (no branch/region/portfolio narrowing).
COMPANY_SCOPE_ROLES = {
    "super_admin", "tenant_admin", "system_admin",
    "disbursement_officer", "reconciliation_officer", "hq_operations",
}

# Human-friendly labels for the UI.
ROLE_LABELS = {
    "super_admin": "Super Admin",
    "tenant_admin": "Tenant Admin",
    "system_admin": "System Administrator",
    "relationship_officer": "Relationship Officer",
    "branch_manager": "Branch Manager",
    "regional_manager": "Regional Manager",
    "disbursement_officer": "Disbursement Officer",
    "reconciliation_officer": "Finance / Reconciliation Officer",
    "hq_operations": "HQ Operations",
    "loan_officer": "Loan Officer (legacy)",
    "call_agent": "Call Agent",
}

# Roles that a System Administrator may assign to users within a tenant
# (super_admin is platform-managed and never assigned via the UI).
ASSIGNABLE_ROLES = [
    "system_admin", "relationship_officer", "branch_manager", "regional_manager",
    "disbursement_officer", "reconciliation_officer", "hq_operations",
    "tenant_admin", "loan_officer", "call_agent",
]


def permissions_for(role: str) -> set[str]:
    """Resolve a role to its concrete permission-key set (wildcard expands to all)."""
    perms = ROLE_PERMISSIONS.get(role, set())
    if WILDCARD in perms:
        return set(PERMISSIONS.keys())
    return set(perms)


def has_permission(role: str, key: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return WILDCARD in perms or key in perms


def role_matrix() -> list[dict]:
    """Full role x permission matrix for the reference UI."""
    out = []
    for role, label in ROLE_LABELS.items():
        out.append({
            "role": role,
            "label": label,
            "wildcard": WILDCARD in ROLE_PERMISSIONS.get(role, set()),
            "permissions": sorted(permissions_for(role)),
        })
    return out
