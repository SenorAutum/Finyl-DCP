"""
Editable, per-tenant RBAC resolution.

The static catalog in app/core/permissions.py defines the BASE role -> permission
map. This service layers per-tenant, DB-backed overrides on top so the Roles &
Permissions matrix is editable per DCP without code changes:

  * RolePermissionOverride rows ADD (granted=True) or REMOVE (granted=False) a
    single permission from a role, scoped to a tenant.
  * CustomRole rows define tenant-specific roles (permission set built purely
    from overrides) and/or override the display label of a built-in role.

Hard invariants (never violated here):
  * super_admin implicitly holds EVERY permission and is never persisted or
    stripped — it is resolved in code and ignored by every override path.
  * The eligible-approver universe (which excludes relationship_officer) is
    intentionally computed from the STATIC catalog in permissions.py, decoupled
    from these editable grants, so RO can never be toggled into an approver tier.
"""
from app.core.permissions import (
    PERMISSIONS, ROLE_LABELS, ROLE_PERMISSIONS, WILDCARD,
    permissions_for,
)
from app.models import CustomRole, RolePermissionOverride

# Roles whose permission set can never be edited away from full/wildcard.
PROTECTED_ROLES = {"super_admin"}
# Role keys reserved by the platform (cannot be created as custom roles).
RESERVED_ROLE_KEYS = set(ROLE_LABELS.keys())


def _overrides_for_tenant(db, tenant_id: int) -> dict[str, dict[str, bool]]:
    """{role: {permission_key: granted}} for one tenant."""
    rows = (db.query(RolePermissionOverride)
            .filter(RolePermissionOverride.tenant_id == tenant_id).all())
    out: dict[str, dict[str, bool]] = {}
    for r in rows:
        out.setdefault(r.role, {})[r.permission_key] = bool(r.granted)
    return out


def _custom_roles_for_tenant(db, tenant_id: int) -> dict[str, str]:
    """{role_key: label} of tenant custom roles / label overrides."""
    rows = (db.query(CustomRole)
            .filter(CustomRole.tenant_id == tenant_id).all())
    return {r.role_key: r.label for r in rows}


def effective_permissions(db, tenant_id: int, role: str) -> set[str]:
    """Resolve a role's effective permission-key set for one tenant.

    super_admin always resolves to the full catalog (never stripped). For every
    other role we start from the static base (or empty for a purely custom role)
    and apply the tenant's grant/revoke overrides.
    """
    if role == "super_admin":
        return set(PERMISSIONS.keys())

    if role in ROLE_PERMISSIONS:
        base = permissions_for(role)
    else:
        base = set()  # custom role — starts empty, built from overrides

    overrides = _overrides_for_tenant(db, tenant_id).get(role, {})
    for key, granted in overrides.items():
        if key not in PERMISSIONS:
            continue
        if granted:
            base.add(key)
        else:
            base.discard(key)
    return base


def list_roles_detail(db, tenant_id: int) -> list[dict]:
    """Full editable role list for a tenant: built-in roles + custom roles, each
    with its effective permissions, wildcard flag, label and custom flag."""
    custom = _custom_roles_for_tenant(db, tenant_id)
    out = []
    seen = set()
    for role, label in ROLE_LABELS.items():
        seen.add(role)
        out.append({
            "role": role,
            "label": custom.get(role, label),
            "wildcard": WILDCARD in ROLE_PERMISSIONS.get(role, set()),
            "protected": role in PROTECTED_ROLES,
            "custom": False,
            "permissions": sorted(effective_permissions(db, tenant_id, role)),
        })
    for role_key, label in custom.items():
        if role_key in seen:
            continue  # label override for a built-in — already applied above
        out.append({
            "role": role_key,
            "label": label,
            "wildcard": False,
            "protected": False,
            "custom": True,
            "permissions": sorted(effective_permissions(db, tenant_id, role_key)),
        })
    return out


def permission_catalog() -> list[dict]:
    """The permission catalog (key + description) for the matrix UI."""
    return [{"key": k, "description": v} for k, v in PERMISSIONS.items()]
