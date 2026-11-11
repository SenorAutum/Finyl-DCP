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
     WP11: a masked (25470****149) or hashed number is matched too; when it
     fits several clients, the payer's name must pick out one.
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


_MASK_CHARS = set("*xX#•")


def _norm_name_tokens(name) -> set[str]:
    return {t for t in "".join(c if c.isalpha() else " " for c in str(name or "").upper()).split()
            if len(t) > 1}


def _name_fits(payer_name, borrower: Borrower) -> bool:
    """Every name M-Pesa shows for the payer is one of the client's names.
    (Safaricom often sends only the first name.)"""
    payer = _norm_name_tokens(payer_name)
    if not payer:
        return True
    mine = _norm_name_tokens(" ".join(filter(None, [borrower.first_name, borrower.middle_name,
                                                     borrower.last_name])))
    return payer <= mine


def _masked_parts(phone) -> tuple[str, str] | None:
    """'25470****149' / '0722 *** 149' -> ('25470', '149'); None if not masked."""
    s = "".join(str(phone or "").split())
    if not any(c in _MASK_CHARS for c in s):
        return None
    first = min(i for i, c in enumerate(s) if c in _MASK_CHARS)
    last = max(i for i, c in enumerate(s) if c in _MASK_CHARS)
    prefix, suffix = s[:first].lstrip("+"), s[last + 1:]
    if not prefix.isdigit() or not suffix.isdigit():
        return None
    if prefix.startswith("0"):
        prefix = "254" + prefix[1:]
    if len(prefix) < 4 or len(suffix) < 3:
        return None
    return prefix, suffix


def _is_hashed(phone) -> bool:
    s = str(phone or "").strip().lower()
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def _candidates(db, tenant_id: int, phone) -> list[Borrower]:
    """Clients of this DCP whose number fits what M-Pesa sent: the full number,
    a masked number (first and last digits) or a SHA-256 hash of the number."""
    from app.services.mpesa import normalise_msisdn
    q = db.query(Borrower).filter(Borrower.tenant_id == tenant_id)
    variants = phone_variants(phone)
    if variants:
        return q.filter(Borrower.phone.in_(variants)).all()
    parts = _masked_parts(phone)
    if parts:
        prefix, suffix = parts
        return [b for b in q.filter(Borrower.phone.like(f"%{suffix}")).all()
                if normalise_msisdn(b.phone or "").startswith(prefix)
                and normalise_msisdn(b.phone or "").endswith(suffix)]
    if _is_hashed(phone):
        import hashlib
        target = str(phone).strip().lower()
        return [b for b in q.all()
                if hashlib.sha256(normalise_msisdn(b.phone or "").encode()).hexdigest() == target]
    return []


def suggest_by_phone(db, tenant_id: int, phone, payer_name=None) -> int | None:
    """A loan to SUGGEST (never apply) for a payment that names no account.
    WP11: also works when Safaricom masks or hashes the number; when several
    clients fit, the payer's name must pick out exactly one."""
    people = _candidates(db, tenant_id, phone)
    if len(people) > 1 or (people and _masked_parts(phone)):
        people = [b for b in people if _name_fits(payer_name, b)]
    if len(people) != 1:
        return None
    loans = _open_loans(db, tenant_id, people[0].id)
    return loans[0].id if len(loans) == 1 else None


def match_reference(db, tenant_id: int, bill_ref, phone=None, payer_name=None) -> Match:
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
                 suggested_loan_id=suggest_by_phone(db, tenant_id, phone, payer_name))
