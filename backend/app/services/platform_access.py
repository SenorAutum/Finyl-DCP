"""WP13 — who on Finyl's side may see what.

Finyl staff and partners are users with role ``platform_user`` and no DCP.
Each has switches (``platform.*`` permissions) and either every DCP or the DCPs
the super admin assigned. The Finyl super admin has every switch and every DCP.
These switches never appear among a DCP's own role permissions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, status

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import PlatformUserAccess, Tenant, User

PLATFORM_ROLE = "platform_user"

PLATFORM_PERMISSIONS = {
    "platform.dashboard": "Command centre and each assigned DCP's dashboard",
    "platform.revenue": "Finyl's revenue: invoices, subscriptions, SMS and service charges",
    "platform.billing_manage": "Set prices, produce invoices and record payments from DCPs",
    "platform.lending": "Finyl's loans to DCPs",
    "platform.lending_manage": "Create loan facilities, record drawdowns and repayments",
    "platform.pipeline": "Onboarded and potential DCPs (sales pipeline)",
    "platform.pipeline_manage": "Add potential DCPs and move them through the pipeline",
    "platform.alerts": "Alerts when something fails",
    "platform.alerts_manage": "Acknowledge and resolve alerts; choose who is told",
    "platform.users_manage": "Create Finyl staff and partners; set their switches and DCPs",
}

PRESETS = {
    "director": {
        "label": "Director / partner (sees, does not change)",
        "permissions": ["platform.dashboard", "platform.revenue", "platform.lending",
                        "platform.pipeline", "platform.alerts"],
    },
    "finance": {
        "label": "Finance",
        "permissions": ["platform.dashboard", "platform.revenue", "platform.billing_manage",
                        "platform.lending", "platform.lending_manage"],
    },
    "operations": {
        "label": "Operations",
        "permissions": ["platform.dashboard", "platform.pipeline", "platform.pipeline_manage",
                        "platform.alerts", "platform.alerts_manage"],
    },
    "support": {
        "label": "Support",
        "permissions": ["platform.dashboard", "platform.alerts", "platform.alerts_manage"],
    },
    "custom": {"label": "Custom (choose the switches)", "permissions": []},
}


@dataclass
class PlatformAccess:
    user: User
    permissions: set = field(default_factory=set)
    all_tenants: bool = False
    tenant_ids: set = field(default_factory=set)
    preset: str = "custom"
    title: str | None = None

    @property
    def is_super(self) -> bool:
        return self.user.role == "super_admin"

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    def sees(self, tenant_id: int | None) -> bool:
        return self.all_tenants or (tenant_id is not None and tenant_id in self.tenant_ids)

    def tenant_filter(self, query, column):
        """Limit a query to the DCPs this person may see."""
        if self.all_tenants:
            return query
        return query.filter(column.in_(sorted(self.tenant_ids) or [-1]))

    def as_dict(self) -> dict:
        return {"permissions": sorted(self.permissions), "all_tenants": self.all_tenants,
                "tenant_ids": sorted(self.tenant_ids), "preset": self.preset,
                "title": self.title, "is_super_admin": self.is_super}


def access_for(db, user: User) -> PlatformAccess:
    if user.role == "super_admin":
        return PlatformAccess(user=user, permissions=set(PLATFORM_PERMISSIONS), all_tenants=True,
                              preset="super_admin", title="Finyl super admin")
    if user.role != PLATFORM_ROLE:
        return PlatformAccess(user=user)
    row = db.get(PlatformUserAccess, user.id)
    if row is None:
        return PlatformAccess(user=user)
    perms = {p for p in (row.permissions or []) if p in PLATFORM_PERMISSIONS}
    return PlatformAccess(user=user, permissions=perms, all_tenants=bool(row.all_tenants),
                          tenant_ids={int(t) for t in (row.tenant_ids or [])},
                          preset=row.preset or "custom", title=row.title)


def require_platform(perm: str):
    """FastAPI dependency: the signed-in person must have this Finyl switch."""
    def dependency(user: User = Depends(get_current_user), db=Depends(get_db)) -> PlatformAccess:
        acc = access_for(db, user)
        if not acc.can(perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "You do not have access to this part of the Finyl command centre")
        return acc
    return dependency


def check_tenant(acc: PlatformAccess, db, tenant_id: int) -> Tenant:
    """The DCP, if this person may see it; otherwise 404 (no hint it exists)."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or getattr(tenant, "is_internal", False) or not acc.sees(tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DCP not found")
    return tenant


def visible_tenants(acc: PlatformAccess, db) -> list[Tenant]:
    # WP14: Finyl's own workspace is not a DCP.
    return (acc.tenant_filter(db.query(Tenant).filter(Tenant.is_internal.is_(False)), Tenant.id)
            .order_by(Tenant.name).all())
