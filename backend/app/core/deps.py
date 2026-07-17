"""
FastAPI dependencies: current user resolution, tenant scoping, feature-flag
enforcement (`require_module`) and role gates.

Tenant scoping: every request derives its tenant from the JWT. A super_admin
may switch tenant context with the `X-Tenant-Id` header.
"""
import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models import TenantModule, User

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
