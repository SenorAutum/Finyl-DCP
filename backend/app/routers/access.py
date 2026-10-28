"""
System Administrator surface — user & access management, org structure,
approval thresholds, audit trail, bulk payment upload and backup / data-integrity
checks. Every endpoint is permission-gated (see app.core.permissions) and
tenant-scoped; privileged mutations are written to the audit trail.
"""
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (get_tenant_id, get_current_user, require_permission,
                           require_role, write_audit)
from app.core.permissions import (PERMISSIONS, ASSIGNABLE_ROLES, ROLE_LABELS,
                                   permissions_for, role_matrix,
                                   APPROVAL_TYPES, APPROVAL_TYPE_LABELS,
                                   eligible_approver_roles)
from app.core.security import hash_password
from app.core.config import settings
from app.models import (User, Region, Branch, Staff, ApprovalThreshold, AuditLog,
                        ApproverSetting, Tenant, Loan, Borrower, Repayment,
                        PaymentTransaction)
from app.schemas import (UserCreate, UserUpdate, RoleAssign, PasswordReset,
                        RegionCreate, BranchCreate, ThresholdCreate, ApproverConfigIn,
                        RoleCreate, RoleLabelUpdate, RolePermissionUpdate)
from app.models import RolePermissionOverride, CustomRole
from app.services import rbac
from app.services import authz

router = APIRouter(prefix="/api/v1/access", tags=["access"],
                   dependencies=[Depends(require_role("super_admin"))])

DEFAULT_PW = "Finyl@2026"


# ---------------------------------------------------------------------------
# Roles & permissions reference
# ---------------------------------------------------------------------------
@router.get("/permissions")
def list_permissions(user: User = Depends(require_permission("roles.view"))):
    return {
        "permissions": [{"key": k, "description": v} for k, v in PERMISSIONS.items()],
        "assignable_roles": [{"role": r, "label": ROLE_LABELS.get(r, r)} for r in ASSIGNABLE_ROLES],
        "matrix": role_matrix(),
    }


# ---------------------------------------------------------------------------
# Editable roles & permissions (super_admin only — whole router is gated)
# ---------------------------------------------------------------------------
import re as _re

_ROLE_KEY_RE = _re.compile(r"^[a-z][a-z0-9_]{2,39}$")


@router.get("/roles")
def list_roles(tenant_id: int = Depends(get_tenant_id),
               db: Session = Depends(get_db),
               _: User = Depends(require_permission("roles.view"))):
    """Full editable role x permission matrix for the resolved tenant, plus the
    permission catalog. Effective permissions layer per-tenant overrides on the
    static role map. super_admin is returned but flagged protected (never edited)."""
    return {
        "permissions": authz.permission_catalog(),
        "roles": authz.list_roles_detail(db, tenant_id),
    }


@router.post("/roles")
def create_role(body: RoleCreate,
                tenant_id: int = Depends(get_tenant_id),
                db: Session = Depends(get_db),
                user: User = Depends(require_permission("roles.assign")),
                request: Request = None):
    """Create a tenant-specific custom role (empty permission set, built up via
    the permission toggles). Reserved / built-in role keys are rejected."""
    key = (body.role_key or "").strip().lower()
    if not _ROLE_KEY_RE.match(key):
        raise HTTPException(422, "role_key must be lowercase letters/digits/underscore, 3-40 chars")
    if key in authz.RESERVED_ROLE_KEYS:
        raise HTTPException(409, "That role key is reserved by the platform")
    existing = (db.query(CustomRole)
                .filter(CustomRole.tenant_id == tenant_id, CustomRole.role_key == key).first())
    if existing:
        raise HTTPException(409, "A role with that key already exists")
    row = CustomRole(tenant_id=tenant_id, role_key=key,
                     label=(body.label or key).strip()[:120],
                     updated_by_user_id=user.id)
    db.add(row)
    write_audit(db, tenant_id=tenant_id, user=user, action="roles.create",
                entity_type="role", details={"role_key": key}, request=request)
    db.commit()
    return {"status": "created", "role": key}


@router.patch("/roles/{role_key}")
def rename_role(role_key: str, body: RoleLabelUpdate,
                tenant_id: int = Depends(get_tenant_id),
                db: Session = Depends(get_db),
                user: User = Depends(require_permission("roles.assign")),
                request: Request = None):
    """Rename a role's display label (built-in or custom). super_admin's label is
    immutable; its key can never be renamed."""
    if role_key == "super_admin":
        raise HTTPException(403, "The super_admin role cannot be modified")
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(422, "label is required")
    row = (db.query(CustomRole)
           .filter(CustomRole.tenant_id == tenant_id, CustomRole.role_key == role_key).first())
    if row:
        row.label = label[:120]
        row.updated_by_user_id = user.id
    else:
        # Built-in role: persist a label override row.
        if role_key not in authz.RESERVED_ROLE_KEYS:
            raise HTTPException(404, "Unknown role")
        db.add(CustomRole(tenant_id=tenant_id, role_key=role_key, label=label[:120],
                          updated_by_user_id=user.id))
    write_audit(db, tenant_id=tenant_id, user=user, action="roles.rename",
                entity_type="role", details={"role_key": role_key, "label": label}, request=request)
    db.commit()
    return {"status": "updated", "role": role_key, "label": label}


@router.post("/roles/{role_key}/permissions")
def set_role_permission(role_key: str, body: RolePermissionUpdate,
                        tenant_id: int = Depends(get_tenant_id),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_permission("roles.assign")),
                        request: Request = None):
    """Grant or revoke a single permission on a role for this tenant.

    Invariants: super_admin can never be altered; the permission key must exist in
    the catalog; the role must be a known built-in or an existing custom role."""
    if role_key == "super_admin":
        raise HTTPException(403, "The super_admin role holds all permissions and cannot be edited")
    if body.permission_key not in PERMISSIONS:
        raise HTTPException(422, "Unknown permission key")
    is_builtin = role_key in authz.RESERVED_ROLE_KEYS
    is_custom = (db.query(CustomRole)
                 .filter(CustomRole.tenant_id == tenant_id,
                         CustomRole.role_key == role_key).first()) is not None
    if not (is_builtin or is_custom):
        raise HTTPException(404, "Unknown role")
    row = (db.query(RolePermissionOverride)
           .filter(RolePermissionOverride.tenant_id == tenant_id,
                   RolePermissionOverride.role == role_key,
                   RolePermissionOverride.permission_key == body.permission_key).first())
    if row:
        row.granted = bool(body.granted)
        row.updated_by_user_id = user.id
    else:
        db.add(RolePermissionOverride(
            tenant_id=tenant_id, role=role_key, permission_key=body.permission_key,
            granted=bool(body.granted), updated_by_user_id=user.id))
    write_audit(db, tenant_id=tenant_id, user=user, action="roles.set_permission",
                entity_type="role",
                details={"role_key": role_key, "permission_key": body.permission_key,
                         "granted": bool(body.granted)}, request=request)
    db.commit()
    return {"status": "updated", "role": role_key,
            "permission_key": body.permission_key, "granted": bool(body.granted),
            "permissions": sorted(authz.effective_permissions(db, tenant_id, role_key))}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def _user_dict(u: User, db: Session) -> dict:
    staff = db.get(Staff, u.staff_id) if u.staff_id else None
    branch = db.get(Branch, u.branch_id) if u.branch_id else None
    region = db.get(Region, u.region_id) if u.region_id else None
    return {
        "id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role,
        "role_label": ROLE_LABELS.get(u.role, u.role),
        "active": u.active, "is_locked": bool(u.is_locked),
        "force_password_reset": bool(u.force_password_reset),
        "deactivated_at": u.deactivated_at.isoformat() if u.deactivated_at else None,
        "staff_id": u.staff_id, "staff_name": staff.name if staff else None,
        "branch_id": u.branch_id, "branch_name": branch.name if branch else None,
        "region_id": u.region_id, "region_name": region.name if region else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("/users")
def list_users(tenant_id: int = Depends(get_tenant_id),
               _: User = Depends(require_permission("users.view")),
               db: Session = Depends(get_db)):
    rows = db.query(User).filter(User.tenant_id == tenant_id).order_by(User.id).all()
    return [_user_dict(u, db) for u in rows]


@router.post("/users")
def create_user(body: UserCreate, request: Request,
                tenant_id: int = Depends(get_tenant_id),
                actor: User = Depends(require_permission("users.manage")),
                db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "A user with that email already exists")
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(400, f"Role must be one of {ASSIGNABLE_ROLES}")
    u = User(
        email=email, full_name=body.full_name, role=body.role,
        hashed_password=hash_password(body.password or DEFAULT_PW),
        tenant_id=tenant_id, staff_id=body.staff_id,
        branch_id=body.branch_id, region_id=body.region_id,
        active=body.active, force_password_reset=body.password is None,
    )
    db.add(u); db.flush()
    write_audit(db, tenant_id=tenant_id, user=actor, action="user.create",
                entity_type="user", entity_id=u.id,
                details={"email": email, "role": body.role}, request=request)
    db.commit()
    return _user_dict(u, db)


def _get_user(db, tenant_id, user_id) -> User:
    u = db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
    if not u:
        raise HTTPException(404, "User not found")
    if u.role == "super_admin":
        raise HTTPException(403, "super_admin is platform-managed")
    return u


@router.put("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request,
                tenant_id: int = Depends(get_tenant_id),
                actor: User = Depends(require_permission("users.manage")),
                db: Session = Depends(get_db)):
    u = _get_user(db, tenant_id, user_id)
    before = _user_dict(u, db)
    if body.role is not None:
        if body.role not in ASSIGNABLE_ROLES:
            raise HTTPException(400, "Invalid role")
        u.role = body.role
    for f in ("full_name", "branch_id", "region_id", "staff_id", "active"):
        v = getattr(body, f)
        if v is not None:
            setattr(u, f, v)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=actor, action="user.edit",
                entity_type="user", entity_id=u.id,
                details={"before": before, "after": _user_dict(u, db)}, request=request)
    db.commit()
    return _user_dict(u, db)


@router.post("/users/{user_id}/role")
def assign_role(user_id: int, body: RoleAssign, request: Request,
                tenant_id: int = Depends(get_tenant_id),
                actor: User = Depends(require_permission("roles.assign")),
                db: Session = Depends(get_db)):
    u = _get_user(db, tenant_id, user_id)
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(400, "Invalid role")
    old = u.role
    u.role = body.role
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=actor, action="user.role_change",
                entity_type="user", entity_id=u.id,
                details={"from": old, "to": body.role}, request=request)
    db.commit()
    return _user_dict(u, db)


@router.post("/users/{user_id}/state")
def set_user_state(user_id: int, request: Request,
                   activate: bool | None = None, lock: bool | None = None,
                   tenant_id: int = Depends(get_tenant_id),
                   actor: User = Depends(require_permission("users.lock")),
                   db: Session = Depends(get_db)):
    """Activate/deactivate and lock/unlock in one place (query params)."""
    u = _get_user(db, tenant_id, user_id)
    action = []
    if activate is not None:
        u.active = activate
        u.deactivated_at = None if activate else datetime.utcnow()
        action.append("reactivate" if activate else "deactivate")
    if lock is not None:
        u.is_locked = lock
        action.append("lock" if lock else "unlock")
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=actor, action="user." + ("_".join(action) or "state"),
                entity_type="user", entity_id=u.id,
                details={"active": u.active, "is_locked": bool(u.is_locked)}, request=request)
    db.commit()
    return _user_dict(u, db)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: PasswordReset, request: Request,
                   tenant_id: int = Depends(get_tenant_id),
                   actor: User = Depends(require_permission("users.lock")),
                   db: Session = Depends(get_db)):
    u = _get_user(db, tenant_id, user_id)
    if body.password:
        u.hashed_password = hash_password(body.password)
        u.force_password_reset = False
    else:
        u.force_password_reset = True
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=actor, action="user.password_reset",
                entity_type="user", entity_id=u.id,
                details={"forced": body.password is None}, request=request)
    db.commit()
    return _user_dict(u, db)


# ---------------------------------------------------------------------------
# Regions & branches
# ---------------------------------------------------------------------------
@router.get("/org")
def list_org(tenant_id: int = Depends(get_tenant_id),
             _: User = Depends(require_permission("org.view")),
             db: Session = Depends(get_db)):
    regions = db.query(Region).filter(Region.tenant_id == tenant_id).order_by(Region.id).all()
    branches = db.query(Branch).filter(Branch.tenant_id == tenant_id).order_by(Branch.id).all()
    staff = db.query(Staff).filter(Staff.tenant_id == tenant_id, Staff.active == True).all()  # noqa: E712
    return {
        "regions": [{"id": r.id, "name": r.name} for r in regions],
        "branches": [{"id": b.id, "name": b.name, "region_id": b.region_id} for b in branches],
        "staff": [{"id": s.id, "name": s.name, "role": s.role, "branch_id": s.branch_id} for s in staff],
    }


@router.post("/regions")
def create_region(body: RegionCreate, request: Request,
                  tenant_id: int = Depends(get_tenant_id),
                  actor: User = Depends(require_permission("org.manage")),
                  db: Session = Depends(get_db)):
    r = Region(tenant_id=tenant_id, name=body.name)
    db.add(r); db.flush()
    write_audit(db, tenant_id=tenant_id, user=actor, action="region.create",
                entity_type="region", entity_id=r.id, details={"name": body.name}, request=request)
    db.commit()
    return {"id": r.id, "name": r.name}


@router.post("/branches")
def create_branch(body: BranchCreate, request: Request,
                  tenant_id: int = Depends(get_tenant_id),
                  actor: User = Depends(require_permission("org.manage")),
                  db: Session = Depends(get_db)):
    if not db.query(Region).filter(Region.id == body.region_id, Region.tenant_id == tenant_id).first():
        raise HTTPException(400, "Region not found in this tenant")
    b = Branch(tenant_id=tenant_id, name=body.name, region_id=body.region_id)
    db.add(b); db.flush()
    write_audit(db, tenant_id=tenant_id, user=actor, action="branch.create",
                entity_type="branch", entity_id=b.id,
                details={"name": body.name, "region_id": body.region_id}, request=request)
    db.commit()
    return {"id": b.id, "name": b.name, "region_id": b.region_id}


# ---------------------------------------------------------------------------
# Approval thresholds
# ---------------------------------------------------------------------------
@router.get("/thresholds")
def list_thresholds(tenant_id: int = Depends(get_tenant_id),
                    _: User = Depends(require_permission("thresholds.view")),
                    db: Session = Depends(get_db)):
    rows = db.query(ApprovalThreshold).filter(ApprovalThreshold.tenant_id == tenant_id).order_by(ApprovalThreshold.id).all()
    return [{"id": t.id, "scope_type": t.scope_type, "scope_key": t.scope_key,
             "threshold_type": t.threshold_type, "amount": float(t.amount)} for t in rows]


@router.post("/thresholds")
def upsert_threshold(body: ThresholdCreate, request: Request,
                     tenant_id: int = Depends(get_tenant_id),
                     actor: User = Depends(require_permission("thresholds.manage")),
                     db: Session = Depends(get_db)):
    row = db.query(ApprovalThreshold).filter(
        ApprovalThreshold.tenant_id == tenant_id,
        ApprovalThreshold.scope_type == body.scope_type,
        ApprovalThreshold.scope_key == body.scope_key,
        ApprovalThreshold.threshold_type == body.threshold_type,
    ).first()
    if row:
        row.amount = body.amount
    else:
        row = ApprovalThreshold(tenant_id=tenant_id, scope_type=body.scope_type,
                                scope_key=body.scope_key, threshold_type=body.threshold_type,
                                amount=body.amount)
        db.add(row)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=actor, action="threshold.set",
                entity_type="threshold", entity_id=row.id,
                details=body.model_dump(), request=request)
    db.commit()
    return {"id": row.id, "scope_type": row.scope_type, "scope_key": row.scope_key,
            "threshold_type": row.threshold_type, "amount": float(row.amount)}


@router.delete("/thresholds/{tid}")
def delete_threshold(tid: int, request: Request,
                     tenant_id: int = Depends(get_tenant_id),
                     actor: User = Depends(require_permission("thresholds.manage")),
                     db: Session = Depends(get_db)):
    row = db.query(ApprovalThreshold).filter(ApprovalThreshold.id == tid, ApprovalThreshold.tenant_id == tenant_id).first()
    if not row:
        raise HTTPException(404, "Threshold not found")
    db.delete(row)
    write_audit(db, tenant_id=tenant_id, user=actor, action="threshold.delete",
                entity_type="threshold", entity_id=tid, request=request)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Per-DCP approver configuration (SUPER ADMIN ONLY)
# ---------------------------------------------------------------------------
@router.get("/approver-config")
def get_approver_config(tenant_id: int | None = None,
                        _: User = Depends(require_role("super_admin")),
                        db: Session = Depends(get_db)):
    """For a DCP, list every eligible approver role per approval type with its
    current EFFECTIVE enabled state (stored override, else permission default).

    Also returns the full tenant list so the super admin can pick a DCP.
    """
    tenants = [{"id": t.id, "name": t.name, "code": t.code, "active": t.active}
               for t in db.query(Tenant).order_by(Tenant.id)]
    result = {"tenants": tenants, "tenant_id": tenant_id, "approval_types": [], }
    if tenant_id is None:
        return result

    # Stored overrides for this tenant, keyed by (approval_type, role).
    rows = db.query(ApproverSetting).filter(ApproverSetting.tenant_id == tenant_id).all()
    stored = {(r.approval_type, r.role): bool(r.enabled) for r in rows}

    for at in APPROVAL_TYPES:
        roles = []
        for role in eligible_approver_roles(at):
            key = (at, role)
            configured = key in stored
            enabled = stored[key] if configured else rbac._default_approver_enabled(at, role)
            roles.append({
                "role": role,
                "label": ROLE_LABELS.get(role, role),
                "enabled": enabled,
                "configured": configured,
                "default": rbac._default_approver_enabled(at, role),
            })
        result["approval_types"].append({
            "approval_type": at,
            "label": APPROVAL_TYPE_LABELS.get(at, at),
            "roles": roles,
        })
    return result


@router.post("/approver-config")
def set_approver_config(body: ApproverConfigIn, request: Request,
                        actor: User = Depends(require_role("super_admin")),
                        db: Session = Depends(get_db)):
    """Upsert a single per-DCP approver toggle. Super admin only; audited."""
    if body.approval_type not in APPROVAL_TYPES:
        raise HTTPException(400, f"approval_type must be one of {APPROVAL_TYPES}")
    if body.role not in eligible_approver_roles(body.approval_type):
        raise HTTPException(
            400, f"Role '{body.role}' is not an eligible approver for '{body.approval_type}'")
    if not db.query(Tenant).filter(Tenant.id == body.tenant_id).first():
        raise HTTPException(404, "Tenant not found")

    row = db.query(ApproverSetting).filter(
        ApproverSetting.tenant_id == body.tenant_id,
        ApproverSetting.approval_type == body.approval_type,
        ApproverSetting.role == body.role,
    ).first()
    if row:
        row.enabled = body.enabled
        row.updated_by_user_id = actor.id
    else:
        row = ApproverSetting(tenant_id=body.tenant_id, approval_type=body.approval_type,
                              role=body.role, enabled=body.enabled,
                              updated_by_user_id=actor.id)
        db.add(row)
    db.flush()
    write_audit(db, tenant_id=body.tenant_id, user=actor, action="approver_config.set",
                entity_type="approver_setting", entity_id=row.id,
                details=body.model_dump(), request=request)
    db.commit()
    return {"id": row.id, "tenant_id": row.tenant_id, "approval_type": row.approval_type,
            "role": row.role, "enabled": bool(row.enabled)}


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
@router.get("/audit")
def audit_trail(tenant_id: int = Depends(get_tenant_id),
                _: User = Depends(require_permission("audit.view")),
                db: Session = Depends(get_db),
                action: str | None = None, entity_type: str | None = None,
                user_email: str | None = None, limit: int = 200):
    q = db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if user_email:
        q = q.filter(AuditLog.user_email.ilike(f"%{user_email}%"))
    rows = q.order_by(AuditLog.created_at.desc()).limit(min(limit, 500)).all()
    return [{"id": a.id, "created_at": a.created_at.isoformat() if a.created_at else None,
             "user_email": a.user_email, "action": a.action,
             "entity_type": a.entity_type, "entity_id": a.entity_id,
             "details": a.details, "ip": a.ip} for a in rows]


# ---------------------------------------------------------------------------
# Bulk payment file / batch upload  (WP4: no longer a mock)
# ---------------------------------------------------------------------------
PAYMENT_UPLOAD_MAX_ROWS = 2000


def _parse_upload_date(value: str):
    """Optional `date` column: YYYY-MM-DD or 'YYYY-MM-DD HH:MM[:SS]' in Kenyan time,
    returned as naive UTC like every other timestamp. None when blank."""
    from datetime import timedelta
    v = (value or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt) - timedelta(hours=3)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{v}' (use YYYY-MM-DD or YYYY-MM-DD HH:MM)")


@router.post("/payment-upload")
async def payment_upload(request: Request, file: UploadFile = File(...),
                         tenant_id: int = Depends(get_tenant_id),
                         actor: User = Depends(require_permission("payments.upload")),
                         db: Session = Depends(get_db)):
    """Bulk-record payments from a CSV (e.g. a paybill or bank statement export).

    Columns (case-insensitive): `account_number` OR `customer_number`, `amount`,
    `mpesa_ref` (or `reference`), optional `date`, optional `channel`
    (mpesa_paybill | mpesa_till | bank | cash | other; default mpesa_paybill).

    WP4 — every row now follows the same rules as a manual payment:
      * matched only within this DCP (loan account number or customer number);
      * the reference must not already be recorded (repayment, suspense, or a
        manual entry awaiting approval) and may appear only once in the file;
      * amounts are exact decimals and must be positive;
      * above the DCP's `manual_payment` threshold (always, when none is set)
        each row waits for a second approver instead of changing a balance.
    Rows that cannot be matched are reported back, not guessed or parked."""
    from decimal import Decimal, InvalidOperation
    from app.core.money import money
    from app.services import payment_approvals as pay_appr
    from app.services import repayments as rp
    from app.services.payment_matching import match_reference

    raw = (await file.read()).decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(raw))
    parked, applied, unmatched, duplicates, errors = [], [], [], [], []
    seen_refs = set()
    reasons = {"unmatched": "account / customer number not found",
               "no_open_loan": "customer has no open loan",
               "ambiguous_customer": "customer has several open loans — use the loan account number"}
    for count, row in enumerate(reader, start=1):
        line = count + 1  # row 1 is the header
        if count > PAYMENT_UPLOAD_MAX_ROWS:
            errors.append(f"file has more than {PAYMENT_UPLOAD_MAX_ROWS} rows; the rest were not read")
            break
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
        acct = r.get("account_number") or r.get("customer_number")
        ref = pay_appr.clean_reference(r.get("mpesa_ref") or r.get("reference"))
        if not acct or not r.get("amount") or not ref:
            errors.append(f"row {line}: account/customer number, amount and mpesa_ref are required")
            continue
        try:
            amount = money(Decimal(r["amount"].replace(",", "")))
        except (InvalidOperation, ValueError):
            errors.append(f"row {line}: bad amount '{r['amount']}'")
            continue
        if amount <= 0:
            errors.append(f"row {line}: amount must be positive")
            continue
        try:
            paid_at = _parse_upload_date(r.get("date"))
        except ValueError as exc:
            errors.append(f"row {line}: {exc}")
            continue
        if paid_at is not None and paid_at > datetime.utcnow():
            errors.append(f"row {line}: date is in the future")
            continue
        channel = (r.get("channel") or "mpesa_paybill").lower()
        if channel not in pay_appr.MANUAL_CHANNELS:
            errors.append(f"row {line}: unknown channel '{channel}'")
            continue
        if ref in seen_refs:
            duplicates.append({"row": line, "reference": ref, "reason": "repeated in this file"})
            continue
        seen_refs.add(ref)
        try:
            pay_appr.ensure_reference_free(db, tenant_id, ref)
        except HTTPException as exc:
            duplicates.append({"row": line, "reference": ref, "reason": exc.detail})
            continue
        match = match_reference(db, tenant_id, acct)
        if match.loan is None:
            unmatched.append({"row": line, "account": acct, "amount": float(amount),
                              "reference": ref, "reason": reasons.get(match.reason, match.reason)})
            continue
        loan = match.loan
        if loan.status not in rp.COLLECTIBLE_STATUSES:
            unmatched.append({"row": line, "account": acct, "amount": float(amount),
                              "reference": ref, "reason": f"loan is {loan.status}"})
            continue
        details = {"channel": channel, "reference": ref,
                   "paid_at": (paid_at or datetime.utcnow()).isoformat(),
                   "note": f"CSV upload {file.filename}, row {line}",
                   "source_endpoint": "payment_upload"}
        if rbac.requires_maker_checker(db, tenant_id, pay_appr.MANUAL_THRESHOLD, float(amount)):
            p = pay_appr.park_manual_payment(db, tenant_id=tenant_id, user=actor, loan=loan,
                                             amount=amount, details=details,
                                             reason=details["note"][:200])
            parked.append({"row": line, "reference": ref, "loan_id": loan.id, "pending_id": p.id})
        else:
            res = pay_appr.apply_manual(db, tenant_id=tenant_id, loan_id=loan.id, amount=amount,
                                        details=details, actor_user_id=actor.id)
            applied.append({"row": line, "reference": ref, "loan_id": loan.id,
                            "applied": res["applied"]})
    write_audit(db, tenant_id=tenant_id, user=actor, action="payments.batch_upload",
                entity_type="payment_batch", entity_id=(file.filename or "")[:40],
                details={"parked": len(parked), "applied": len(applied),
                         "unmatched": len(unmatched), "duplicates": len(duplicates),
                         "errors": len(errors)},
                request=request)
    db.commit()
    return {"filename": file.filename,
            "pending_approval": len(parked), "applied": len(applied),
            "matched": len(parked) + len(applied),
            "unmatched": len(unmatched), "unmatched_rows": unmatched[:200],
            "duplicates": duplicates[:200], "errors": errors[:50]}


# ---------------------------------------------------------------------------
# Backups & data-integrity checks (mock)
# ---------------------------------------------------------------------------
@router.post("/backup")
def trigger_backup(request: Request, tenant_id: int = Depends(get_tenant_id),
                   actor: User = Depends(require_permission("backups.manage")),
                   db: Session = Depends(get_db)):
    ref = "BKP-" + datetime.utcnow().strftime("%Y%m%d%H%M%S")
    counts = {
        "borrowers": db.query(Borrower).filter(Borrower.tenant_id == tenant_id).count(),
        "loans": db.query(Loan).filter(Loan.tenant_id == tenant_id).count(),
        "repayments": db.query(Repayment).filter(Repayment.tenant_id == tenant_id).count(),
    }
    write_audit(db, tenant_id=tenant_id, user=actor, action="backup.run",
                entity_type="backup", entity_id=ref, details=counts, request=request)
    db.commit()
    return {"reference": ref, "status": "completed", "snapshot_counts": counts,
            "created_at": datetime.utcnow().isoformat(),
            "note": "Mock logical snapshot — records the row counts captured at backup time."}


@router.get("/integrity")
def integrity_check(tenant_id: int = Depends(get_tenant_id),
                    actor: User = Depends(require_permission("backups.manage")),
                    db: Session = Depends(get_db)):
    """Consistency checks on this DCP's loans.

    WP5 (F25): exact Decimal arithmetic (the old float/Decimal mix crashed), and
    balance mismatches now count against `healthy`. For an open loan:
        balance = principal + interest + fees in balance + penalties charged
                  - repayments - early-settlement interest written off
    A difference above KES 1 is listed. Loans with penalties charged before WP5
    were not recorded and will show up here until reviewed."""
    from app.core.money import D, money
    loans = db.query(Loan).filter(Loan.tenant_id == tenant_id).all()
    borrower_ids = {b.id for b in db.query(Borrower.id).filter(Borrower.tenant_id == tenant_id)}
    loans_without_borrower = sum(1 for l in loans if l.borrower_id not in borrower_ids)

    mismatches = []
    for l in loans:
        if l.status not in ("active", "overdue", "defaulted"):
            continue
        paid = sum((D(r.amount or 0) for r in l.repayments), D(0))
        expected = money(D(l.total_due) + D(getattr(l, "fees_in_balance", 0) or 0)
                         + D(getattr(l, "penalties_charged", 0) or 0)
                         - paid - D(getattr(l, "interest_rebate", 0) or 0))
        actual = money(l.outstanding_balance or 0)
        if abs(expected - actual) > D("1.00"):
            mismatches.append({"loan_id": l.id, "account_number": l.account_number,
                               "expected": float(expected), "actual": float(actual)})

    negative_balances = sum(1 for l in loans if money(l.outstanding_balance or 0) < 0)
    checks = {
        "loans_without_client": loans_without_borrower,
        "outstanding_balance_mismatches": len(mismatches),
        "negative_balances": negative_balances,
        "total_loans_checked": len(loans),
    }
    healthy = loans_without_borrower == 0 and negative_balances == 0 and not mismatches
    write_audit(db, tenant_id=tenant_id, user=actor, action="integrity.check",
                entity_type="integrity", details=checks)
    db.commit()
    return {"healthy": healthy, "checks": checks, "mismatches": mismatches[:100],
            "checked_at": datetime.utcnow().isoformat()}
