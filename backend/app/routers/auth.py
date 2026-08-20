"""Auth endpoints: login, current profile, tenant module flags.

AUTH-02: failed logins increment a counter; crossing the threshold auto-locks
the account for a cooldown window. A lightweight in-memory per-IP rate limiter
throttles credential-stuffing bursts (single uvicorn worker -> shared state).
AUTH-03: self-service password change that clears force_password_reset.
AUTH-04: password change / logout bump the user's token_version, revoking every
previously issued JWT.
"""
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_tenant_id, get_scope, UserScope, write_audit
from app.core.security import create_access_token, verify_password, hash_password
from app.core.permissions import permissions_for, ROLE_LABELS
from app.core.obs import log_auth_event
from app.models import ApprovalThreshold, MODULE_KEYS, Tenant, TenantModule, User
from app.schemas import LoginRequest, ChangePasswordRequest, SignupRequest

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# --- AUTH-02 tunables --------------------------------------------------------
MAX_FAILED_ATTEMPTS = 5          # consecutive bad passwords before auto-lock
LOCKOUT_MINUTES = 15             # how long the auto-lock lasts
RATE_LIMIT_MAX = 10              # max login attempts per IP ...
RATE_LIMIT_WINDOW = 60           # ... per this many seconds

# In-memory sliding-window rate-limit state. The service runs a single uvicorn
# worker, so this per-process dict is authoritative. (For a multi-worker /
# multi-host deploy, swap this for the nginx `limit_req` zone shipped in
# deploy/finyl-dcp.conf or a shared Redis counter.)
_rl_lock = threading.Lock()
_rl_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request | None) -> str:
    if request is None:
        return "unknown"
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_login(request: Request = None):
    """Reject a client that exceeds RATE_LIMIT_MAX login attempts per window."""
    ip = _client_ip(request)
    now = time.monotonic()
    with _rl_lock:
        hits = _rl_hits[ip]
        cutoff = now - RATE_LIMIT_WINDOW
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_MAX:
            retry = int(RATE_LIMIT_WINDOW - (now - hits[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many login attempts. Please wait a moment and try again.",
                headers={"Retry-After": str(max(retry, 1))},
            )
        hits.append(now)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_locked(user: User) -> bool:
    """True while the account is under an admin lock or an active auto-lock."""
    if getattr(user, "is_locked", False):
        return True
    locked_until = getattr(user, "locked_until", None)
    if locked_until is not None:
        # locked_until is TIMESTAMPTZ (aware); guard against a naive value.
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        return locked_until > _utcnow()
    return False


def _login(db: Session, email: str, password: str, request: Request = None) -> dict:
    user = db.query(User).filter(User.email == email.lower().strip()).first()

    # Generic message: never disclose whether the email exists or is locked.
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    ip = _client_ip(request)

    if user and _is_locked(user):
        # Do not leak lock state as a distinct signal to anonymous callers;
        # 423 tells a legitimate user their account is temporarily protected.
        log_auth_event("login_blocked_locked", email=email, user_id=user.id, ip=ip)
        raise HTTPException(
            status.HTTP_423_LOCKED,
            "Account temporarily locked due to repeated failed logins. "
            "Try again later or contact your administrator.",
        )

    if not user or not verify_password(password, user.hashed_password):
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = _utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
                write_audit(db, tenant_id=user.tenant_id, user=user,
                            action="auth.account_locked", entity_type="user",
                            entity_id=user.id,
                            details={"failed_attempts": user.failed_login_attempts},
                            request=request)
                log_auth_event("account_locked", email=email, user_id=user.id, ip=ip,
                               detail=f"failed_attempts={user.failed_login_attempts}")
            db.commit()
        # Do not disclose whether the email existed.
        log_auth_event("login_failed", email=email,
                       user_id=getattr(user, "id", None), ip=ip)
        raise invalid

    if not user.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    # Success — clear the brute-force counters.
    user.failed_login_attempts = 0
    user.locked_until = None
    token = create_access_token(user.id, user.role, user.tenant_id,
                                token_version=user.token_version or 0)
    write_audit(db, tenant_id=user.tenant_id, user=user, action="auth.login",
                entity_type="user", entity_id=user.id, request=request)
    db.commit()
    log_auth_event("login_success", email=user.email, user_id=user.id, ip=ip, ok=True)
    return {"access_token": token, "token_type": "bearer",
            "force_password_reset": bool(user.force_password_reset)}


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db), request: Request = None,
          _rl: None = Depends(rate_limit_login)):
    return _login(db, body.email, body.password, request)


@router.post("/login/form")
def login_form(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db),
               request: Request = None, _rl: None = Depends(rate_limit_login)):
    """OAuth2 password-flow variant (used by Swagger UI)."""
    return _login(db, form.username, form.password, request)


# --- AUTH-05: self-service DCP (tenant) signup -------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Sensible out-of-the-box maker-checker / approval limits so a freshly
# registered DCP is usable without a manual RBAC seeding step. Mirrors the
# structure used by seeds/rbac_seed.py.
_DEFAULT_THRESHOLDS = [
    ("role", "relationship_officer", "loan_approval", 0),
    ("role", "branch_manager", "loan_approval", 100000),
    ("role", "regional_manager", "loan_approval", 500000),
    ("role", "all", "disbursement", 200000),
    ("role", "all", "refund", 50000),
]


def _slug_code(name: str) -> str:
    """Derive an uppercase alphanumeric tenant code (<=20 chars) from a name."""
    slug = re.sub(r"[^A-Za-z0-9]", "", name or "").upper()[:12]
    return slug or "DCP"


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest, db: Session = Depends(get_db), request: Request = None,
           _rl: None = Depends(rate_limit_login)):
    """Public self-service registration for a brand-new DCP (tenant).

    In a single transaction this creates the Tenant, enables every platform
    module for it, seeds default approval thresholds, and creates the first
    user as the tenant-scoped administrator (role=system_admin). No JWT is
    issued; the caller is directed to sign in normally.
    """
    # --- server-side validation ---------------------------------------------
    org = (body.organization_name or "").strip()
    if not org:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Organization name is required")
    if len(org) > 120:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Organization name must be 120 characters or fewer")

    full_name = (body.admin_full_name or "").strip()
    if not full_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Administrator full name is required")
    if len(full_name) > 160:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Administrator full name must be 160 characters or fewer")

    email = (body.admin_email or "").strip().lower()
    if not email or not _EMAIL_RE.match(email) or len(email) > 160:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "A valid administrator email address is required")

    password = body.password or ""
    if len(password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Password must be at least 8 characters")
    if len(password) > 128:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Password must be 128 characters or fewer")
    if body.confirm_password is not None and body.confirm_password != password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Passwords do not match")

    color = (body.logo_color or "#10B981").strip() or "#10B981"

    # --- uniqueness pre-checks (case-insensitive) ----------------------------
    from sqlalchemy import func
    if db.query(Tenant).filter(func.lower(Tenant.name) == org.lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "An organization with this name already exists")
    if db.query(User).filter(func.lower(User.email) == email).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "An account with this email already exists")

    # --- unique tenant code --------------------------------------------------
    base = _slug_code(org)
    code = base
    n = 2
    while db.query(Tenant).filter(Tenant.code == code).first():
        suffix = str(n)
        code = f"{base[:20 - len(suffix)]}{suffix}"
        n += 1

    # --- single transaction --------------------------------------------------
    try:
        tenant = Tenant(name=org, code=code, logo_color=color, active=True)
        db.add(tenant)
        db.flush()  # assign tenant.id

        for key in MODULE_KEYS:
            db.add(TenantModule(tenant_id=tenant.id, module_key=key, enabled=True))

        for scope_type, scope_key, threshold_type, amount in _DEFAULT_THRESHOLDS:
            db.add(ApprovalThreshold(tenant_id=tenant.id, scope_type=scope_type,
                                     scope_key=scope_key, threshold_type=threshold_type,
                                     amount=amount))

        user = User(email=email, hashed_password=hash_password(password),
                    full_name=full_name, role="system_admin", tenant_id=tenant.id,
                    active=True, force_password_reset=False)
        db.add(user)
        db.flush()  # assign user.id

        write_audit(db, tenant_id=tenant.id, user=user, action="auth.signup",
                    entity_type="tenant", entity_id=tenant.id,
                    details={"organization_name": org, "admin_email": email},
                    request=request)
        db.commit()
    except IntegrityError:
        # Race: another request registered the same name/email between our
        # pre-check and commit. Return a clean conflict.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "An organization or account with these details already exists")

    return {
        "ok": True,
        "tenant": {"id": tenant.id, "name": tenant.name, "code": tenant.code},
        "admin": {"id": user.id, "email": user.email, "role": user.role},
        "detail": "Account created. Please sign in with your new credentials.",
    }


@router.post("/change-password")
def change_password(body: ChangePasswordRequest,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db),
                    request: Request = None):
    """Self-service password change.

    Verifies the current password, stores a new bcrypt hash, clears the
    force_password_reset flag and bumps token_version so every previously issued
    token (including the one used for this call) is revoked. A fresh token is
    returned so the client can stay signed in.
    """
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    new_pw = (body.new_password or "").strip()
    if len(new_pw) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "New password must be at least 8 characters")
    if verify_password(new_pw, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "New password must differ from the current password")

    user.hashed_password = hash_password(new_pw)
    user.force_password_reset = False
    user.failed_login_attempts = 0
    user.locked_until = None
    user.token_version = (user.token_version or 0) + 1  # revoke old tokens
    write_audit(db, tenant_id=user.tenant_id, user=user, action="auth.change_password",
                entity_type="user", entity_id=user.id, request=request)
    db.commit()

    token = create_access_token(user.id, user.role, user.tenant_id,
                                token_version=user.token_version)
    return {"access_token": token, "token_type": "bearer",
            "detail": "Password changed"}


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db),
           request: Request = None):
    """Revoke the caller's tokens by bumping token_version (AUTH-04)."""
    user.token_version = (user.token_version or 0) + 1
    write_audit(db, tenant_id=user.tenant_id, user=user, action="auth.logout",
                entity_type="user", entity_id=user.id, request=request)
    db.commit()
    return {"detail": "Logged out"}


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
