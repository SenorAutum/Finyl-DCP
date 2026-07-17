"""Super Admin: tenant management + module feature-flag matrix."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_role
from app.models import MODULE_KEYS, Tenant, TenantModule, User
from app.schemas import ModuleToggle, TenantCreate

router = APIRouter(prefix="/api/v1/admin", tags=["admin"],
                   dependencies=[Depends(require_role("super_admin"))])


@router.get("/module-matrix")
def module_matrix(db: Session = Depends(get_db)):
    """Grid of tenants × modules with enabled flags."""
    tenants = db.query(Tenant).order_by(Tenant.id).all()
    flags = db.query(TenantModule).all()
    fmap = {(f.tenant_id, f.module_key): f.enabled for f in flags}
    return {
        "module_keys": MODULE_KEYS,
        "tenants": [{
            "id": t.id, "name": t.name, "code": t.code,
            "logo_color": t.logo_color, "active": t.active,
            "modules": {k: fmap.get((t.id, k), False) for k in MODULE_KEYS},
        } for t in tenants],
    }


@router.post("/tenants")
def create_tenant(body: TenantCreate, db: Session = Depends(get_db)):
    tenant = Tenant(**body.model_dump())
    db.add(tenant)
    db.flush()
    for key in MODULE_KEYS:  # new tenants start with all modules on
        db.add(TenantModule(tenant_id=tenant.id, module_key=key, enabled=True))
    db.commit()
    return {"id": tenant.id, "name": tenant.name}


@router.patch("/tenants/{tenant_id}")
def update_tenant(tenant_id: int, body: TenantCreate, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    for k, v in body.model_dump().items():
        setattr(tenant, k, v)
    db.commit()
    return {"ok": True}


@router.post("/modules/toggle")
def toggle_module(body: ModuleToggle, db: Session = Depends(get_db)):
    if body.module_key not in MODULE_KEYS:
        raise HTTPException(400, f"Unknown module '{body.module_key}'")
    row = (db.query(TenantModule)
           .filter(TenantModule.tenant_id == body.tenant_id,
                   TenantModule.module_key == body.module_key).first())
    if not row:
        row = TenantModule(tenant_id=body.tenant_id, module_key=body.module_key)
        db.add(row)
    row.enabled = body.enabled
    db.commit()
    return {"tenant_id": body.tenant_id, "module_key": body.module_key, "enabled": row.enabled}
