"""WP14 — automatic posting: loans, payments and Finyl's invoices into the books.

``sync(db, tenant_id)`` looks for records that have not yet reached the ledger
and posts one journal per record. It runs every few minutes from the scheduler,
when someone presses "Post now", and before any report is shown, so the books
always agree with the loan and payment screens.

What is posted (accounts are the DCP's posting accounts, see ledger.POSTING_ROLES):

  Loan paid out        Dr Loans receivable (principal)
                       Cr M-Pesa B2C / Bank (what reached the customer)
                       Cr Fee income, Cr Excise duty payable (fees taken from the payout)
                       — or, when fees are added to the balance:
                       Dr Fees receivable, Cr Fee income, Cr Excise duty payable
  Repayment            Dr M-Pesa paybill / Bank / Cash (by how it was paid; a suspense
                       allocation debits Suspense instead)
                       Cr Loans receivable (principal part), Cr Interest income,
                       Cr Fees receivable (fees added to the balance) then Penalty income
  Unallocated money    Dr M-Pesa paybill / Bank, Cr Suspense
  Suspense refunded    Dr Suspense, Cr M-Pesa paybill / Bank
  Finyl invoice        (in the DCP's books) a supplier bill from Finyl: Dr Platform fees /
                       SMS, Cr Accounts payable; Finyl's receipt of payment pays the bill
  Finyl's own books    (Finyl's internal workspace) invoices to DCPs, payments received,
                       unmatched paybill money, loans to DCPs and their repayments
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import and_, exists, func
from sqlalchemy.orm import Session

from app.core.money import money
from app.models import (AccJournal, AccJournalLine, Loan, PaymentTransaction, PlatformDrawdown,
                        PlatformFacility, PlatformInvoice, PlatformInvoiceLine, PlatformPayment,
                        PlatformUnmatchedPayment, Repayment, SuspenseEntry, Tenant)
from app.services import ledger as L
from app.services.pricing import loan_snapshot

log = logging.getLogger("finyl.books")

NOT_DISBURSED = ("pending", "underwriting", "approved", "processing", "rejected", "cancelled")
BATCH = 500


def _posted(tenant_id, source_type, id_col):
    return exists().where(and_(AccJournal.tenant_id == tenant_id, AccJournal.source_type == source_type,
                               AccJournal.source_id == id_col))


def _start(settings):
    return settings.books_start


def money_account(b: L.Books, method: str | None, source: str | None = None):
    m = (method or "").lower()
    if m == "suspense_allocation":
        return b.account("suspense")
    if m == "topup" or (source or "") == "topup":
        return b.account("mpesa_disbursement")      # kept back from a top-up payout
    if "cash" in m:
        return b.account("cash")
    if any(k in m for k in ("bank", "cheque", "eft", "rtgs", "pesalink")) or (source or "") in ("bank", "manual"):
        return b.account("bank")
    return b.account("mpesa_collections")


# --------------------------------------------------------------------------- loans
def _post_disbursement(db, b: L.Books, loan: Loan):
    principal = money(loan.principal or 0)
    if principal <= 0 or loan.disbursement_date is None:
        return None
    snap = loan_snapshot(loan, loan.product) if loan.product is not None else {}
    payout = money(snap.get("payout_amount", principal) or 0)
    fees = money(snap.get("total_fees", 0) or 0)
    excise = money(snap.get("excise_duty", 0) or 0)
    added = (snap.get("fee_collection") or "deduct") == "add_to_balance"
    via_b2c = db.query(PaymentTransaction.id).filter(
        PaymentTransaction.tenant_id == loan.tenant_id, PaymentTransaction.loan_id == loan.id,
        PaymentTransaction.type == "b2c", PaymentTransaction.status == "success").first()
    paid_from = b.account("mpesa_disbursement" if via_b2c else "bank")
    tags = dict(loan_id=loan.id, borrower_id=loan.borrower_id, branch_id=loan.branch_id)
    desc = f"Loan {loan.account_number} paid out"
    lines = [L.line(b.account("loan_book"), debit=principal, description=desc, **tags),
             L.line(paid_from, credit=payout, description=desc, **tags)]
    if added:
        if fees + excise > 0:
            lines.append(L.line(b.account("fees_receivable"), debit=fees + excise,
                                description="Fees and excise added to the loan balance", **tags))
        fee_income = fees + (principal - payout)          # cents the payout was rounded by
    else:
        fee_income = principal - payout - excise          # fees taken from the payout
    if excise > 0:
        lines.append(L.line(b.account("excise"), credit=excise, description="Excise duty on loan fees", **tags))
    if fee_income != 0:
        lines.append(L.line(b.account("fee_income"), credit=fee_income, description="Loan fees", **tags))
    return L.post_journal(db, b.tenant_id, entry_date=loan.disbursement_date, description=desc,
                          lines=lines, kind="auto", source_type="loan_disbursement", source_id=loan.id,
                          branch_id=loan.branch_id, reference=loan.account_number,
                          settings=b.settings, auto_date=True)


def _fees_receivable_left(db, b: L.Books, loan_id: int):
    acct = b.account("fees_receivable")
    bal = (db.query(func.coalesce(func.sum(AccJournalLine.debit - AccJournalLine.credit), 0))
           .join(AccJournal, AccJournal.id == AccJournalLine.journal_id)
           .filter(AccJournalLine.tenant_id == b.tenant_id, AccJournalLine.account_id == acct.id,
                   AccJournalLine.loan_id == loan_id, AccJournal.status.in_(L.COUNTED)).scalar())
    return max(money(bal or 0), L.ZERO)


def _post_repayment(db, b: L.Books, r: Repayment, loan: Loan | None):
    amount = money(r.amount or 0)
    if amount == 0:
        return None
    interest = money(r.interest_component or 0)
    fee = money(r.fee_component or 0)
    principal = money(r.principal_component or 0)
    if principal + interest + fee != amount:
        principal = amount - interest - fee
    tags = dict(loan_id=r.loan_id, borrower_id=getattr(loan, "borrower_id", None),
                branch_id=getattr(loan, "branch_id", None))
    acct_no = getattr(loan, "account_number", f"#{r.loan_id}")
    desc = f"Repayment on {acct_no}"
    lines = [L.line(money_account(b, r.method), debit=amount, description=desc, **tags)]
    written_off = getattr(loan, "written_off_at", None)
    if written_off is not None and r.payment_date and r.payment_date >= written_off:
        # WP16: the loan has left the books; whatever comes in now is a recovery.
        lines.append(L.line(b.account("recoveries"), credit=amount, description="Recovery on a written-off loan", **tags))
        return L.post_journal(db, b.tenant_id, entry_date=L.nairobi_date(r.payment_date),
                              description=f"Recovery on written-off loan {acct_no}", lines=lines, kind="auto",
                              source_type="repayment", source_id=r.id, branch_id=tags["branch_id"],
                              reference=r.mpesa_ref, settings=b.settings, auto_date=True)
    if principal:
        lines.append(L.line(b.account("loan_book"), credit=principal, description="Principal", **tags))
    if interest:
        lines.append(L.line(b.account("interest_income"), credit=interest, description="Interest", **tags))
    if fee:
        to_fees = min(fee, _fees_receivable_left(db, b, r.loan_id)) if fee > 0 else L.ZERO
        if to_fees:
            lines.append(L.line(b.account("fees_receivable"), credit=to_fees,
                                description="Fees added to the balance", **tags))
        if fee - to_fees:
            lines.append(L.line(b.account("penalty_income"), credit=fee - to_fees,
                                description="Penalties and charges", **tags))
    return L.post_journal(db, b.tenant_id, entry_date=L.nairobi_date(r.payment_date), description=desc,
                          lines=lines, kind="auto", source_type="repayment", source_id=r.id,
                          branch_id=tags["branch_id"], reference=r.mpesa_ref, settings=b.settings,
                          auto_date=True)


def _suspense_account(b, s: SuspenseEntry):
    if (s.source or "") == "topup":
        return b.account("mpesa_disbursement")
    return money_account(b, None, "bank" if (s.source or "") in ("bank", "manual") else "mpesa")


def _post_suspense_in(db, b, s: SuspenseEntry):
    amt = money(s.amount or 0)
    if amt <= 0:
        return None
    if s.reason == "interest_refund_due":
        # No money came in: interest booked when it was paid is now owed back to
        # the borrower (early settlement, CBK LN 191/2026 reg. 56(4)).
        desc = "Interest to refund after early settlement"
        return L.post_journal(db, b.tenant_id, entry_date=L.nairobi_date(s.paid_at or s.created_at),
                              description=desc,
                              lines=[L.line(b.account("interest_income"), debit=amt, description=desc,
                                            loan_id=s.matched_loan_id),
                                     L.line(b.account("suspense"), credit=amt, description=desc,
                                            loan_id=s.matched_loan_id)],
                              kind="auto", source_type="suspense_in", source_id=s.id, reference=s.mpesa_ref,
                              settings=b.settings, auto_date=True)
    desc = f"Payment not yet allocated ({s.reason})"
    return L.post_journal(db, b.tenant_id, entry_date=L.nairobi_date(s.paid_at or s.created_at),
                          description=desc,
                          lines=[L.line(_suspense_account(b, s), debit=amt, description=desc),
                                 L.line(b.account("suspense"), credit=amt, description=desc)],
                          kind="auto", source_type="suspense_in", source_id=s.id, reference=s.mpesa_ref,
                          settings=b.settings, auto_date=True)


def _post_suspense_out(db, b, s: SuspenseEntry):
    amt = money(s.amount or 0)
    if amt <= 0:
        return None
    desc = f"Unallocated payment {s.status}"
    return L.post_journal(db, b.tenant_id, entry_date=L.nairobi_date(s.resolved_at or datetime.utcnow()),
                          description=desc,
                          lines=[L.line(b.account("suspense"), debit=amt, description=desc),
                                 L.line(_suspense_account(b, s), credit=amt, description=desc)],
                          kind="auto", source_type="suspense_out", source_id=s.id, reference=s.mpesa_ref,
                          settings=b.settings, auto_date=True)


# --------------------------------------------------------------------------- Finyl's own books
INCOME_ROLE = {"subscription": "inc_subscription", "onboarding": "inc_onboarding", "branch": "inc_branch",
               "sms": "inc_sms"}


def _post_platform_invoice(db, b, inv: PlatformInvoice):
    t = db.get(Tenant, inv.tenant_id)
    desc = f"Invoice {inv.number} to {t.name if t else 'DCP'}"
    lines = [L.line(b.account("dcp_receivable"), debit=inv.total, description=desc)]
    for ln in db.query(PlatformInvoiceLine).filter(PlatformInvoiceLine.invoice_id == inv.id):
        lines.append(L.line(b.account(INCOME_ROLE.get(ln.kind, "inc_other")), credit=ln.amount,
                            description=ln.description[:300]))
    return L.post_journal(db, b.tenant_id, entry_date=L.nairobi_date(inv.issued_at) or inv.period_end,
                          description=desc, lines=lines, kind="auto", source_type="platform_invoice",
                          source_id=inv.id, reference=inv.number, settings=b.settings, auto_date=True)


def _platform_cash(b, channel):
    return b.account("bank" if (channel or "").startswith("bank") else "mpesa_collections")


def _post_platform_payment(db, b, p: PlatformPayment):
    came_via_suspense = db.query(PlatformUnmatchedPayment.id).filter(
        PlatformUnmatchedPayment.reference == p.reference).first() is not None
    dr = b.account("suspense") if came_via_suspense else _platform_cash(b, p.channel)
    amt = money(p.amount)
    if p.facility_id:
        fac = db.get(PlatformFacility, p.facility_id)
        desc = f"Repayment on {fac.number if fac else 'facility'}"
        principal, interest = money(p.principal or 0), money(p.interest or 0)
        if principal + interest != amt:
            principal = amt - interest
        lines = [L.line(dr, debit=amt, description=desc),
                 L.line(b.account("dcp_loans"), credit=principal, description="Principal"),
                 L.line(b.account("inc_facility_interest"), credit=interest, description="Interest")]
    else:
        inv = db.get(PlatformInvoice, p.invoice_id) if p.invoice_id else None
        desc = f"Payment for {inv.number if inv else 'invoice'}"
        lines = [L.line(dr, debit=amt, description=desc),
                 L.line(b.account("dcp_receivable"), credit=amt, description=desc)]
    return L.post_journal(db, b.tenant_id, entry_date=L.nairobi_date(p.paid_at), description=desc,
                          lines=lines, kind="auto", source_type="platform_payment", source_id=p.id,
                          reference=p.reference, settings=b.settings, auto_date=True)


def _post_platform_unmatched(db, b, u: PlatformUnmatchedPayment, out: bool):
    amt = money(u.amount)
    cash = b.account("mpesa_collections")
    desc = "Paybill payment not yet matched" if not out else "Unmatched paybill payment refunded"
    lines = ([L.line(cash, debit=amt, description=desc), L.line(b.account("suspense"), credit=amt, description=desc)]
             if not out else
             [L.line(b.account("suspense"), debit=amt, description=desc), L.line(cash, credit=amt, description=desc)])
    return L.post_journal(db, b.tenant_id, entry_date=L.nairobi_date(u.paid_at or u.created_at), description=desc,
                          lines=lines, kind="auto", source_type="platform_unmatched_out" if out else "platform_unmatched_in",
                          source_id=u.id, reference=u.reference, settings=b.settings, auto_date=True)


def _post_drawdown(db, b, d: PlatformDrawdown):
    fac = db.get(PlatformFacility, d.facility_id)
    desc = f"Drawdown on {fac.number if fac else 'facility'}"
    return L.post_journal(db, b.tenant_id, entry_date=d.disbursed_on, description=desc,
                          lines=[L.line(b.account("dcp_loans"), debit=d.amount, description=desc),
                                 L.line(b.account("bank"), credit=d.amount, description=desc)],
                          kind="auto", source_type="platform_drawdown", source_id=d.id, reference=d.reference,
                          settings=b.settings, auto_date=True)


# --------------------------------------------------------------------------- the run
def sync(db: Session, tenant_id: int, *, limit: int = BATCH, force: bool = False) -> dict:
    """Post everything not yet in the books (at most ``limit`` records of each kind
    per call). Safe to run at the same time from two places: the settings row is
    locked, and the source index refuses a second journal for the same record."""
    settings = L.ensure_books(db, tenant_id, lock=True)
    if not settings.auto_post and not force:
        return {"posted": 0, "auto_post": False}
    b = L.Books(db, tenant_id, settings)
    start = _start(settings)
    counts = {}

    def run(name, rows, fn):
        n = 0
        for row in rows:
            try:
                with db.begin_nested():
                    if fn(row) is not None:
                        n += 1
            except Exception as exc:  # noqa: BLE001 — one bad record must not stop the books
                log.warning("books: could not post %s %s for tenant %s: %s", name,
                            getattr(row, "id", "?"), tenant_id, exc)
                counts.setdefault("errors", []).append(f"{name} {getattr(row, 'id', '?')}: {getattr(exc, 'detail', exc)}")
        counts[name] = n

    q = (db.query(Loan).filter(Loan.tenant_id == tenant_id, Loan.disbursement_date.isnot(None),
                               ~Loan.status.in_(NOT_DISBURSED), ~_posted(tenant_id, "loan_disbursement", Loan.id)))
    if start:
        q = q.filter(Loan.disbursement_date >= start)
    run("loans_paid_out", q.order_by(Loan.disbursement_date, Loan.id).limit(limit).all(),
        lambda loan: _post_disbursement(db, b, loan))

    q = (db.query(Repayment, Loan).outerjoin(Loan, Loan.id == Repayment.loan_id)
         .filter(Repayment.tenant_id == tenant_id, ~_posted(tenant_id, "repayment", Repayment.id)))
    if start:
        q = q.filter(Repayment.payment_date >= datetime.combine(start, datetime.min.time()) - L.NAIROBI)
    run("repayments", q.order_by(Repayment.payment_date, Repayment.id).limit(limit).all(),
        lambda pair: _post_repayment(db, b, pair[0], pair[1]))

    q = db.query(SuspenseEntry).filter(SuspenseEntry.tenant_id == tenant_id,
                                       ~_posted(tenant_id, "suspense_in", SuspenseEntry.id))
    if start:
        q = q.filter(SuspenseEntry.created_at >= datetime.combine(start, datetime.min.time()) - L.NAIROBI)
    run("unallocated_in", q.order_by(SuspenseEntry.id).limit(limit).all(), lambda s: _post_suspense_in(db, b, s))
    q = db.query(SuspenseEntry).filter(SuspenseEntry.tenant_id == tenant_id,
                                       SuspenseEntry.status.in_(("refunded", "reversed")),
                                       _posted(tenant_id, "suspense_in", SuspenseEntry.id),
                                       ~_posted(tenant_id, "suspense_out", SuspenseEntry.id))
    run("unallocated_out", q.order_by(SuspenseEntry.id).limit(limit).all(), lambda s: _post_suspense_out(db, b, s))

    # Finyl's invoices, as supplier bills in this DCP's books
    from app.services import payables
    try:
        with db.begin_nested():
            counts.update(payables.sync_finyl_bills(db, b, limit=limit))
    except Exception as exc:  # noqa: BLE001
        log.warning("books: Finyl bills for tenant %s: %s", tenant_id, exc)
        counts.setdefault("errors", []).append(f"Finyl invoices: {getattr(exc, 'detail', exc)}")

    if L.is_internal(db, tenant_id):
        q = db.query(PlatformInvoice).filter(PlatformInvoice.status != "void",
                                             ~_posted(tenant_id, "platform_invoice", PlatformInvoice.id))
        run("dcp_invoices", q.order_by(PlatformInvoice.id).limit(limit).all(),
            lambda inv: _post_platform_invoice(db, b, inv))
        voided = (db.query(AccJournal).filter(AccJournal.tenant_id == tenant_id,
                                              AccJournal.source_type == "platform_invoice",
                                              AccJournal.status == "posted",
                                              AccJournal.source_id.in_(db.query(PlatformInvoice.id)
                                                                       .filter(PlatformInvoice.status == "void")))
                  .limit(limit).all())
        run("dcp_invoices_voided", voided,
            lambda j: L.reverse_journal(db, j, on=max(L.today(), (settings.lock_date or date.min) + timedelta(days=1)),
                                        user=None, reason="invoice voided", source_type="platform_invoice_void",
                                        source_id=j.source_id))
        q = db.query(PlatformPayment).filter(~_posted(tenant_id, "platform_payment", PlatformPayment.id))
        run("dcp_payments", q.order_by(PlatformPayment.id).limit(limit).all(),
            lambda p: _post_platform_payment(db, b, p))
        q = db.query(PlatformUnmatchedPayment).filter(
            ~_posted(tenant_id, "platform_unmatched_in", PlatformUnmatchedPayment.id))
        run("paybill_unmatched_in", q.limit(limit).all(), lambda u: _post_platform_unmatched(db, b, u, False))
        q = db.query(PlatformUnmatchedPayment).filter(
            PlatformUnmatchedPayment.status == "refunded",
            ~_posted(tenant_id, "platform_unmatched_out", PlatformUnmatchedPayment.id))
        run("paybill_unmatched_out", q.limit(limit).all(), lambda u: _post_platform_unmatched(db, b, u, True))
        q = db.query(PlatformDrawdown).filter(~_posted(tenant_id, "platform_drawdown", PlatformDrawdown.id))
        run("dcp_drawdowns", q.order_by(PlatformDrawdown.id).limit(limit).all(), lambda d: _post_drawdown(db, b, d))

    settings.last_auto_post_at = datetime.utcnow()
    counts["posted"] = sum(v for k, v in counts.items() if isinstance(v, int))
    db.flush()
    return counts


def pending_counts(db: Session, tenant_id: int) -> dict:
    """How many records are waiting to be posted (for the Books overview)."""
    s = L.ensure_books(db, tenant_id)
    q = db.query(func.count(Loan.id)).filter(Loan.tenant_id == tenant_id, Loan.disbursement_date.isnot(None),
                                             ~Loan.status.in_(NOT_DISBURSED),
                                             ~_posted(tenant_id, "loan_disbursement", Loan.id))
    if s.books_start:
        q = q.filter(Loan.disbursement_date >= s.books_start)
    loans = q.scalar() or 0
    q = db.query(func.count(Repayment.id)).filter(Repayment.tenant_id == tenant_id,
                                                  ~_posted(tenant_id, "repayment", Repayment.id))
    if s.books_start:
        q = q.filter(Repayment.payment_date >= datetime.combine(s.books_start, datetime.min.time()) - L.NAIROBI)
    reps = q.scalar() or 0
    return {"loans": int(loans), "repayments": int(reps)}


def sync_all(db: Session) -> dict:
    """Scheduler entry point: every active tenant whose books post automatically."""
    out = {}
    for (tid,) in db.query(Tenant.id).filter(Tenant.active.is_(True)).all():
        try:
            out[tid] = sync(db, tid).get("posted", 0)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("books: auto-post failed for tenant %s", tid)
            try:
                from app.services.platform_alerts import raise_alert
                raise_alert(kind="job_failed", severity="warning", tenant_id=tid,
                            title="Accounting auto-posting failed",
                            detail="The books could not be updated from loans and payments.")
            except Exception:  # noqa: BLE001
                pass
    return out
