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
                        RegionCreate, BranchCreate, ThresholdCreate, ApproverConfigIn)
from app.services import rbac

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
# Bulk payment file / batch upload (mock parser)
# ---------------------------------------------------------------------------
@router.post("/payment-upload")
async def payment_upload(request: Request, file: UploadFile = File(...),
                         tenant_id: int = Depends(get_tenant_id),
                         actor: User = Depends(require_permission("payments.upload")),
                         db: Session = Depends(get_db)):
    """Parse a CSV of repayments and bulk-create Repayment + C2B transaction rows.

    Expected columns (case-insensitive): account_number, amount, mpesa_ref[, date].
    This is a MOCK reconciliation parser — it matches each row to a loan by
    account_number within the tenant and records the repayment.
    """
    raw = (await file.read()).decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(raw))
    matched, unmatched, errors = 0, 0, []
    for i, row in enumerate(reader, start=1):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        acct, amount = r.get("account_number"), r.get("amount")
        if not acct or not amount:
            errors.append(f"row {i}: missing account_number/amount"); continue
        loan = db.query(Loan).filter(Loan.tenant_id == tenant_id, Loan.account_number == acct).first()
        if not loan:
            unmatched += 1; continue
        try:
            amt = float(amount)
        except ValueError:
            errors.append(f"row {i}: bad amount '{amount}'"); continue
        db.add(Repayment(tenant_id=tenant_id, loan_id=loan.id, amount=amt,
                         payment_date=datetime.utcnow(), method="batch_upload",
                         mpesa_ref=r.get("mpesa_ref")))
        loan.outstanding_balance = max(0, float(loan.outstanding_balance or 0) - amt)
        matched += 1
    write_audit(db, tenant_id=tenant_id, user=actor, action="payments.batch_upload",
                entity_type="payment_batch", entity_id=file.filename,
                details={"matched": matched, "unmatched": unmatched, "errors": len(errors)},
                request=request)
    db.commit()
    return {"filename": file.filename, "matched": matched, "unmatched": unmatched,
            "errors": errors[:20]}


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
    """Lightweight consistency checks reported as simple stats."""
    loans = db.query(Loan).filter(Loan.tenant_id == tenant_id).all()
    borrower_ids = {b.id for b in db.query(Borrower.id).filter(Borrower.tenant_id == tenant_id)}
    loans_without_borrower = sum(1 for l in loans if l.borrower_id not in borrower_ids)

    mismatched = 0
    for l in loans:
        if l.status in ("active", "overdue"):
            paid = sum(float(r.amount) for r in l.repayments)
            expected = round(l.total_due, 2)
            outstanding = float(l.outstanding_balance or 0)
            if abs((expected - paid) - outstanding) > 1.0:
                mismatched += 1

    negative_balances = sum(1 for l in loans if float(l.outstanding_balance or 0) < 0)
    checks = {
        "loans_without_client": loans_without_borrower,
        "outstanding_balance_mismatches": mismatched,
        "negative_balances": negative_balances,
        "total_loans_checked": len(loans),
    }
    healthy = loans_without_borrower == 0 and negative_balances == 0
    write_audit(db, tenant_id=tenant_id, user=actor, action="integrity.check",
                entity_type="integrity", details=checks)
    db.commit()
    return {"healthy": healthy, "checks": checks, "checked_at": datetime.utcnow().isoformat()}
