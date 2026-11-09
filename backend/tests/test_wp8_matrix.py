"""
WP8 (review step 5) - role x permission x DCP acceptance matrix.

The routes are discovered from the application itself, so a new page is checked
automatically:

  1. Every GET page guarded by require_permission(...) is called by every
     built-in role. A role without the permission must get a clean 403
     ("Missing required permission"); a role with it must not; nothing may
     crash (no 500), whoever calls it.
  2. Every GET page that takes one record number is called by the
     administrator of ANOTHER DCP with this DCP's record numbers. The answer
     must never contain this DCP's client names, phone numbers, account
     numbers or staff e-mails.
"""
import inspect
import re
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.permissions import ROLE_PERMISSIONS, WILDCARD
from app.core.security import hash_password
from app.models import MODULE_KEYS, Tenant, TenantModule, User

ROLES = sorted(r for r in ROLE_PERMISSIONS if r != "super_admin")


def _permission_needs(dependant):
    """(keys, mode) of every require_permission(...) check under a route."""
    found = []
    for sub in dependant.dependencies:
        call = sub.call
        if getattr(call, "__qualname__", "").startswith("require_permission.<locals>"):
            nl = inspect.getclosurevars(call).nonlocals
            found.append((tuple(nl.get("keys", ())), nl.get("mode", "all")))
        found.extend(_permission_needs(sub))
    return found


def _walk(routes, prefix=""):
    for r in routes:
        if isinstance(r, APIRoute):
            yield prefix, r
        elif hasattr(r, "original_router"):          # FastAPI >= 0.13x keeps includes
            ctx = getattr(r, "include_context", None)
            yield from _walk(r.original_router.routes, prefix + (getattr(ctx, "prefix", "") or ""))


def _get_routes():
    """Every GET route as (full path, route)."""
    import app.main as main_mod
    seen = set()
    for prefix, r in _walk(main_mod.app.routes):
        path = prefix + r.path
        if "GET" in r.methods and path not in seen:
            seen.add(path)
            yield path, r


def _role_has(role, keys, mode):
    perms = ROLE_PERMISSIONS.get(role, set())
    if WILDCARD in perms:
        return True
    checks = [k in perms for k in keys]
    return all(checks) if mode == "all" else any(checks)


@pytest.fixture()
def m(seed, db):
    import app.main as main_mod
    A = seed.tenant
    for key in MODULE_KEYS:                     # every module on: test permissions only
        if not db.query(TenantModule).filter_by(tenant_id=A.id, module_key=key).first():
            db.add(TenantModule(tenant_id=A.id, module_key=key, enabled=True))
    B = Tenant(name="Rival DCP", code="RIV", active=True)
    db.add(B)
    db.flush()
    for key in MODULE_KEYS:
        db.add(TenantModule(tenant_id=B.id, module_key=key, enabled=True))
    pw = hash_password("Passw0rd!x")
    users = {}
    for role in ROLES:
        users[role] = User(email=f"{role}@a.dcp", hashed_password=pw, full_name=f"A {role}",
                           role=role, tenant_id=A.id, branch_id=seed.branch.id,
                           staff_id=seed.staff.id if role in ("relationship_officer",
                                                              "loan_officer", "call_agent") else None)
        db.add(users[role])
    rival = User(email="admin@rival.dcp", hashed_password=pw, full_name="Rival Admin",
                 role="tenant_admin", tenant_id=B.id)
    db.add(rival)
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app, raise_server_exceptions=False)

    yield SimpleNamespace(seed=seed, db=db, A=A, B=B, users=users, rival=rival, loan=loan, api=api)
    main_mod.app.dependency_overrides.clear()


def test_every_role_gets_exactly_what_its_permissions_allow(m):
    routes = [(path, _permission_needs(r.dependant)) for path, r in _get_routes()
              if "{" not in path]
    routes = [(path, needs) for path, needs in routes if needs]
    assert len(routes) >= 20, "route discovery found too few permission-guarded pages"
    problems = []
    for role in ROLES:
        api = m.api(m.users[role])
        for path, needs in routes:
            res = api.get(path, headers={"X-Tenant-Id": str(m.A.id)})
            allowed = all(_role_has(role, keys, mode) for keys, mode in needs)
            denied_for_permission = (res.status_code == 403
                                     and "Missing required permission" in res.text)
            if res.status_code >= 500:
                problems.append(f"{role:24} {path}: crashed ({res.status_code})")
            elif allowed and denied_for_permission:
                problems.append(f"{role:24} {path}: refused although the role has {needs}")
            elif not allowed and res.status_code != 403:
                problems.append(f"{role:24} {path}: answered {res.status_code} without {needs}")
    assert not problems, "\n" + "\n".join(problems)


def test_another_dcp_cannot_read_this_dcps_records(m):
    seed, db = m.seed, m.db
    borrower = seed.borrower
    secrets = [s for s in (borrower.full_name, borrower.phone, m.loan.account_number,
                           seed.maker.email) if s]
    ids = sorted({borrower.id, m.loan.id, seed.product.id, seed.branch.id, seed.staff.id,
                  seed.maker.id, m.A.id})
    api = m.api(m.rival)
    leaks = []
    checked = 0
    for route_path, _r in _get_routes():
        params = re.findall(r"{(\w+)}", route_path)
        if len(params) != 1:
            continue
        checked += 1
        for rid in ids:
            path = route_path.replace("{" + params[0] + "}", str(rid))
            res = api.get(path, headers={"X-Tenant-Id": str(m.B.id)})
            if res.status_code >= 500:
                leaks.append(f"{path}: crashed ({res.status_code})")
                continue
            if res.status_code == 200:
                body = res.text
                hit = [s for s in secrets if s and str(s) in body]
                if hit:
                    leaks.append(f"{path}: shows {hit}")
    assert checked >= 20, "route discovery found too few record pages"
    assert not leaks, "\n" + "\n".join(leaks)
