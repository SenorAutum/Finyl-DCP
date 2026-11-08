"""
Lending Engine: borrower registry, product configuration, loan lifecycle.

Lifecycle: pending → underwriting → approved → active (auto-disburse via mock
Daraja B2C + SMS) → paid | overdue | defaulted. Rejection allowed pre-approval.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.core.crypto import pii_hash
from app.core.database import get_db
from app.core.deps import (get_current_user, require_module, require_permission,
                           require_role, get_scope, UserScope, write_audit)
from app.models import (Borrower, Branch, ImpactSurvey, Loan, LoanActiveLockLog,
                        PaymentTransaction, Product, ProductChangeRequest, Region, Repayment,
                        Staff, Tenant, User)
from app.schemas import (BorrowerCreate, LoanApplication, LoanStatusUpdate, ProductCreate,
                         ReassignRequest, QuoteRequest)
from app.core.money import D, money
from app.core import obs
from app.routers.clients import (_client_dict as _borrower_dict, LOCKED_FIELDS,
                                 MIN_ONBOARDING_AGE, _age_years)
from app.core.ownership import get_owned_optional, require_client, resolve_loan_officer
from app.services.authz import user_can
from app.services.onboarding import initial_profile_state
from app.services import mpesa, sms, client_edit, eligibility, kyc_decisions
from datetime import datetime, timezone
from fastapi import Request

# Loan statuses that keep a borrower record edit-locked (an obligation is live).
ACTIVE_LOAN_STATUSES = ("active", "disbursed", "arrears", "overdue")


def _release_active_lock(db: Session, loan: Loan, user) -> None:
    """Clear a loan's active-lock on a terminal transition, and unlock the borrower
    for editing when they have no other live obligation. Closes the open lock log."""
    loan.active_lock = False
    open_log = (db.query(LoanActiveLockLog)
                .filter(LoanActiveLockLog.loan_id == loan.id,
                        LoanActiveLockLog.unlock_at.is_(None))
                .order_by(LoanActiveLockLog.id.desc()).first())
    if open_log:
        open_log.unlock_at = datetime.now(timezone.utc)
        open_log.unlock_reason = f"loan->{loan.status}"
    # Only unlock the borrower if no other active loan keeps them locked.
    others = (db.query(Loan)
              .filter(Loan.tenant_id == loan.tenant_id,
                      Loan.borrower_id == loan.borrower_id, Loan.id != loan.id,
                      Loan.status.in_(ACTIVE_LOAN_STATUSES)).first())
    if not others and loan.borrower is not None:
        loan.borrower.edit_locked = False
        loan.borrower.edit_locked_reason = None

router = APIRouter(prefix="/api/v1/lending", tags=["lending"])

VALID_TRANSITIONS = {
    "pending": ["underwriting", "rejected"],
    "underwriting": ["approved", "rejected"],
    "approved": ["active"],           # via disbursement
    "active": ["paid", "overdue", "defaulted"],
    "overdue": ["paid", "defaulted", "active"],
}


# ---------- Org reference data (regions / branches / staff) --------------------

@router.get("/org")
def org_reference(tenant_id: int = Depends(require_module("lending")), db: Session = Depends(get_db)):
    regions = db.query(Region).filter(Region.tenant_id == tenant_id).all()
    branches = db.query(Branch).filter(Branch.tenant_id == tenant_id).all()
    staff = db.query(Staff).filter(Staff.tenant_id == tenant_id, Staff.active).all()
    # WP7 (review 7.11): people pickers for GPS trails, devices, field tasks and
    # promises-to-pay need USER ids; they were being given staff-record ids.
    users = (db.query(User).filter(User.tenant_id == tenant_id, User.active)
             .order_by(User.full_name).all())
    return {
        "regions": [{"id": r.id, "name": r.name} for r in regions],
        "branches": [{"id": b.id, "name": b.name, "region_id": b.region_id} for b in branches],
        "staff": [{"id": s.id, "name": s.name, "role": s.role, "branch_id": s.branch_id} for s in staff],
        "users": [{"id": u.id, "name": u.full_name or u.email, "role": u.role,
                   "staff_id": u.staff_id, "branch_id": u.branch_id} for u in users],
    }


# ---------- Client registry (legacy borrower paths — see routers/clients.py) ----

@router.get("/borrowers")
def list_borrowers(tenant_id: int = Depends(require_module("lending")),
                   db: Session = Depends(get_db), scope: UserScope = Depends(get_scope),
                   search: str = "", page: int = 1, page_size: int = 20):
    q = db.query(Borrower).filter(Borrower.tenant_id == tenant_id)
    q = scope.apply_client(q, Borrower)
    if search:
        like = f"%{search}%"
        # PII-02: national_id is encrypted at rest — match it via the blind index
        # (exact match on the full ID) instead of a substring ILIKE.
        q = q.filter(or_(Borrower.first_name.ilike(like), Borrower.last_name.ilike(like),
                         Borrower.phone.ilike(like),
                         Borrower.national_id_hash == pii_hash(search.strip())))
    total = q.count()
    rows = q.order_by(Borrower.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "items": [_borrower_dict(b) for b in rows]}


@router.post("/borrowers")
def create_borrower(body: BorrowerCreate, tenant_id: int = Depends(require_module("lending")),
                    db: Session = Depends(get_db),
                    user: User = Depends(require_permission("clients.create")),
                    request: Request = None):
    """Legacy alias of POST /clients. It MUST enforce the same rules as the
    canonical route (permission, age gate, maker-checker status, ownership)."""
    age = _age_years(body.date_of_birth)
    if age is not None and age < MIN_ONBOARDING_AGE:
        obs.log_cbk_event(obs.CBK_AGE_REJECTED, tenant_id=tenant_id, user_id=user.id,
                          entity_type="client", outcome="rejected", detail=f"age={age}")
        raise HTTPException(422, f"Applicant must be at least {MIN_ONBOARDING_AGE} "
                                 f"years old (computed age: {age}).")
    # Referenced org records must belong to this tenant (not just "exist").
    get_owned_optional(db, Region, body.region_id, tenant_id, label="Region")
    get_owned_optional(db, Branch, body.branch_id, tenant_id, label="Branch")

    profile_status, officer_staff_id = initial_profile_state(db, user)
    # INPUT-02: never mass-assign trust/lifecycle fields from client input.
    b = Borrower(tenant_id=tenant_id, **body.model_dump(exclude={"kyc_status"}),
                 profile_status=profile_status, officer_staff_id=officer_staff_id,
                 onboarded_by=user.full_name, created_by_user_id=user.id)
    if age is not None:
        b.date_of_birth_verified = True
        b.age_verified_at = datetime.utcnow()
    db.add(b)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="client.create",
                entity_type="client", entity_id=b.id,
                details={"profile_status": profile_status, "via": "legacy_borrowers"},
                request=request)
    db.commit()
    return _borrower_dict(b)


@router.put("/borrowers/{borrower_id}")
def update_borrower(borrower_id: int, body: BorrowerCreate,
                    tenant_id: int = Depends(require_module("lending")),
                    db: Session = Depends(get_db),
                    user: User = Depends(require_permission("clients.edit")),
                    scope: UserScope = Depends(get_scope)):
    b = require_client(db, tenant_id, borrower_id, scope)   # tenant + portfolio scope
    get_owned_optional(db, Region, body.region_id, tenant_id, label="Region")
    get_owned_optional(db, Branch, body.branch_id, tenant_id, label="Branch")
    # Record-level lock (Phase 2): when the borrower is edit-locked (active loan)
    # their locked identity fields are frozen and must go through the maker-checker
    # edit-request workflow rather than this direct PUT.
    changed = [f for f in LOCKED_FIELDS
               if str(getattr(body, f, None) or "") != str(getattr(b, f, None) or "")]
    # Field-level lock: primary identity fields need clients.edit_locked (effective grant).
    if changed and not user_can(db, user, "clients.edit_locked"):
        raise HTTPException(422, "You are not permitted to change locked field(s): "
                                 f"{', '.join(changed)}")
    record_locked = bool(getattr(b, "edit_locked", False)) or \
        client_edit.has_active_loan(db, tenant_id=tenant_id, client_id=b.id)
    if record_locked:
        if changed:
            obs.log_cbk_event(obs.CBK_EDIT_LOCK_ENFORCED, tenant_id=tenant_id,
                              entity_type="borrower", entity_id=b.id, outcome="blocked",
                              detail=",".join(changed))
            raise HTTPException(
                409, "Borrower is edit-locked (active loan). Locked field(s) "
                     f"{', '.join(changed)} require an approved edit request.")
    # INPUT-02: kyc_status is a trust field — never settable via this endpoint.
    for k, v in body.model_dump(exclude={"kyc_status"}).items():
        setattr(b, k, v)
    db.commit()
    return _borrower_dict(b)


# ---------- Product configuration ---------------------------------------------------

LOAN_CATEGORIES = ["personal", "business", "secured", "unsecured"]


def _product_dict(p: Product) -> dict:
    return {
        "id": p.id, "name": p.name, "code": p.code, "interest_rate": p.interest_rate,
        "interest_method": p.interest_method, "tenure_value": p.tenure_value,
        "tenure_unit": p.tenure_unit, "repayment_frequency": p.repayment_frequency,
        "min_amount": float(p.min_amount), "max_amount": float(p.max_amount),
        "min_age": p.min_age, "max_age": p.max_age, "penalty_rate": p.penalty_rate,
        "rules": p.rules, "active": p.active,
        # Phase 2 — product-level requirement flags.
        "loan_category": p.loan_category,
        "requires_guarantor": bool(p.requires_guarantor),
        "requires_collateral": bool(p.requires_collateral),
        "requires_next_of_kin": bool(p.requires_next_of_kin),
        "business_op_age_required": bool(getattr(p, "business_op_age_required", False)),
    }


@router.get("/products")
def list_products(tenant_id: int = Depends(require_module("lending")),
                  db: Session = Depends(get_db),
                  loan_category: str = Query("", description="Filter by loan_category"),
                  active_only: bool = Query(False)):
    q = db.query(Product).filter(Product.tenant_id == tenant_id)
    if loan_category:
        if loan_category not in LOAN_CATEGORIES:
            raise HTTPException(400, f"Invalid loan_category. Valid: {LOAN_CATEGORIES}")
        q = q.filter(Product.loan_category == loan_category)
    if active_only:
        q = q.filter(Product.active.is_(True))
    rows = q.order_by(Product.id).all()
    # WP7: show a change waiting for Finyl approval on its product.
    pending = {r.product_id: r for r in db.query(ProductChangeRequest).filter(
        ProductChangeRequest.tenant_id == tenant_id,
        ProductChangeRequest.status == "pending",
        ProductChangeRequest.product_id.isnot(None))}
    out = []
    for p in rows:
        d = _product_dict(p)
        r = pending.get(p.id)
        d["pending_change"] = ({"request_id": r.id, "proposed": r.proposed,
                                "requested_at": r.requested_at} if r else None)
        out.append(d)
    return out


# --- Products: the DCP proposes, Finyl approves (WP7) ----------------------------
# A Finyl super admin changes products directly. A DCP administrator (with
# products.manage) PROPOSES a new product or a change; it goes live only when a
# Finyl super admin approves it, because pricing changes need CBK approval.
# Pausing a product (switching it off) needs no approval: it only stops new
# lending. Every change, direct or approved, is in the audit trail.
_PRODUCT_FIELDS = set(ProductCreate.model_fields)


def _check_product_values(values: dict) -> None:
    if values.get("min_age") is not None and values["min_age"] < 18:
        raise HTTPException(422, "Minimum age cannot be below 18")
    lo, hi = values.get("min_amount"), values.get("max_amount")
    if lo is not None and hi is not None and float(lo) > float(hi):
        raise HTTPException(422, "Minimum amount cannot be above the maximum amount")
    for k in ("interest_rate", "penalty_rate"):
        if values.get(k) is not None and float(values[k]) < 0:
            raise HTTPException(422, f"{k.replace('_', ' ')} cannot be negative")


def _jsonable(v):
    from decimal import Decimal
    if isinstance(v, Decimal):
        return float(v)
    return v


def _request_dict(r: ProductChangeRequest, db: Session) -> dict:
    who = db.get(User, r.requested_by)
    decider = db.get(User, r.decided_by) if r.decided_by else None
    prod = db.get(Product, r.product_id) if r.product_id else None
    return {"id": r.id, "action": r.action, "status": r.status,
            "product_id": r.product_id,
            "product_name": prod.name if prod else (r.proposed or {}).get("name"),
            "proposed": r.proposed, "current_values": r.current_values,
            "reason": r.reason,
            "requested_by": who.full_name if who else r.requested_by,
            "requested_at": r.requested_at,
            "decided_by": decider.full_name if decider else None,
            "decided_at": r.decided_at, "decision_note": r.decision_note}


def _require_products_manage(db: Session, user: User) -> None:
    if not user_can(db, user, "products.manage"):
        raise HTTPException(403, "Missing required permission: products.manage")


@router.post("/products")
def create_product(body: ProductCreate, tenant_id: int = Depends(require_module("lending")),
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db), request: Request = None):
    values = body.model_dump()
    _check_product_values(values)
    if user.role == "super_admin":
        p = Product(tenant_id=tenant_id, **values)
        db.add(p)
        db.flush()
        write_audit(db, tenant_id=tenant_id, user=user, action="product.create",
                    entity_type="product", entity_id=p.id,
                    details={k: _jsonable(v) for k, v in values.items()}, request=request)
        db.commit()
        return {"id": p.id, "status": "live"}
    _require_products_manage(db, user)
    r = ProductChangeRequest(tenant_id=tenant_id, action="create",
                             proposed={k: _jsonable(v) for k, v in values.items()},
                             requested_by=user.id, status="pending")
    db.add(r)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="product.propose_create",
                entity_type="product_request", entity_id=r.id,
                details={"name": values.get("name")}, request=request)
    db.commit()
    return {"status": "pending_approval", "request_id": r.id,
            "message": "Sent to Finyl for approval. The product goes live once approved."}


@router.put("/products/{product_id}")
def update_product(product_id: int, body: ProductCreate,
                   tenant_id: int = Depends(require_module("lending")),
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db), request: Request = None):
    p = db.query(Product).filter(Product.id == product_id, Product.tenant_id == tenant_id).first()
    if not p:
        raise HTTPException(404, "Product not found")
    # Only the fields the caller sent: an older screen that does not know the
    # eligibility switches must not reset them to their defaults.
    sent = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in sent.items()
               if _jsonable(getattr(p, k, None)) != _jsonable(v)}
    _check_product_values({k: changes.get(k, getattr(p, k, None))
                           for k in ("min_age", "min_amount", "max_amount",
                                     "interest_rate", "penalty_rate")})
    if not changes:
        return {"ok": True, "status": "unchanged"}
    before = {k: _jsonable(getattr(p, k, None)) for k in changes}

    pause_only = set(changes) == {"active"} and changes["active"] is False
    if user.role == "super_admin" or (pause_only and user_can(db, user, "products.manage")):
        for k, v in changes.items():
            setattr(p, k, v)
        write_audit(db, tenant_id=tenant_id, user=user,
                    action="product.pause" if pause_only else "product.update",
                    entity_type="product", entity_id=p.id,
                    details={"before": before,
                             "after": {k: _jsonable(v) for k, v in changes.items()}},
                    request=request)
        db.commit()
        return {"ok": True, "status": "live"}

    _require_products_manage(db, user)
    if db.query(ProductChangeRequest).filter(
            ProductChangeRequest.product_id == p.id,
            ProductChangeRequest.status == "pending").first():
        raise HTTPException(409, "This product already has a change waiting for Finyl "
                                 "approval. Withdraw it first, or wait for the decision.")
    r = ProductChangeRequest(tenant_id=tenant_id, product_id=p.id, action="update",
                             proposed={k: _jsonable(v) for k, v in changes.items()},
                             current_values=before, requested_by=user.id, status="pending")
    db.add(r)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="product.propose_update",
                entity_type="product_request", entity_id=r.id,
                details={"product_id": p.id, "fields": sorted(changes)}, request=request)
    db.commit()
    return {"status": "pending_approval", "request_id": r.id,
            "message": "Sent to Finyl for approval. The change applies once approved."}


@router.get("/product-requests")
def list_product_requests(tenant_id: int = Depends(require_module("lending")),
                          user: User = Depends(get_current_user),
                          db: Session = Depends(get_db), status: str = "pending"):
    if user.role != "super_admin":
        _require_products_manage(db, user)
    q = db.query(ProductChangeRequest).filter(ProductChangeRequest.tenant_id == tenant_id)
    if status != "all":
        q = q.filter(ProductChangeRequest.status == status)
    rows = q.order_by(ProductChangeRequest.requested_at.desc()).limit(200).all()
    return [_request_dict(r, db) for r in rows]


def _open_request(db: Session, tenant_id: int, request_id: int) -> ProductChangeRequest:
    r = (db.query(ProductChangeRequest)
         .filter(ProductChangeRequest.id == request_id,
                 ProductChangeRequest.tenant_id == tenant_id)
         .with_for_update().first())
    if not r:
        raise HTTPException(404, "Request not found")
    if r.status != "pending":
        raise HTTPException(409, f"This request is already {r.status}")
    return r


@router.post("/product-requests/{request_id}/approve")
def approve_product_request(request_id: int, body: dict | None = None,
                            tenant_id: int = Depends(require_module("lending")),
                            user: User = Depends(require_role("super_admin")),
                            db: Session = Depends(get_db), request: Request = None):
    r = _open_request(db, tenant_id, request_id)
    values = dict(r.proposed or {})
    unknown = set(values) - _PRODUCT_FIELDS
    if unknown:
        raise HTTPException(422, f"Unknown product fields: {sorted(unknown)}")
    if r.action == "create":
        _check_product_values(values)
        p = Product(tenant_id=tenant_id, **ProductCreate(**values).model_dump())
        db.add(p)
        db.flush()
        r.product_id = p.id
    else:
        p = db.query(Product).filter(Product.id == r.product_id,
                                     Product.tenant_id == tenant_id).first()
        if not p:
            raise HTTPException(404, "The product no longer exists")
        _check_product_values({k: values.get(k, getattr(p, k, None))
                               for k in ("min_age", "min_amount", "max_amount",
                                         "interest_rate", "penalty_rate")})
        for k, v in values.items():
            setattr(p, k, v)
    r.status = "approved"
    r.decided_by = user.id
    r.decided_at = datetime.utcnow()
    r.decision_note = ((body or {}).get("note") or "").strip()[:1000] or None
    write_audit(db, tenant_id=tenant_id, user=user, action="product.approve_" + r.action,
                entity_type="product", entity_id=p.id,
                details={"request_id": r.id, "applied": values}, request=request)
    db.commit()
    return _request_dict(r, db)


@router.post("/product-requests/{request_id}/reject")
def reject_product_request(request_id: int, body: dict,
                           tenant_id: int = Depends(require_module("lending")),
                           user: User = Depends(require_role("super_admin")),
                           db: Session = Depends(get_db), request: Request = None):
    note = ((body or {}).get("note") or "").strip()
    if not note:
        raise HTTPException(422, "Give a reason so the DCP knows what to change")
    r = _open_request(db, tenant_id, request_id)
    r.status = "rejected"
    r.decided_by = user.id
    r.decided_at = datetime.utcnow()
    r.decision_note = note[:1000]
    write_audit(db, tenant_id=tenant_id, user=user, action="product.reject_" + r.action,
                entity_type="product_request", entity_id=r.id,
                details={"note": note[:300]}, request=request)
    db.commit()
    return _request_dict(r, db)


@router.post("/product-requests/{request_id}/withdraw")
def withdraw_product_request(request_id: int,
                             tenant_id: int = Depends(require_module("lending")),
                             user: User = Depends(get_current_user),
                             db: Session = Depends(get_db), request: Request = None):
    if user.role != "super_admin":
        _require_products_manage(db, user)
    r = _open_request(db, tenant_id, request_id)
    r.status = "withdrawn"
    r.decided_by = user.id
    r.decided_at = datetime.utcnow()
    write_audit(db, tenant_id=tenant_id, user=user, action="product.withdraw_" + r.action,
                entity_type="product_request", entity_id=r.id, request=request)
    db.commit()
    return _request_dict(r, db)


# ---------- Loans -------------------------------------------------------------------------

def _loan_dict(l: Loan) -> dict:
    return {
        "id": l.id, "account_number": l.account_number,
        "borrower_id": l.borrower_id, "borrower_name": l.borrower.full_name if l.borrower else None,
        "borrower_phone": l.borrower.phone if l.borrower else None,
        "product_id": l.product_id, "product_name": l.product.name if l.product else None,
        "staff_id": l.staff_id, "staff_name": l.staff.name if l.staff else None,
        "branch_id": l.branch_id, "principal": float(l.principal),
        "interest_rate": l.interest_rate, "status": l.status,
        "application_date": l.application_date, "approval_date": l.approval_date,
        "disbursement_date": l.disbursement_date, "due_date": l.due_date,
        "outstanding_balance": float(l.outstanding_balance or 0),
        "total_due": round(l.total_due, 2), "loan_cycle_number": l.loan_cycle_number,
        "escalation_level": getattr(l, "escalation_level", None),
        "approved_by_user_id": getattr(l, "approved_by_user_id", None),
        "decision_note": getattr(l, "decision_note", None),
        "active_lock": bool(getattr(l, "active_lock", False)),
        "interest_rebate": float(getattr(l, "interest_rebate", 0) or 0),
        "fees_in_balance": float(getattr(l, "fees_in_balance", 0) or 0),
        "pricing": getattr(l, "pricing_snapshot", None),
        "early_settled_at": getattr(l, "early_settled_at", None),
    }


@router.get("/loans")
def list_loans(tenant_id: int = Depends(require_module("lending")), db: Session = Depends(get_db),
               scope: UserScope = Depends(get_scope),
               status: str = "", search: str = "", page: int = 1, page_size: int = 20):
    q = (db.query(Loan).options(joinedload(Loan.borrower), joinedload(Loan.product), joinedload(Loan.staff))
         .filter(Loan.tenant_id == tenant_id))
    q = scope.apply_loan(q, Loan)
    if status:
        q = q.filter(Loan.status == status)
    if search:
        like = f"%{search}%"
        q = q.join(Borrower).filter(or_(Loan.account_number.ilike(like),
                                        Borrower.first_name.ilike(like), Borrower.last_name.ilike(like)))
    total = q.count()
    rows = q.order_by(Loan.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "items": [_loan_dict(l) for l in rows]}


@router.get("/loans/{loan_id}/payoff")
def loan_payoff(loan_id: int, on: date | None = None,
                tenant_id: int = Depends(require_module("lending")),
                db: Session = Depends(get_db), scope: UserScope = Depends(get_scope)):
    """What the borrower must pay to clear the loan today (or on `on`).

    Before the due date only interest earned so far is payable (CBK LN 191/2026
    reg. 56(4)); the rest of the term's interest is shown as `rebate` and is
    written off automatically when a payment of at least `payoff_amount` arrives."""
    from app.services import repayments as rp
    l = (db.query(Loan).options(joinedload(Loan.product))
         .filter(Loan.id == loan_id, Loan.tenant_id == tenant_id).first())
    if not l:
        raise HTTPException(404, "Loan not found")
    if not scope.can_see_loan(l):
        raise HTTPException(403, "Loan is outside your data scope")
    return {"loan_id": l.id, "account_number": l.account_number, "status": l.status,
            "due_date": l.due_date, **rp.payoff_quote(db, l, on)}


@router.get("/loans/{loan_id}")
def loan_detail(loan_id: int, tenant_id: int = Depends(require_module("lending")),
                db: Session = Depends(get_db), scope: UserScope = Depends(get_scope)):
    l = (db.query(Loan).options(joinedload(Loan.borrower), joinedload(Loan.product),
                                joinedload(Loan.staff), joinedload(Loan.repayments))
         .filter(Loan.id == loan_id, Loan.tenant_id == tenant_id).first())
    if not l:
        raise HTTPException(404, "Loan not found")
    if not scope.can_see_loan(l):
        raise HTTPException(403, "Loan is outside your data scope")
    d = _loan_dict(l)
    d["borrower"] = _borrower_dict(l.borrower)
    d["repayments"] = [{
        "id": r.id, "amount": float(r.amount), "principal_component": float(r.principal_component or 0),
        "interest_component": float(r.interest_component or 0), "payment_date": r.payment_date,
        "method": r.method, "mpesa_ref": r.mpesa_ref,
    } for r in sorted(l.repayments, key=lambda r: r.payment_date, reverse=True)]
    # Simple flat-interest schedule for display
    if l.product and l.disbursement_date:
        n = max(1, l.product.tenure_value)
        installment = round(l.total_due / n, 2)
        step = 7 if l.product.tenure_unit == "weeks" else 30
        d["schedule"] = [{"n": i + 1,
                          "due_date": l.disbursement_date + timedelta(days=step * (i + 1)),
                          "amount": installment} for i in range(n)]
    else:
        d["schedule"] = []
    return d


@router.post("/loans/apply")
def apply_for_loan(body: LoanApplication, tenant_id: int = Depends(require_module("lending")),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_permission("loans.create")),
                   scope: UserScope = Depends(get_scope)):
    # Tenant + portfolio scope, and a row lock so concurrent applications for
    # the same client cannot both read the same loan-cycle number.
    borrower = require_client(db, tenant_id, body.borrower_id, scope, lock=True)
    # Never trust a caller-supplied officer id: derive / validate it.
    staff_id = resolve_loan_officer(db, tenant_id, scope, borrower, body.staff_id)
    if borrower.profile_status != "approved":
        raise HTTPException(
            422,
            f"Client profile is '{borrower.profile_status}'. Only approved clients may apply for a loan."
        )
    product = db.query(Product).filter(Product.id == body.product_id,
                                       Product.tenant_id == tenant_id, Product.active).first()
    if not product:
        raise HTTPException(404, "Product not found or inactive")
    if not (float(product.min_amount) <= body.principal <= float(product.max_amount)):
        raise HTTPException(400, f"Amount must be between {product.min_amount} and {product.max_amount}")
    # WP6 (F20): an approved profile is not enough. The client must pass the
    # central KYC decision NOW and meet the product's own rules. ("escalate"
    # may apply; it is decided by a person at loan approval.)
    d = kyc_decisions.evaluate(db, tenant_id=tenant_id, client_id=borrower.id)
    if d.decision == kyc_decisions.DECISION_FAIL:
        raise HTTPException(409, "KYC check failed, so this client cannot apply. "
                                 + " ".join(kyc_decisions.explain(d.reasons)))
    eligibility.require_product_rules(db, tenant_id=tenant_id, client=borrower, product=product)

    prior = db.query(Loan).filter(Loan.borrower_id == borrower.id).count()
    cycle = prior + 1

    # Impact survey gate: 2nd+ cycle borrowers must have a survey for this cycle
    if cycle >= 2:
        has_survey = (db.query(ImpactSurvey)
                      .filter(ImpactSurvey.borrower_id == borrower.id,
                              ImpactSurvey.loan_cycle_number == cycle).count() > 0)
        if not has_survey:
            raise HTTPException(428, "IMPACT_SURVEY_REQUIRED")  # 428 Precondition Required

    # Serialise account-number generation per tenant, and never reuse a number:
    # count()+1 alone repeats a number once any loan row is removed, and loan
    # account numbers are what borrowers type at the paybill. Migration 032 adds
    # a unique index on (tenant_id, account_number) as the database backstop.
    db.query(Tenant).filter(Tenant.id == tenant_id).with_for_update().first()
    seq = db.query(Loan).filter(Loan.tenant_id == tenant_id).count() + 1
    while True:
        account_number = f"FL/FY{date.today().year}/{tenant_id}/{seq}"
        taken = (db.query(Loan.id).filter(Loan.tenant_id == tenant_id,
                                          Loan.account_number == account_number).first())
        if taken is None:
            break
        seq += 1
    loan = Loan(
        tenant_id=tenant_id,
        account_number=account_number,
        borrower_id=borrower.id, product_id=product.id,
        staff_id=staff_id, branch_id=borrower.branch_id,
        principal=body.principal, interest_rate=product.interest_rate,
        status="pending", application_date=date.today(), loan_cycle_number=cycle,
        outstanding_balance=0,
        # WP5 (F23): the price shown to the borrower now, kept for payout/balance.
        pricing_snapshot=_quote_breakdown(product, body.principal),
    )
    db.add(loan)
    db.commit()
    return _loan_dict(loan)


@router.post("/loans/{loan_id}/transition")
def transition_loan(loan_id: int, body: LoanStatusUpdate,
                    tenant_id: int = Depends(require_module("lending")),
                    db: Session = Depends(get_db),
                    scope: UserScope = Depends(get_scope),
                    user: User = Depends(require_permission("loans.transition")),
                    request: Request = None):
    """Generic lifecycle transitions (e.g. active → paid/overdue/defaulted).

    Requires ``loans.transition`` (credit-risk roles only; HQ operations is
    read-only). ``paid`` is only accepted when the loan carries NO outstanding
    balance, so this endpoint can never write a debt off the books by status.

    NOTE: Loan APPROVAL is no longer performed here (it moved to the Approvals
    inbox at /api/v1/approvals with threshold + escalation enforcement) and
    DISBURSEMENT moved to /api/v1/payments/disburse (maker-checker). This
    endpoint therefore refuses the approve/disburse steps.
    """
    # Row-lock the loan so a concurrent repayment/settlement cannot interleave
    # with the balance check below (no joinedload: FOR UPDATE can't span an outer join).
    loan = (db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == tenant_id)
            .with_for_update().first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    if not scope.can_see_loan(loan):
        raise HTTPException(403, "Loan is outside your data scope")
    allowed = VALID_TRANSITIONS.get(loan.status, [])
    if body.status not in allowed:
        raise HTTPException(400, f"Cannot move loan from '{loan.status}' to '{body.status}'. Allowed: {allowed}")
    if body.status == "approved":
        raise HTTPException(409, "Use the Approvals inbox (/api/v1/approvals/loans) to approve loans.")
    if body.status == "active":
        raise HTTPException(409, "Use disbursement (/api/v1/payments/disburse) to activate an approved loan.")

    if body.status == "paid":
        balance = money(loan.outstanding_balance)
        if balance > 0:
            raise HTTPException(
                409, f"Loan has an outstanding balance of KES {balance}. A loan can only be "
                     "marked paid once the balance is zero; record the repayment instead.")

    prev = loan.status
    loan.status = body.status
    # Active-lock lifecycle: a settled loan releases the borrower edit-lock; a loan
    # that goes overdue/defaulted stays locked (the obligation is still live).
    if body.status in ("paid", "closed", "written_off"):
        _release_active_lock(db, loan, scope.user)
        obs.log_cbk_event(obs.CBK_LOAN_ACTIVE_LOCK, tenant_id=tenant_id,
                          user_id=getattr(scope.user, "id", None),
                          entity_type="loan", entity_id=loan.id, outcome="released")
    write_audit(db, tenant_id=tenant_id, user=scope.user, action="loan.transition",
                entity_type="loan", entity_id=loan.id,
                details={"from": prev, "to": body.status,
                         "active_lock": loan.active_lock}, request=request)
    # Notify the borrower when a loan is moved to the defaulted status.
    if body.status == "defaulted":
        try:
            if loan.borrower:
                sms.sms_defaulted(db, tenant_id, loan.borrower, loan)
        except Exception:
            pass
    db.commit()
    return _loan_dict(loan)


@router.post("/loans/{loan_id}/reassign")
def reassign_loan(loan_id: int, body: ReassignRequest,
                  tenant_id: int = Depends(require_module("lending")),
                  db: Session = Depends(get_db),
                  user: User = Depends(require_permission("loans.reassign")),
                  scope: UserScope = Depends(get_scope),
                  request: Request = None):
    """Reassign a loan to another officer (branch/regional managers)."""
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == tenant_id).first()
    if not loan:
        raise HTTPException(404, "Loan not found")
    if not scope.can_see_loan(loan):
        raise HTTPException(403, "Loan is outside your data scope")
    staff = db.query(Staff).filter(Staff.id == body.staff_id, Staff.tenant_id == tenant_id).first()
    if not staff:
        raise HTTPException(404, "Target officer not found")
    prev = loan.staff_id
    loan.staff_id = body.staff_id
    write_audit(db, tenant_id=tenant_id, user=user, action="loan.reassign",
                entity_type="loan", entity_id=loan.id,
                details={"from_staff_id": prev, "to_staff_id": body.staff_id,
                         "reason": body.reason}, request=request)
    db.commit()
    return _loan_dict(loan)



# ---------------------------------------------------------------------------
# Loan-origination pricing quote & suggested credit limit (read-only, advisory)
# ---------------------------------------------------------------------------
def _quote_breakdown(product: Product, principal) -> dict:
    """Pricing preview — WP5: the same calculation that sets the loan's payout
    and balance (services/pricing.py), so the quote is what gets charged."""
    from app.services.pricing import quote_breakdown
    return quote_breakdown(product, principal)


def _suggested_limit(db: Session, tenant_id: int, borrower_id: int, product: Product) -> dict:
    """Advisory credit limit derived from borrower repayment history.

    No history -> conservative base from the product's amount band. With history,
    the borrower's largest prior principal is scaled by an on-time performance
    multiplier and clamped to the product's [min, max] band.
    """
    pmin = D(product.min_amount or 0)
    pmax = D(product.max_amount or 0)

    prior_loans = (db.query(Loan)
                   .filter(Loan.tenant_id == tenant_id,
                           Loan.borrower_id == borrower_id).all())
    paid = [l for l in prior_loans if l.status == "paid"]
    active = [l for l in prior_loans if l.status in ("active", "overdue", "defaulted")]
    current_exposure = money(sum((D(l.outstanding_balance or 0) for l in active), D(0)))
    total_prior = len(prior_loans)
    max_prior = max((D(l.principal or 0) for l in prior_loans), default=D(0))

    on_time = 0
    for l in paid:
        reps = list(l.repayments or [])
        if l.due_date and reps:
            last = max(r.payment_date for r in reps)
            last_date = last.date() if hasattr(last, "date") else last
            if last_date <= l.due_date:
                on_time += 1
        else:
            on_time += 1
    on_time_ratio = (on_time / len(paid)) if paid else None

    if total_prior == 0:
        suggested = pmin
        basis = "product_default"
        rationale = "No repayment history; limit set to the product minimum."
    else:
        if on_time_ratio is None:
            multiplier = D("1.0")
        elif on_time_ratio >= D("0.9"):
            multiplier = D("1.5")
        elif on_time_ratio >= D("0.7"):
            multiplier = D("1.25")
        elif on_time_ratio >= D("0.5"):
            multiplier = D("1.0")
        else:
            multiplier = D("0.75")
        suggested = money(max_prior * multiplier)
        basis = "history"
        rationale = (f"Based on {total_prior} prior loan(s), {len(paid)} fully repaid; "
                     f"largest prior principal {float(max_prior)} scaled by on-time "
                     f"performance multiplier {float(multiplier)}.")

    # Clamp to the product amount band
    if pmax > 0:
        suggested = min(suggested, pmax)
    suggested = max(suggested, pmin)
    suggested = money(suggested)
    headroom = money(max(D(0), suggested - current_exposure))

    return {
        "borrower_id": borrower_id,
        "product_id": product.id,
        "suggested_limit": float(suggested),
        "current_exposure": float(current_exposure),
        "available_headroom": float(headroom),
        "prior_loan_count": total_prior,
        "fully_repaid_count": len(paid),
        "active_loan_count": len(active),
        "max_prior_principal": float(max_prior),
        "on_time_ratio": (round(on_time_ratio, 3) if on_time_ratio is not None else None),
        "product_min": float(pmin),
        "product_max": float(pmax),
        "basis": basis,
        "rationale": rationale,
    }


@router.post("/quote")
def pricing_quote(body: QuoteRequest, tenant_id: int = Depends(require_module("lending")),
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Read-only origination pricing preview. Does not create or modify any loan."""
    product = (db.query(Product)
               .filter(Product.id == body.product_id, Product.tenant_id == tenant_id).first())
    if not product:
        raise HTTPException(404, "Product not found")
    if not (float(product.min_amount) <= body.principal <= float(product.max_amount)):
        raise HTTPException(400, f"Amount must be between {product.min_amount} and {product.max_amount}")

    result = {"quote": _quote_breakdown(product, body.principal)}
    if body.borrower_id is not None:
        borrower = (db.query(Borrower)
                    .filter(Borrower.id == body.borrower_id,
                            Borrower.tenant_id == tenant_id).first())
        if not borrower:
            raise HTTPException(404, "Borrower not found")
        result["suggested_limit"] = _suggested_limit(db, tenant_id, borrower.id, product)
    return result


@router.get("/suggested-limit")
def suggested_limit(borrower_id: int = Query(...), product_id: int = Query(None),
                    tenant_id: int = Depends(require_module("lending")),
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Advisory credit limit for a borrower. Read-only."""
    borrower = (db.query(Borrower)
                .filter(Borrower.id == borrower_id, Borrower.tenant_id == tenant_id).first())
    if not borrower:
        raise HTTPException(404, "Borrower not found")
    if product_id is not None:
        product = (db.query(Product)
                   .filter(Product.id == product_id, Product.tenant_id == tenant_id).first())
        if not product:
            raise HTTPException(404, "Product not found")
    else:
        product = (db.query(Product)
                   .filter(Product.tenant_id == tenant_id, Product.active)
                   .order_by(Product.id.asc()).first())
        if not product:
            raise HTTPException(404, "No active product configured")
    return _suggested_limit(db, tenant_id, borrower.id, product)
