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
                         ReassignRequest, QuoteRequest)
from app.core.money import D, money
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



# ---------------------------------------------------------------------------
# Loan-origination pricing quote & suggested credit limit (read-only, advisory)
# ---------------------------------------------------------------------------
def _quote_breakdown(product: Product, principal) -> dict:
    """Decimal-accurate pricing preview for a prospective loan. No mutation.

    Fee configuration is read from ``product.rules`` (optional keys, default 0):
      - ``processing_fee_rate``: percent of principal
      - ``facility_fee``: flat amount
    Kenya excise duty is levied at 20% of total fees.
    """
    principal = money(principal)
    rate = D(product.interest_rate or 0)                 # % over the product tenure
    interest = money(principal * rate / D(100))

    rules = product.rules or {}
    try:
        proc_rate = D(str(rules.get("processing_fee_rate", 0) or 0))
    except Exception:
        proc_rate = D(0)
    try:
        facility_flat = D(str(rules.get("facility_fee", 0) or 0))
    except Exception:
        facility_flat = D(0)

    processing_fee = money(principal * proc_rate / D(100))
    facility_fee = money(facility_flat)
    total_fees = money(processing_fee + facility_fee)
    excise_duty = money(total_fees * D("0.20"))          # 20% excise on fees (Kenya)
    total_cost_of_credit = money(interest + total_fees + excise_duty)
    total_repayable = money(principal + total_cost_of_credit)

    # APR-style annualised effective rate over the product tenure
    tenure_value = max(1, int(product.tenure_value or 1))
    unit = (product.tenure_unit or "weeks").lower()
    if unit == "weeks":
        years = D(tenure_value) * D(7) / D(365)
    elif unit == "months":
        years = D(tenure_value) / D(12)
    else:
        years = D(tenure_value) / D(365)
    if years <= 0:
        years = D(1)
    effective_annual_rate = (money((total_cost_of_credit / principal) / years * D(100))
                             if principal > 0 else D(0))

    return {
        "product_id": product.id,
        "product_name": product.name,
        "principal": float(principal),
        "interest_rate": float(rate),
        "interest_method": product.interest_method,
        "tenure_value": tenure_value,
        "tenure_unit": product.tenure_unit,
        "interest": float(interest),
        "processing_fee": float(processing_fee),
        "facility_fee": float(facility_fee),
        "total_fees": float(total_fees),
        "excise_duty": float(excise_duty),
        "excise_rate": 0.20,
        "total_cost_of_credit": float(total_cost_of_credit),
        "total_repayable": float(total_repayable),
        "effective_annual_rate": float(effective_annual_rate),
    }


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
