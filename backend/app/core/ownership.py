"""
Tenant and data-scope ownership guards.

WHY THIS EXISTS
---------------
Most related-table foreign keys in this schema are single-column references
(e.g. ``complaints.borrower_id -> borrowers.id``). The database therefore cannot
stop a row in tenant A from pointing at a row in tenant B, and the API layer
did not consistently check either. Every caller-supplied identifier that names
another record (client, staff, user, region, branch ...) must be resolved
through ONE of these helpers before it is read, written or echoed back.

CONVENTIONS
-----------
* A record that exists but belongs to a different tenant is reported exactly
  like a record that does not exist (404). Distinguishing the two would let a
  caller enumerate other tenants' identifiers.
* A record that is inside the tenant but outside the caller's portfolio /
  branch / region is a 403, matching the existing "outside your data scope"
  behaviour.
* Services (which must not depend on FastAPI) raise ``OwnershipError``; the
  application maps it to a 404 in ``app.main``.
"""
from __future__ import annotations

from fastapi import HTTPException

from app.core.permissions import OFFICER_SCOPE_ROLES


class OwnershipError(LookupError):
    """A referenced record does not exist inside the caller's tenant."""

    def __init__(self, label: str, obj_id=None):
        self.label = label
        self.obj_id = obj_id
        super().__init__(f"{label} not found")


def get_owned(db, model, obj_id, tenant_id: int, *, label: str | None = None,
              lock: bool = False):
    """Return ``model`` row ``obj_id`` iff it belongs to ``tenant_id``.

    ``lock=True`` takes a row lock (SELECT ... FOR UPDATE) so the caller can
    safely read-modify-write; it is a no-op on SQLite.
    """
    q = db.query(model).filter(model.id == obj_id, model.tenant_id == tenant_id)
    if lock:
        q = q.with_for_update()
    row = q.first()
    if row is None:
        raise OwnershipError(label or model.__name__, obj_id)
    return row


def get_owned_optional(db, model, obj_id, tenant_id: int, *, label: str | None = None):
    """Like ``get_owned`` but passes ``None`` straight through (optional FK)."""
    if obj_id is None:
        return None
    return get_owned(db, model, obj_id, tenant_id, label=label)


def require_client(db, tenant_id: int, client_id: int, scope=None, *, lock: bool = False):
    """Client (``Borrower``) in the tenant and, when ``scope`` is given, in scope."""
    from app.models import Borrower
    client = get_owned(db, Borrower, client_id, tenant_id, label="Client", lock=lock)
    if scope is not None and not scope.can_see_client(client):
        raise HTTPException(403, "Client is outside your data scope")
    return client


def require_staff(db, tenant_id: int, staff_id: int, scope=None):
    """Staff record in the tenant and, for branch/region-scoped callers, in scope."""
    from app.models import Staff
    staff = get_owned(db, Staff, staff_id, tenant_id, label="Staff")
    if (scope is not None and not scope.company_wide
            and scope.role not in OFFICER_SCOPE_ROLES
            and scope.branch_ids is not None
            and staff.branch_id not in scope.branch_ids):
        raise HTTPException(403, "Staff member is outside your data scope")
    return staff


def require_user_in_tenant(db, actor, target_user_id: int):
    """Target ``User`` must be in the actor's tenant (super_admin may cross)."""
    from app.models import User
    target = db.get(User, target_user_id)
    if target is None or (actor.role != "super_admin"
                          and target.tenant_id != actor.tenant_id):
        raise OwnershipError("User", target_user_id)
    return target


def resolve_loan_officer(db, tenant_id: int, scope, client, requested_staff_id):
    """Decide which staff member a new loan is booked against.

    * Front-line officers can only book against THEMSELVES. A missing staff
      linkage fails closed rather than producing an unowned loan.
    * Everyone else may name an officer, defaulting to the client's own
      officer; the named officer must be in the tenant and in the caller's
      branch/region scope.
    """
    if scope.role in OFFICER_SCOPE_ROLES:
        if scope.staff_id is None:
            raise HTTPException(
                403, "Your account is not linked to a staff record, so you cannot "
                     "book loans. Ask an administrator to link it.")
        if requested_staff_id not in (None, scope.staff_id):
            raise HTTPException(403, "You may only book loans against your own portfolio")
        return scope.staff_id

    staff_id = requested_staff_id if requested_staff_id is not None else client.officer_staff_id
    if staff_id is None:
        return None
    return require_staff(db, tenant_id, staff_id, scope).id
