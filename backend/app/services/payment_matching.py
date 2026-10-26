"""
WP2 — turn what a payer typed as the paybill "Account Number" into a loan,
strictly INSIDE one DCP.

Each DCP has its own paybill, so the paybill shortcode on the callback decides
the DCP first (services/webhook_security.shortcode_to_tenant); this module then
only ever looks at that DCP's borrowers and loans.

Matching order:
  1. Loan account number (e.g. FL/FY2026/33/38), case/space-insensitive.
  2. Customer number (7 digits with a valid check digit) -> that customer's
     one open loan. If the customer has no open loan, or more than one, the
     money is NOT guessed onto a loan: it goes to the review queue.
  3. Nothing matched -> review queue. If the paying phone number belongs to a
     customer of this DCP with one open loan, that loan is recorded as a
     SUGGESTION for the reviewer. A phone match is never applied automatically:
     people pay for relatives, and Safaricom may mask the number.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func

from app.core import customer_number as cn
from app.models import Borrower, Loan
from app.services.repayments import COLLECTIBLE_STATUSES


@dataclass
class Match:
    loan: Loan | None = None            # apply automatically to this loan
    how: str = "none"                   # account_number | customer_number | none
    reason: str | None = None           # why no automatic match (suspense reason)
    suggested_loan_id: int | None = None
    borrower_id: int | None = None


def _clean_ref(bill_ref) -> str:
    return "".join(str(bill_ref or "").split()).upper()


def phone_variants(phone) -> list[str]:
    """The formats a Kenyan number may be stored in (07.., 2547.., +2547..)."""
    from app.services.mpesa import normalise_msisdn
    n = normalise_msisdn(phone or "")
    if len(n) != 12 or not n.startswith("254"):
        return []
    local = "0" + n[3:]
    return [n, "+" + n, local, n[3:]]


def _open_loans(db, tenant_id, borrower_id):
    return (db.query(Loan)
            .filter(Loan.tenant_id == tenant_id, Loan.borrower_id == borrower_id,
                    Loan.status.in_(COLLECTIBLE_STATUSES))
            .order_by(Loan.due_date.asc().nullslast(), Loan.id.asc()).all())


def suggest_by_phone(db, tenant_id: int, phone) -> int | None:
    variants = phone_variants(phone)
    if not variants:
        return None
    borrowers = (db.query(Borrower.id)
                 .filter(Borrower.tenant_id == tenant_id, Borrower.phone.in_(variants))
                 .all())
    if len(borrowers) != 1:
        return None
    loans = _open_loans(db, tenant_id, borrowers[0][0])
    return loans[0].id if len(loans) == 1 else None


def match_reference(db, tenant_id: int, bill_ref, phone=None) -> Match:
    ref = _clean_ref(bill_ref)

    if ref:
        loan = (db.query(Loan)
                .filter(Loan.tenant_id == tenant_id,
                        func.upper(func.replace(Loan.account_number, " ", "")) == ref)
                .order_by(Loan.id.desc()).first())
        if loan is not None:
            return Match(loan=loan, how="account_number", borrower_id=loan.borrower_id)

        if cn.is_valid(ref):
            borrower = (db.query(Borrower)
                        .filter(Borrower.tenant_id == tenant_id,
                                Borrower.customer_number == cn.normalise(ref)).first())
            if borrower is not None:
                loans = _open_loans(db, tenant_id, borrower.id)
                if len(loans) == 1:
                    return Match(loan=loans[0], how="customer_number", borrower_id=borrower.id)
                return Match(how="none", borrower_id=borrower.id,
                             reason=("no_open_loan" if not loans else "ambiguous_customer"),
                             suggested_loan_id=(loans[0].id if loans else None))

    return Match(how="none", reason="unmatched",
                 suggested_loan_id=suggest_by_phone(db, tenant_id, phone))
