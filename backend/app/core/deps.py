"""
FastAPI dependencies: current user resolution, tenant scoping, feature-flag
enforcement (`require_module`) and role gates.

Tenant scoping: every request derives its tenant from the JWT. A super_admin
may switch tenant context with the `X-Tenant-Id` header.
"""
from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.core.permissions import has_permission, permissions_for, COMPANY_SCOPE_ROLES
from app.models import AuditLog, Branch, Staff, TenantModule, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

ROLE_HIERARCHY = {"super_admin": 4, "tenant_admin": 3, "loan_officer": 2, "call_agent": 1}


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    try:
        payload = decode_token(token)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    # AUTH-04: token_version mismatch => the token was revoked (logout / password
    # change bumps the user's token_version).
    if int(payload.get("tv", 0)) != int(getattr(user, "token_version", 0) or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has been revoked")
    if getattr(user, "is_locked", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is locked")
    # AUTH-02: honor an active temporary auto-lock window.
    locked_until = getattr(user, "locked_until", None)
    if locked_until is not None:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until > datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_423_LOCKED,
                                "Account temporarily locked")
    return user


def get_tenant_id(
    user: User = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None),
) -> int:
    """Resolve the effective tenant. super_admin can impersonate via X-Tenant-Id."""
    if user.role == "super_admin":
        if x_tenant_id:
            return int(x_tenant_id)
        if user.tenant_id:
            return user.tenant_id
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "super_admin must supply X-Tenant-Id header")
    return user.tenant_id


def require_module(module_key: str):
    """403 when the tenant's feature flag for `module_key` is off."""
    def dependency(
        tenant_id: int = Depends(get_tenant_id),
        db: Session = Depends(get_db),
    ) -> int:
        row = (
            db.query(TenantModule)
            .filter(TenantModule.tenant_id == tenant_id, TenantModule.module_key == module_key)
            .first()
        )
        if not row or not row.enabled:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Module '{module_key}' is not enabled for this tenant",
            )
        return tenant_id
    return dependency


def require_role(*roles: str):
    """Restrict an endpoint to specific roles (super_admin always passes)."""
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role == "super_admin" or user.role in roles:
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
    return dependency


# ---------------------------------------------------------------------------
# Permission-driven access control
# ---------------------------------------------------------------------------
def require_permission(*keys: str, mode: str = "all"):
    """Enforce that the current user holds the given permission key(s).

    mode="all" (default) requires every key; mode="any" requires at least one.
    super_admin passes unconditionally (wildcard). Returns the User so handlers
    can use it directly.
    """
    def dependency(user: User = Depends(get_current_user)) -> User:
        checks = [has_permission(user.role, k) for k in keys]
        ok = all(checks) if mode == "all" else any(checks)
        if not ok:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Missing required permission: {' & '.join(keys) if mode == 'all' else ' / '.join(keys)}",
            )
        return user
    return dependency


class UserScope:
    """Resolved data-scope for the current user, derived from role + org links."""

    def __init__(self, user: User, db: Session):
        self.user = user
        self.role = user.role
        self.tenant_id = user.tenant_id
        self.staff_id = user.staff_id
        self.branch_id = user.branch_id
        self.region_id = user.region_id
        self.company_wide = user.role in COMPANY_SCOPE_ROLES
        # Regional managers see all branches inside their region.
        self.branch_ids = None
        if self.role == "regional_manager" and self.region_id:
            self.branch_ids = [b.id for b in db.query(Branch.id)
                               .filter(Branch.region_id == self.region_id)]
        elif self.role in ("branch_manager",) and self.branch_id:
            self.branch_ids = [self.branch_id]

    # --- query narrowing ---------------------------------------------------
    def apply_loan(self, query, Loan):
        if self.company_wide:
            return query
        if self.role in ("relationship_officer", "loan_officer", "call_agent"):
            return query.filter(Loan.staff_id == self.staff_id)
        if self.branch_ids is not None:
            return query.filter(Loan.branch_id.in_(self.branch_ids))
        return query.filter(Loan.id == -1)  # no scope resolvable -> nothing

    def apply_client(self, query, Client):
        if self.company_wide:
            return query
        if self.role in ("relationship_officer", "loan_officer", "call_agent"):
            return query.filter(Client.officer_staff_id == self.staff_id)
        if self.branch_ids is not None:
            return query.filter(Client.branch_id.in_(self.branch_ids))
        return query.filter(Client.id == -1)

    def can_see_loan(self, loan) -> bool:
        if self.company_wide:
            return True
        if self.role in ("relationship_officer", "loan_officer", "call_agent"):
            return loan.staff_id == self.staff_id
        if self.branch_ids is not None:
            return loan.branch_id in self.branch_ids
        return False

    def can_see_client(self, client) -> bool:
        if self.company_wide:
            return True
        if self.role in ("relationship_officer", "loan_officer", "call_agent"):
            return client.officer_staff_id == self.staff_id
        if self.branch_ids is not None:
            return client.branch_id in self.branch_ids
        return False

    def as_dict(self) -> dict:
        return {
            "role": self.role, "staff_id": self.staff_id,
            "branch_id": self.branch_id, "region_id": self.region_id,
            "company_wide": self.company_wide, "branch_ids": self.branch_ids,
        }


def get_scope(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserScope:
    return UserScope(user, db)


def write_audit(db: Session, *, tenant_id, user, action, entity_type=None,
                entity_id=None, details=None, request: Request | None = None):
    """Append an audit-trail entry. Never raises — auditing must not break flows."""
    try:
        ip = None
        if request is not None:
            ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
        db.add(AuditLog(
            tenant_id=tenant_id,
            user_id=getattr(user, "id", None),
            user_email=getattr(user, "email", None),
            action=action, entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=details or {}, ip=ip,
        ))
        db.flush()
    except Exception:
        db.rollback()
