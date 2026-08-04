"""Auth endpoints: login, current profile, tenant module flags."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_tenant_id, get_scope, UserScope, write_audit
from app.core.security import create_access_token, verify_password
from app.core.permissions import permissions_for, ROLE_LABELS
from app.models import MODULE_KEYS, Tenant, TenantModule, User
from app.schemas import LoginRequest

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _login(db: Session, email: str, password: str, request: Request = None) -> dict:
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    if getattr(user, "is_locked", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is locked — contact your administrator")
    token = create_access_token(user.id, user.role, user.tenant_id)
    write_audit(db, tenant_id=user.tenant_id, user=user, action="auth.login",
                entity_type="user", entity_id=user.id, request=request)
    db.commit()
    return {"access_token": token, "token_type": "bearer"}


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db), request: Request = None):
    return _login(db, body.email, body.password, request)


@router.post("/login/form")
def login_form(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db),
               request: Request = None):
    """OAuth2 password-flow variant (used by Swagger UI)."""
    return _login(db, form.username, form.password, request)


@router.get("/me")
def me(user: User = Depends(get_current_user),
       tenant_id: int = Depends(get_tenant_id),
       scope: UserScope = Depends(get_scope),
       db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id) if tenant_id else None
    flags = {k: False for k in MODULE_KEYS}
    if tenant_id:
        for m in db.query(TenantModule).filter(TenantModule.tenant_id == tenant_id):
            flags[m.module_key] = m.enabled
    if user.role == "super_admin":
        flags = {k: True for k in MODULE_KEYS}  # super_admin sees everything
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name,
        "role": user.role, "role_label": ROLE_LABELS.get(user.role, user.role),
        "tenant_id": tenant_id,
        "tenant_name": tenant.name if tenant else None,
        "tenant_color": tenant.logo_color if tenant else "#10B981",
        "modules": flags,
        "permissions": permissions_for(user.role),
        "scope": scope.as_dict(),
        "staff_id": user.staff_id,
        "branch_id": getattr(user, "branch_id", None),
        "region_id": getattr(user, "region_id", None),
        "force_password_reset": bool(getattr(user, "force_password_reset", False)),
    }


@router.get("/tenants")
def switchable_tenants(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Tenants a super_admin can switch context to."""
    if user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "super_admin only")
    return [{"id": t.id, "name": t.name, "code": t.code, "active": t.active}
            for t in db.query(Tenant).order_by(Tenant.id)]
