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
    "payments.bank_import": "Import bank statements (money in is matched to loans; the rest goes to suspense)",
    "messaging.manage": "Customise per-DCP SMS message templates",
    "sms.send": "Send a one-off SMS to a client from the DCP's sender name",
    "sms.view": "View the SMS log (codes are never shown)",
    "products.manage": "Propose new loan products and product changes (go live after Finyl approval)",
    # Clients (origination)
    "clients.view_all": "View all clients in scope (branch / region / company)",
    "clients.view_portfolio": "View only own-portfolio clients",
    "clients.create": "Create new client profiles (KYC capture)",
    "clients.edit": "Edit client details (non-locked fields)",
    "clients.edit_locked": "Edit locked primary fields (phone, National ID, DOB)",
    "clients.approve": "Approve / reject client profiles",
    "wallets.unlock": "Unlock a client's confirmed M-Pesa wallet so it can be changed (with a reason)",
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
    "loans.transition": "Manually move a loan between post-disbursement states (overdue / defaulted / paid-when-zero-balance)",
    # Money movement (maker-checker)
    "disburse.execute": "Disburse approved loans (Daraja B2C)",
    "disburse.approve": "Approve a pending disbursement (checker)",
    "refund.execute": "Process refunds",
    "refund.approve": "Approve a pending refund (checker)",
    "reconcile.execute": "Reconcile incoming repayments to loan accounts",
    "reconcile.approve": "Approve a manual payment or suspense allocation (checker)",
    "collections.stk_push": "Trigger an STK-push collections prompt to a borrower",
    # Dashboards / reporting
    "dashboard.company": "View company-wide dashboards (all branches/regions)",
    "dashboard.region": "View regional dashboards (branches in own region)",
    "dashboard.branch": "View own-branch dashboard",
    "dashboard.portfolio": "View own-portfolio dashboard",
    "reports.export": "Download / export reports",
    "reports.schedule": "Schedule recurring report generation",
    "reports.template": "Build / save custom report templates",
    "reports.flag": "Flag data anomalies for Compliance",
    # Accounting
    "accounting.export": "Export the general ledger (double-entry) & view the Chart of Accounts",
    # WP14 — the books (ERP accounting)
    "accounting.view": "View the books: journals, ledgers, financial statements, payables, assets, budgets",
    "accounting.post": "Enter journals, supplier bills and payments; import statements; register assets",
    "accounting.approve": "Approve journals, bills, payments and expense claims entered by someone else",
    "accounting.close": "Run depreciation, loan-loss provision and month-end close; lock and reopen periods",
    "accounting.settings": "Chart of accounts, posting accounts, approval limits, suppliers' defaults, budgets",
    "expenses.submit": "Submit your own expense claims",
    # WP15 — human resources and payroll
    "hr.view": "View employee records, leave, attendance and payroll",
    "hr.manage": "Add and edit employees, pay items, documents, attendance and leave types",
    "leave.approve": "Approve or reject anyone's leave (managers can always decide for their own team)",
    "payroll.run": "Prepare payroll, produce payslips, salary files and statutory returns",
    "payroll.approve": "Approve payroll prepared by someone else (posts it to the books) and record payments",
    "appraisals.manage": "Write and share performance appraisals",
    "hr.self": "See your own payslips, leave and appraisals; apply for leave",
    # WP16 — lending additions
    "loans.waive": "Ask for and approve waivers of interest or fees (a second person approves)",
    "loans.topup": "Start a top-up (a new loan that first settles the client's current loan)",
    "collateral.manage": "Record and release collateral (logbooks, title deeds, equipment, savings)",
    "groups.manage": "Create lending groups, add members and record meetings",
    # WP17 — CRM
    "crm.view": "See client timelines, follow-up tasks, segments and the sales pipeline report",
    "crm.log": "Log calls, visits and other contact with clients and leads; create follow-up tasks",
    "crm.campaigns": "Build client segments and draft SMS campaigns",
    "crm.campaigns_approve": "Approve SMS campaigns drafted by someone else (sends them)",
    # --- Phase 2 additive permissions ---------------------------------------
    # Guarantors
    "guarantors.manage": "Create / edit / validate guarantors and their documents",
    # KYC escalations & validation
    "kyc.escalate": "Log a KYC mismatch escalation",
    "kyc.resolve": "Resolve / override a KYC mismatch escalation",
    # Field operations
    "field_ops.gps_view": "View officer GPS trails and location alerts",
    "field_ops.tasks_manage": "Plan and manage officer daily field tasks",
    # Activity monitoring
    "activity.view": "View staff activity logs and screenshots",
    # Collections
    "collections.ptp_manage": "Create and manage promises-to-pay",
    "collections.efficiency_view": "View collection-efficiency analytics",
    # Third-party API clients
    "api_clients.manage": "Create / revoke third-party API ingestion clients",
    # Client edit maker-checker
    "client_edits.request": "Submit a locked-field client edit request",
    "client_edits.approve_secondary": "First-level approval of a client edit request",
    "client_edits.approve_primary": "Final approval of a client edit request",
    # Security config
    "security.manage": "Manage tenant security config (OTP, device, geo/time fences)",
    # Global search
    "search.global": "Use global cross-entity search",
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
        "sms.view",
        "users.view", "users.manage", "users.lock",
        "roles.view", "roles.assign",
        "org.view", "org.manage",
        "thresholds.view", "thresholds.manage",
        "audit.view", "backups.manage", "payments.upload",
        "messaging.manage", "products.manage",
        "accounting.export",
        "dashboard.company",  # read-only situational awareness
        # Phase 2 — configuration & platform-admin surfaces
        "api_clients.manage", "security.manage", "activity.view",
        "field_ops.gps_view", "field_ops.tasks_manage",
        "search.global",
    },

    # Relationship Officer — front-line origination within own portfolio.
    "relationship_officer": {
        "clients.view_portfolio", "clients.create", "clients.edit",
        "docs.upload", "docs.verify",
        "loans.view_portfolio", "loans.create",
        "collections.stk_push",
        "dashboard.portfolio",
        # Phase 2 — front-line field & origination surfaces
        "guarantors.manage", "kyc.escalate",
        "collections.ptp_manage",
        "client_edits.request",
        "search.global",
    },

    # Branch Manager — oversight & approval for a single branch.
    "branch_manager": {
        "sms.send", "sms.view",
        "clients.view_all", "clients.edit", "clients.edit_locked", "clients.approve",
        "wallets.unlock",
        "docs.upload", "docs.verify",
        "loans.view_all", "loans.approve", "loans.adjust", "loans.reassign",
        "loans.escalate", "loans.transition", "loans.writeoff",
        "disburse.approve", "refund.approve", "reconcile.approve",
        "collections.stk_push",
        "org.view", "thresholds.view",
        "dashboard.branch",
        # Phase 2 — branch oversight surfaces
        "guarantors.manage", "kyc.escalate", "kyc.resolve",
        "field_ops.gps_view", "field_ops.tasks_manage",
        "activity.view",
        "collections.ptp_manage", "collections.efficiency_view",
        "client_edits.request", "client_edits.approve_secondary",
        "client_edits.approve_primary",
        "search.global",
    },

    # Regional Manager — oversight across branches in a region.
    "regional_manager": {
        "sms.view",
        "clients.view_all",
        "loans.view_all", "loans.approve", "loans.escalate", "loans.reassign",
        "loans.transition", "loans.writeoff", "loans.adjust",
        "disburse.approve", "refund.approve", "reconcile.approve",
        "collections.stk_push",
        "org.view", "thresholds.view",
        "dashboard.region",
        # Phase 2 — regional oversight surfaces
        "field_ops.gps_view", "field_ops.tasks_manage",
        "activity.view",
        "collections.efficiency_view",
        "kyc.resolve",
        "client_edits.approve_primary",
        "search.global",
    },

    # Disbursement Officer — company read + disburse approved loans (maker).
    "disbursement_officer": {
        "clients.view_all", "loans.view_all", "dashboard.company",
        "disburse.execute", "disburse.approve",
    },

    # Finance / Reconciliation Officer — company read + reconcile + refunds.
    "reconciliation_officer": {
        "sms.send", "sms.view",
        "clients.view_all", "loans.view_all", "dashboard.company",
        "reconcile.execute", "refund.execute", "refund.approve", "reconcile.approve",
        "collections.stk_push",
        "payments.upload", "payments.bank_import",
        "accounting.export",
    },

    # HQ Operations — central reporting/monitoring, strictly READ-ONLY.
    "hq_operations": {
        "sms.view",
        "clients.view_all", "loans.view_all", "dashboard.company",
        "reports.export", "reports.schedule", "reports.template", "reports.flag",
        "accounting.export",
        "audit.view",
        # Phase 2 — central monitoring surfaces (read-only)
        "activity.view", "field_ops.gps_view",
        "collections.efficiency_view",
        "search.global",
    },

    # HQ / Credit Committee — the TOP tier of the loan-approval escalation ladder.
    # A distinct approver role (NOT the read-only hq_operations). Its actual
    # ability to approve any given action is still gated by the per-DCP
    # approver-configuration toggle (see app.services.rbac.approver_enabled).
    "hq_credit_committee": {
        "clients.view_all", "loans.view_all", "dashboard.company",
        "clients.approve",
        "loans.approve", "loans.escalate", "loans.adjust", "loans.transition", "loans.writeoff",
        "disburse.approve", "refund.approve", "reconcile.approve",
    },

    # Director — senior executive with company-wide read visibility, approval
    # authority, executive reporting and oversight/compliance access. NOT a
    # day-to-day operational role (no origination / money-movement execution).
    "director": {
        # Org-wide read visibility
        "clients.view_all", "loans.view_all",
        # Approval authority
        "clients.approve",
        "loans.approve", "loans.adjust", "loans.transition", "loans.writeoff", "loans.escalate",
        "disburse.approve", "refund.approve", "reconcile.approve",
        # Executive reporting
        "dashboard.company",
        "reports.export", "reports.schedule", "reports.template", "reports.flag",
        "accounting.export",
        # Oversight & compliance
        "audit.view", "activity.view",
        "org.view", "thresholds.view",
        "kyc.resolve",
        "collections.efficiency_view",
        "search.global",
    },
}

# WP14 — finance roles for the books.
ROLE_PERMISSIONS["accountant"] = {
    "accounting.view", "accounting.post", "accounting.export", "accounting.close",
    "reports.export", "dashboard.company", "loans.view_all", "clients.view_all",
    "payments.bank_import", "search.global",
}
ROLE_PERMISSIONS["finance_manager"] = {
    "accounting.view", "accounting.post", "accounting.approve", "accounting.close",
    "accounting.settings", "accounting.export",
    "reports.export", "reports.schedule", "dashboard.company", "loans.view_all", "clients.view_all",
    "reconcile.approve", "refund.approve", "audit.view", "search.global",
}
for _role, _extra in {
    "system_admin": {"accounting.view", "accounting.settings"},
    "reconciliation_officer": {"accounting.view", "accounting.post"},
    "hq_operations": {"accounting.view"},
    "director": {"accounting.view", "accounting.approve", "accounting.close"},
}.items():
    ROLE_PERMISSIONS[_role] |= _extra
# WP16 — lending additions.
for _role, _extra in {
    "relationship_officer": {"collateral.manage", "groups.manage", "loans.topup"},
    "branch_manager": {"loans.waive", "collateral.manage", "groups.manage", "loans.topup"},
    "regional_manager": {"loans.waive", "collateral.manage", "groups.manage"},
    "hq_credit_committee": {"loans.waive"},
    "director": {"loans.waive"},
}.items():
    ROLE_PERMISSIONS[_role] |= _extra

# WP17 — CRM.
for _role, _extra in {
    "relationship_officer": {"crm.view", "crm.log"},
    "branch_manager": {"crm.view", "crm.log", "crm.campaigns", "crm.campaigns_approve"},
    "regional_manager": {"crm.view", "crm.log", "crm.campaigns_approve"},
    "hq_operations": {"crm.view"},
    "director": {"crm.view", "crm.campaigns_approve"},
    "system_admin": {"crm.view"},
}.items():
    ROLE_PERMISSIONS[_role] |= _extra

# WP15 — HR roles.
ROLE_PERMISSIONS["hr_manager"] = {
    "hr.view", "hr.manage", "leave.approve", "payroll.run", "appraisals.manage",
    "reports.export", "search.global",
}
for _role, _extra in {
    "finance_manager": {"hr.view", "payroll.approve"},
    "director": {"hr.view", "payroll.approve", "leave.approve", "appraisals.manage"},
}.items():
    ROLE_PERMISSIONS[_role] |= _extra
# Every member of staff can claim their own expenses and see their own HR record.
for _role, _perms in ROLE_PERMISSIONS.items():
    if WILDCARD not in _perms:
        _perms.update({"expenses.submit", "hr.self"})

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
    "clients.view_portfolio", "loans.view_portfolio", "dashboard.portfolio", "crm.view", "crm.log",
    "expenses.submit", "hr.self",
}

# Front-line roles whose data scope is pinned to their own staff record.
OFFICER_SCOPE_ROLES = {"relationship_officer", "loan_officer", "call_agent"}

# Roles that see company-wide data (no branch/region/portfolio narrowing).
COMPANY_SCOPE_ROLES = {
    "super_admin", "tenant_admin", "system_admin",
    "disbursement_officer", "reconciliation_officer", "hq_operations",
    "hq_credit_committee", "director", "accountant", "finance_manager", "hr_manager",
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
    "hq_credit_committee": "HQ / Credit Committee",
    "director": "Director",
    "loan_officer": "Loan Officer (legacy)",
    "call_agent": "Call Agent",
    "platform_user": "Finyl staff / partner",
    "accountant": "Accountant",
    "finance_manager": "Finance Manager",
    "hr_manager": "HR Manager",
}

# Roles that a System Administrator may assign to users within a tenant
# (super_admin is platform-managed and never assigned via the UI).
ASSIGNABLE_ROLES = [
    "system_admin", "relationship_officer", "branch_manager", "regional_manager",
    "disbursement_officer", "reconciliation_officer", "hq_operations",
    "hq_credit_committee", "director",
    "tenant_admin", "loan_officer", "call_agent",
    "accountant", "finance_manager", "hr_manager",
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


# --- Per-DCP configurable approver model ------------------------------------
# The four approval action types and the underlying permission that grants each.
APPROVAL_TYPE_PERMISSION = {
    "loan": "loans.approve",
    "client": "clients.approve",
    "disbursement": "disburse.approve",
    "refund": "refund.approve",
    "manual_payment": "reconcile.approve",
}
APPROVAL_TYPES = ["loan", "client", "disbursement", "refund", "manual_payment"]
APPROVAL_TYPE_LABELS = {
    "loan": "Loan approvals",
    "client": "Client-profile approvals",
    "disbursement": "Disbursement (maker-checker)",
    "refund": "Refund (maker-checker)",
    "manual_payment": "Manual payment / suspense allocation (maker-checker)",
}

# Legacy roles are never surfaced in the approver-configuration UI.
LEGACY_ROLES = {"tenant_admin", "loan_officer", "call_agent"}


def eligible_approver_roles(approval_type: str) -> list[str]:
    """Roles that are ELIGIBLE to act as approvers for `approval_type`.

    A role is eligible when it holds the underlying approval permission and is
    neither the front-line originator (relationship_officer), the implicit
    platform owner (super_admin), nor a legacy role. This is the universe of
    roles the super admin may toggle on/off per DCP.
    """
    perm = APPROVAL_TYPE_PERMISSION[approval_type]
    out = []
    for role in ROLE_LABELS:
        if role in ("super_admin", "relationship_officer") or role in LEGACY_ROLES:
            continue
        if has_permission(role, perm):
            out.append(role)
    return out


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
