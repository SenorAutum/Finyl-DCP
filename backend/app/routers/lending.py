"""
Lending Engine: borrower registry, product configuration, loan lifecycle.

Lifecycle: pending → underwriting → approved → active (auto-disburse via mock
Daraja B2C + SMS) → paid | overdue | defaulted. Rejection allowed pre-approval.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import (get_current_user, require_module, require_permission,
                           require_role, get_scope, UserScope, write_audit)
from app.models import (Borrower, Branch, ImpactSurvey, Loan, PaymentTransaction,
                        Product, Region, Repayment, Staff, User)
from app.schemas import (BorrowerCreate, LoanApplication, LoanStatusUpdate, ProductCreate,
                         ReassignRequest)
from app.routers.clients import _client_dict as _borrower_dict
from app.services import mpesa, sms
from fastapi import Request

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
    return {
        "regions": [{"id": r.id, "name": r.name} for r in regions],
        "branches": [{"id": b.id, "name": b.name, "region_id": b.region_id} for b in branches],
        "staff": [{"id": s.id, "name": s.name, "role": s.role, "branch_id": s.branch_id} for s in staff],
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
        q = q.filter(or_(Borrower.first_name.ilike(like), Borrower.last_name.ilike(like),
                         Borrower.national_id.ilike(like), Borrower.phone.ilike(like)))
    total = q.count()
    rows = q.order_by(Borrower.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "items": [_borrower_dict(b) for b in rows]}


@router.post("/borrowers")
def create_borrower(body: BorrowerCreate, tenant_id: int = Depends(require_module("lending")),
                    db: Session = Depends(get_db)):
    # INPUT-02: never mass-assign trust/lifecycle fields from client input.
    b = Borrower(tenant_id=tenant_id, **body.model_dump(exclude={"kyc_status"}))
    db.add(b)
    db.commit()
    return _borrower_dict(b)


@router.put("/borrowers/{borrower_id}")
def update_borrower(borrower_id: int, body: BorrowerCreate,
                    tenant_id: int = Depends(require_module("lending")),
                    db: Session = Depends(get_db)):
    b = db.query(Borrower).filter(Borrower.id == borrower_id, Borrower.tenant_id == tenant_id).first()
    if not b:
        raise HTTPException(404, "Borrower not found")
    # INPUT-02: kyc_status is a trust field — never settable via this endpoint.
    for k, v in body.model_dump(exclude={"kyc_status"}).items():
        setattr(b, k, v)
    db.commit()
    return _borrower_dict(b)


# ---------- Product configuration ---------------------------------------------------

@router.get("/products")
def list_products(tenant_id: int = Depends(require_module("lending")), db: Session = Depends(get_db)):
    rows = db.query(Product).filter(Product.tenant_id == tenant_id).order_by(Product.id).all()
    return [{
        "id": p.id, "name": p.name, "code": p.code, "interest_rate": p.interest_rate,
        "interest_method": p.interest_method, "tenure_value": p.tenure_value,
        "tenure_unit": p.tenure_unit, "repayment_frequency": p.repayment_frequency,
        "min_amount": float(p.min_amount), "max_amount": float(p.max_amount),
        "min_age": p.min_age, "max_age": p.max_age, "penalty_rate": p.penalty_rate,
        "rules": p.rules, "active": p.active,
    } for p in rows]


@router.post("/products")
def create_product(body: ProductCreate, tenant_id: int = Depends(require_module("lending")),
                   _: User = Depends(require_role("super_admin")),
                   db: Session = Depends(get_db)):
    p = Product(tenant_id=tenant_id, **body.model_dump())
    db.add(p)
    db.commit()
    return {"id": p.id}


@router.put("/products/{product_id}")
def update_product(product_id: int, body: ProductCreate,
                   tenant_id: int = Depends(require_module("lending")),
                   _: User = Depends(require_role("super_admin")),
                   db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id, Product.tenant_id == tenant_id).first()
    if not p:
        raise HTTPException(404, "Product not found")
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    db.commit()
    return {"ok": True}


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
                   user: User = Depends(require_permission("loans.create"))):
    borrower = db.query(Borrower).filter(Borrower.id == body.borrower_id,
                                         Borrower.tenant_id == tenant_id).first()
    if not borrower:
        raise HTTPException(404, "Borrower not found")
    product = db.query(Product).filter(Product.id == body.product_id,
                                       Product.tenant_id == tenant_id, Product.active).first()
    if not product:
        raise HTTPException(404, "Product not found or inactive")
    if not (float(product.min_amount) <= body.principal <= float(product.max_amount)):
        raise HTTPException(400, f"Amount must be between {product.min_amount} and {product.max_amount}")

    prior = db.query(Loan).filter(Loan.borrower_id == borrower.id).count()
    cycle = prior + 1

    # Impact survey gate: 2nd+ cycle borrowers must have a survey for this cycle
    if cycle >= 2:
        has_survey = (db.query(ImpactSurvey)
                      .filter(ImpactSurvey.borrower_id == borrower.id,
                              ImpactSurvey.loan_cycle_number == cycle).count() > 0)
        if not has_survey:
            raise HTTPException(428, "IMPACT_SURVEY_REQUIRED")  # 428 Precondition Required

    seq = db.query(Loan).filter(Loan.tenant_id == tenant_id).count() + 1
    loan = Loan(
        tenant_id=tenant_id,
        account_number=f"FL/FY{date.today().year}/{tenant_id}/{seq}",
        borrower_id=borrower.id, product_id=product.id,
        staff_id=body.staff_id, branch_id=borrower.branch_id,
        principal=body.principal, interest_rate=product.interest_rate,
        status="pending", application_date=date.today(), loan_cycle_number=cycle,
        outstanding_balance=0,
    )
    db.add(loan)
    db.commit()
    return _loan_dict(loan)


@router.post("/loans/{loan_id}/transition")
def transition_loan(loan_id: int, body: LoanStatusUpdate,
                    tenant_id: int = Depends(require_module("lending")),
                    db: Session = Depends(get_db),
                    scope: UserScope = Depends(get_scope),
                    request: Request = None):
    """Generic lifecycle transitions (e.g. active → paid/overdue/defaulted).

    NOTE: Loan APPROVAL is no longer performed here (it moved to the Approvals
    inbox at /api/v1/approvals with threshold + escalation enforcement) and
    DISBURSEMENT moved to /api/v1/payments/disburse (maker-checker). This
    endpoint therefore refuses the approve/disburse steps.
    """
    loan = (db.query(Loan).options(joinedload(Loan.borrower), joinedload(Loan.product))
            .filter(Loan.id == loan_id, Loan.tenant_id == tenant_id).first())
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

    prev = loan.status
    loan.status = body.status
    write_audit(db, tenant_id=tenant_id, user=scope.user, action="loan.transition",
                entity_type="loan", entity_id=loan.id,
                details={"from": prev, "to": body.status}, request=request)
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
