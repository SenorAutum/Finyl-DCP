"""Who may be approved and who may borrow (WP6, F20).

One place for the checks that the profile-approval, loan-application and
loan-approval boundaries all apply, so they cannot drift apart:

  * the central KYC decision (``kyc_decisions.evaluate``): ``fail`` blocks;
    ``escalate`` needs a person holding ``kyc.resolve`` to proceed with a note;
  * the product's own rules: age range, guarantor, next of kin, business age;
  * how many open loans a client may hold, set per product
    (``product.rules["max_open_loans"]``: 1 by default, 0 = no limit).
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Borrower, ClientNextOfKin, Guarantor, Loan, Product
from app.services import kyc_decisions
from app.services.authz import user_can

# A loan in any of these states still counts as the client's open loan.
OPEN_LOAN_STATUSES = ("pending", "underwriting", "approved", "active", "overdue",
                      "defaulted", "disbursed", "arrears")


def _reasons_text(decision) -> str:
    return " ".join(kyc_decisions.explain(decision.reasons))


def require_kyc(db: Session, *, tenant_id: int, client: Borrower, user=None,
                note: str | None = None, stage: str = "this step") -> kyc_decisions.KycDecision:
    """Raise unless the client's KYC decision allows ``stage``.

    ``escalate`` is allowed only when ``user`` holds ``kyc.resolve`` and gives a
    written ``note`` (the manual review is then on record)."""
    d = kyc_decisions.evaluate(db, tenant_id=tenant_id, client_id=client.id)
    if d.decision == kyc_decisions.DECISION_FAIL:
        raise HTTPException(409, f"KYC check failed, so {stage} is not allowed. "
                                 f"{_reasons_text(d)}")
    if d.decision == kyc_decisions.DECISION_ESCALATE:
        if not kyc_decisions.settings(db, tenant_id).allow_review_override:
            # This DCP chose: manual-review items must be fixed, not approved over.
            raise HTTPException(
                409, f"KYC needs manual review before {stage}. {_reasons_text(d)} "
                     "Your organisation requires these to be resolved first "
                     "(close the KYC escalation or re-read the ID).")
        can_resolve = user is not None and user_can(db, user, "kyc.resolve")
        if not (can_resolve and (note or "").strip()):
            raise HTTPException(
                409, f"KYC needs manual review before {stage}. {_reasons_text(d)} "
                     "A user with KYC-resolve rights can proceed with a written note.")
    return d


def _age(client: Borrower):
    return kyc_decisions._age_years(client.date_of_birth)


def require_product_rules(db: Session, *, tenant_id: int, client: Borrower,
                          product: Product, exclude_loan_id: int | None = None) -> None:
    rules = product.rules or {}
    problems = []

    age = _age(client)
    if product.min_age and (age is None or age < product.min_age):
        problems.append(f"the client must be at least {product.min_age} "
                        f"(age {'unknown' if age is None else age})")
    if product.max_age and age is not None and age > product.max_age:
        problems.append(f"the client must be at most {product.max_age} (age {age})")

    if product.requires_guarantor:
        g = (db.query(Guarantor.id)
             .filter(Guarantor.tenant_id == tenant_id, Guarantor.client_id == client.id,
                     Guarantor.active.is_(True), Guarantor.kyc_status == "validated").first())
        if g is None:
            problems.append("a validated guarantor is required")

    if product.requires_next_of_kin:
        n = (db.query(ClientNextOfKin.id)
             .filter(ClientNextOfKin.tenant_id == tenant_id,
                     ClientNextOfKin.client_id == client.id,
                     ClientNextOfKin.active.is_(True)).first())
        if n is None:
            problems.append("a next of kin is required")

    if getattr(product, "business_op_age_required", False):
        need = rules.get("min_business_age_months", 6)
        have = client.business_operational_age_months
        if have is None or have < need:
            problems.append(f"the business must have operated for at least {need} months")

    if problems:
        raise HTTPException(422, f"{product.name}: " + "; ".join(problems) + ".")

    limit = max_open_loans(product)
    if limit:
        q = (db.query(Loan.account_number)
             .filter(Loan.tenant_id == tenant_id, Loan.borrower_id == client.id,
                     Loan.status.in_(OPEN_LOAN_STATUSES)))
        if exclude_loan_id:
            q = q.filter(Loan.id != exclude_loan_id)
        others = [a for (a,) in q.all()]
        if len(others) >= limit:
            if limit == 1:
                raise HTTPException(409, f"The client already has an open loan "
                                         f"({others[0]}). {product.name} allows one "
                                         "open loan at a time.")
            raise HTTPException(409, f"The client already has {len(others)} open loans "
                                     f"({', '.join(others[:5])}). {product.name} allows "
                                     f"{limit} at a time.")


def max_open_loans(product: Product) -> int:
    """How many open loans a client may hold when applying for this product.
    Product setting ``rules.max_open_loans``: 1 by default, 0 = no limit.
    (The earlier ``allow_concurrent_loans: true`` also means no limit.)"""
    rules = product.rules or {}
    if "max_open_loans" in rules:
        try:
            return max(0, int(rules["max_open_loans"]))
        except (TypeError, ValueError):
            return 1
    return 0 if rules.get("allow_concurrent_loans") else 1
