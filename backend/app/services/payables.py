"""WP14 — payables: supplier bills, staff expense claims and payments out.

Controls (maker-checker, set per DCP in Accounting -> Settings):
  * a bill or a payment at or below the DCP's approval limit is posted at once;
    above it, a second person with accounting approval must approve it;
  * an expense claim is always approved by someone other than the claimant;
  * nobody approves their own bill, claim, payment or journal (Finyl's super
    admin excepted, as the platform owner).

Posting:
  bill approved      Dr expense / asset lines, Dr VAT input
                     Cr Accounts payable (what is owed), Cr Withholding tax payable
  claim approved     Dr expense lines, Cr Staff claims payable
  payment posted     Dr Accounts payable / Staff claims payable, Cr the bank / M-Pesa / cash
                     account it was paid from
  bill voided        the bill's journal is reversed (only while nothing is paid on it)
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.money import D, money
from app.models import (AccBill, AccBillLine, AccExpenseClaim, AccExpenseClaimLine, AccJournal,
                        AccPaymentOut, AccSupplier, ChartOfAccount, PlatformInvoice,
                        PlatformInvoiceLine, PlatformPayment)
from app.services import ledger as L

FINYL_SUPPLIER = "Finyl (Afridon IT Solutions)"


def _same_person(user, other_id) -> bool:
    return (getattr(user, "role", None) != "super_admin" and other_id is not None
            and getattr(user, "id", None) == other_id)


def account_of(db, tenant_id, account_id, *, types=None, cash=None) -> ChartOfAccount:
    a = db.get(ChartOfAccount, account_id) if account_id else None
    if a is None or a.tenant_id != tenant_id:
        raise HTTPException(422, "Choose an account from this DCP's chart of accounts")
    if not a.active:
        raise HTTPException(422, f"Account {a.code} {a.name} is switched off")
    if types and a.type not in types:
        raise HTTPException(422, f"Account {a.code} {a.name} cannot be used here (it is {a.type})")
    if cash is True and not a.is_cash:
        raise HTTPException(422, f"Pay from a bank, M-Pesa or cash account ({a.code} {a.name} is not one)")
    return a


# --------------------------------------------------------------------------- bills
def outstanding(db, bill: AccBill) -> D:
    pending = (db.query(func.coalesce(func.sum(AccPaymentOut.amount), 0))
               .filter(AccPaymentOut.bill_id == bill.id, AccPaymentOut.status == "pending").scalar())
    return money(D(bill.total) - D(bill.paid) - D(pending or 0))


def create_bill(db: Session, tenant_id: int, *, user, supplier: AccSupplier, bill_date: date,
                due_date: date | None, supplier_ref: str | None, description: str | None,
                branch_id: int | None, lines: list[dict], wht_rate=None,
                source_type: str | None = None, source_id: int | None = None,
                auto_approve: bool = False) -> AccBill:
    if supplier.tenant_id != tenant_id:
        raise HTTPException(404, "Supplier not found")
    if not lines:
        raise HTTPException(422, "Add at least one line to the bill")
    settings = L.ensure_books(db, tenant_id)
    L.check_open(settings, bill_date)
    if due_date and due_date < bill_date:
        raise HTTPException(422, "The due date cannot be before the bill date")
    subtotal = vat = L.ZERO
    rows = []
    for ln in lines:
        acct = account_of(db, tenant_id, ln.get("account_id"), types=("expense", "asset"))
        if acct.is_cash:
            raise HTTPException(422, "A bill line cannot go to a bank or M-Pesa account")
        amount = money(ln.get("amount") or 0)
        if amount <= 0:
            raise HTTPException(422, "Every bill line needs an amount above zero")
        rate = money(ln.get("vat_rate") or 0)
        if rate < 0 or rate > 100:
            raise HTTPException(422, "VAT rate must be between 0 and 100")
        line_vat = money(amount * rate / 100)
        subtotal += amount
        vat += line_vat
        rows.append(AccBillLine(account_id=acct.id, description=(ln.get("description") or "")[:300] or None,
                                amount=amount, vat_rate=rate, vat=line_vat,
                                branch_id=ln.get("branch_id") or branch_id))
    rate = D(supplier.wht_rate or 0) if wht_rate is None else D(wht_rate)
    if rate < 0 or rate > 30:
        raise HTTPException(422, "Withholding tax rate must be between 0 and 30%")
    wht = money(subtotal * rate / 100)
    total = money(subtotal + vat - wht)
    bill = AccBill(tenant_id=tenant_id, number=L.take_number(db, tenant_id, "bill"), supplier_id=supplier.id,
                   supplier_ref=(supplier_ref or None) and supplier_ref[:60], bill_date=bill_date,
                   due_date=due_date or bill_date + timedelta(days=30), branch_id=branch_id,
                   description=(description or None) and description[:300], subtotal=subtotal, vat=vat,
                   wht=wht, total=total, paid=0, status="pending", source_type=source_type,
                   source_id=source_id, created_by_user_id=getattr(user, "id", None))
    db.add(bill)
    db.flush()
    for r in rows:
        r.bill_id = bill.id
        db.add(r)
    db.flush()
    if auto_approve or total <= D(settings.payment_approval_limit or 0):
        _post_bill(db, bill, user, settings)
    return bill


def _post_bill(db, bill: AccBill, user, settings=None):
    b = L.Books(db, bill.tenant_id, settings)
    sup = db.get(AccSupplier, bill.supplier_id)
    desc = f"Bill {bill.number} from {sup.name}" + (f" ({bill.supplier_ref})" if bill.supplier_ref else "")
    lines = []
    for ln in db.query(AccBillLine).filter(AccBillLine.bill_id == bill.id):
        lines.append(L.Line(account_id=ln.account_id, debit=D(ln.amount), description=ln.description or desc,
                            branch_id=ln.branch_id, supplier_id=sup.id))
    if D(bill.vat):
        lines.append(L.line(b.account("vat_input"), debit=bill.vat, description="VAT on bill", supplier_id=sup.id))
    lines.append(L.line(b.account("payables"), credit=bill.total, description=desc, supplier_id=sup.id))
    if D(bill.wht):
        lines.append(L.line(b.account("wht_payable"), credit=bill.wht, description="Withholding tax deducted",
                            supplier_id=sup.id))
    j = L.post_journal(db, bill.tenant_id, entry_date=bill.bill_date, description=desc, lines=lines,
                       kind="auto", source_type="bill", source_id=bill.id, user=user, branch_id=bill.branch_id,
                       reference=bill.supplier_ref or bill.number, settings=b.settings, auto_date=True)
    bill.journal_id = j.id
    bill.status = "approved"
    bill.approved_by_user_id = getattr(user, "id", None)
    db.flush()
    return j


def approve_bill(db, bill: AccBill, user):
    if bill.status != "pending":
        raise HTTPException(409, f"This bill is {bill.status}, not waiting for approval")
    if _same_person(user, bill.created_by_user_id):
        raise HTTPException(403, "Someone other than the person who entered the bill must approve it")
    return _post_bill(db, bill, user)


def reject_bill(db, bill: AccBill, user, reason: str):
    if bill.status != "pending":
        raise HTTPException(409, f"This bill is {bill.status}, not waiting for approval")
    bill.status, bill.void_reason = "rejected", reason
    bill.approved_by_user_id = getattr(user, "id", None)


def void_bill(db, bill: AccBill, user, reason: str):
    if bill.status in ("void", "rejected"):
        raise HTTPException(409, f"This bill is already {bill.status}")
    if D(bill.paid) > 0 or outstanding(db, bill) != money(D(bill.total) - D(bill.paid)):
        raise HTTPException(409, "Money has been paid (or a payment is waiting) on this bill: it cannot be voided")
    if bill.journal_id:
        j = db.get(AccJournal, bill.journal_id)
        if j and j.status == "posted":
            s = L.ensure_books(db, bill.tenant_id)
            on = max(L.today(), (s.lock_date or date.min) + timedelta(days=1))
            L.reverse_journal(db, j, on=on, user=user, reason=f"bill {bill.number} voided",
                              source_type="bill_void", source_id=bill.id)
    bill.status, bill.void_reason = "void", reason


# --------------------------------------------------------------------------- claims
def create_claim(db, tenant_id, *, user, claim_date: date, description: str, branch_id, lines: list[dict]):
    if not lines:
        raise HTTPException(422, "Add at least one expense to the claim")
    if claim_date > L.today():
        raise HTTPException(422, "The claim date cannot be in the future")
    total, rows = L.ZERO, []
    for ln in lines:
        acct = account_of(db, tenant_id, ln.get("account_id"), types=("expense",))
        amount = money(ln.get("amount") or 0)
        if amount <= 0:
            raise HTTPException(422, "Every expense needs an amount above zero")
        total += amount
        rows.append(AccExpenseClaimLine(account_id=acct.id, description=(ln.get("description") or "")[:300] or None,
                                        amount=amount, receipt_ref=(ln.get("receipt_ref") or "")[:60] or None))
    c = AccExpenseClaim(tenant_id=tenant_id, number=L.take_number(db, tenant_id, "claim"),
                        claimant_user_id=user.id, claim_date=claim_date, description=description[:300],
                        branch_id=branch_id, total=total, paid=0, status="submitted")
    db.add(c)
    db.flush()
    for r in rows:
        r.claim_id = c.id
        db.add(r)
    db.flush()
    return c


def approve_claim(db, c: AccExpenseClaim, user):
    if c.status != "submitted":
        raise HTTPException(409, f"This claim is {c.status}, not waiting for approval")
    if _same_person(user, c.claimant_user_id):
        raise HTTPException(403, "You cannot approve your own expense claim")
    b = L.Books(db, c.tenant_id)
    desc = f"Expense claim {c.number}: {c.description}"
    lines = [L.Line(account_id=ln.account_id, debit=D(ln.amount), description=ln.description or desc,
                    branch_id=c.branch_id)
             for ln in db.query(AccExpenseClaimLine).filter(AccExpenseClaimLine.claim_id == c.id)]
    lines.append(L.line(b.account("claims_payable"), credit=c.total, description=desc, branch_id=c.branch_id))
    j = L.post_journal(db, c.tenant_id, entry_date=c.claim_date, description=desc, lines=lines, kind="auto",
                       source_type="expense_claim", source_id=c.id, user=user, branch_id=c.branch_id,
                       reference=c.number, settings=b.settings, auto_date=True)
    c.journal_id, c.status, c.approved_by_user_id = j.id, "approved", user.id
    return j


def reject_claim(db, c: AccExpenseClaim, user, reason: str):
    if c.status != "submitted":
        raise HTTPException(409, f"This claim is {c.status}, not waiting for approval")
    if _same_person(user, c.claimant_user_id):
        raise HTTPException(403, "You cannot decide your own expense claim")
    c.status, c.reject_reason, c.approved_by_user_id = "rejected", reason, user.id


def claim_outstanding(db, c: AccExpenseClaim):
    pending = (db.query(func.coalesce(func.sum(AccPaymentOut.amount), 0))
               .filter(AccPaymentOut.claim_id == c.id, AccPaymentOut.status == "pending").scalar())
    return money(D(c.total) - D(c.paid) - D(pending or 0))


# --------------------------------------------------------------------------- payments out
def create_payment(db, tenant_id, *, user, kind: str, target_id: int, amount, paid_on: date,
                   account_id: int, method: str | None, reference: str | None, auto_approve=False):
    settings = L.ensure_books(db, tenant_id)
    L.check_open(settings, paid_on)
    if paid_on > L.today():
        raise HTTPException(422, "The payment date cannot be in the future")
    acct = account_of(db, tenant_id, account_id, cash=True)
    amount = money(amount or 0)
    if amount <= 0:
        raise HTTPException(422, "Enter an amount above zero")
    if kind == "bill":
        bill = db.query(AccBill).filter(AccBill.id == target_id, AccBill.tenant_id == tenant_id).with_for_update().first()
        if bill is None:
            raise HTTPException(404, "Bill not found")
        if bill.status not in ("approved", "part_paid"):
            raise HTTPException(409, f"This bill is {bill.status}: only an approved bill can be paid")
        left = outstanding(db, bill)
        sup = db.get(AccSupplier, bill.supplier_id)
        p = AccPaymentOut(kind="bill", bill_id=bill.id, supplier_id=bill.supplier_id, payee=sup.name)
    elif kind == "claim":
        c = (db.query(AccExpenseClaim).filter(AccExpenseClaim.id == target_id, AccExpenseClaim.tenant_id == tenant_id)
             .with_for_update().first())
        if c is None:
            raise HTTPException(404, "Claim not found")
        if c.status != "approved":
            raise HTTPException(409, f"This claim is {c.status}: only an approved claim can be paid")
        left = claim_outstanding(db, c)
        from app.models import User
        u = db.get(User, c.claimant_user_id)
        p = AccPaymentOut(kind="claim", claim_id=c.id, payee=getattr(u, "full_name", None) or getattr(u, "email", ""))
    else:
        raise HTTPException(422, "kind must be bill or claim")
    if amount > left:
        raise HTTPException(422, f"That is more than is left to pay (KES {left:,.2f})")
    p.tenant_id, p.number, p.amount, p.paid_on = tenant_id, L.take_number(db, tenant_id, "payment"), amount, paid_on
    p.account_id, p.method = acct.id, (method or None) and method[:20]
    p.reference, p.status, p.created_by_user_id = (reference or None) and reference[:60], "pending", getattr(user, "id", None)
    db.add(p)
    db.flush()
    if auto_approve or amount <= D(settings.payment_approval_limit or 0):
        _post_payment(db, p, user, settings)
    return p


def _post_payment(db, p: AccPaymentOut, user, settings=None):
    b = L.Books(db, p.tenant_id, settings)
    if p.kind == "bill":
        bill = db.get(AccBill, p.bill_id)
        role, desc = "payables", f"Payment {p.number} for bill {bill.number} to {p.payee}"
    else:
        bill = None
        c = db.get(AccExpenseClaim, p.claim_id)
        role, desc = "claims_payable", f"Payment {p.number} for claim {c.number} to {p.payee}"
    j = L.post_journal(db, p.tenant_id, entry_date=p.paid_on, description=desc,
                       lines=[L.line(b.account(role), debit=p.amount, description=desc, supplier_id=p.supplier_id),
                              L.Line(account_id=p.account_id, credit=D(p.amount), description=desc,
                                     supplier_id=p.supplier_id)],
                       kind="auto", source_type="payment_out", source_id=p.id, user=user,
                       reference=p.reference or p.number, settings=b.settings, auto_date=True)
    p.journal_id, p.status, p.approved_by_user_id = j.id, "posted", getattr(user, "id", None)
    if bill is not None:
        bill.paid = money(D(bill.paid) + D(p.amount))
        bill.status = "paid" if D(bill.paid) >= D(bill.total) else "part_paid"
    else:
        c.paid = money(D(c.paid) + D(p.amount))
        if D(c.paid) >= D(c.total):
            c.status = "paid"
    db.flush()
    return j


def approve_payment(db, p: AccPaymentOut, user):
    if p.status != "pending":
        raise HTTPException(409, f"This payment is {p.status}, not waiting for approval")
    if _same_person(user, p.created_by_user_id):
        raise HTTPException(403, "Someone other than the person who entered the payment must approve it")
    return _post_payment(db, p, user)


def reject_payment(db, p: AccPaymentOut, user, reason):
    if p.status != "pending":
        raise HTTPException(409, f"This payment is {p.status}, not waiting for approval")
    p.status, p.reject_reason, p.approved_by_user_id = "rejected", reason, user.id


def aged_payables(db, tenant_id, as_at: date) -> dict:
    buckets = [("current", -10**6, 0), ("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10**6)]
    by_sup = {}
    for bill in db.query(AccBill).filter(AccBill.tenant_id == tenant_id,
                                         AccBill.status.in_(("approved", "part_paid"))):
        left = money(D(bill.total) - D(bill.paid))
        if left <= 0:
            continue
        days = (as_at - (bill.due_date or bill.bill_date)).days
        sup = by_sup.setdefault(bill.supplier_id, {"supplier_id": bill.supplier_id, "total": 0.0,
                                                   **{k: 0.0 for k, *_ in buckets}})
        for k, lo, hi in buckets:
            if lo <= days <= hi:
                sup[k] = round(sup[k] + float(left), 2)
        sup["total"] = round(sup["total"] + float(left), 2)
    names = {s.id: s.name for s in db.query(AccSupplier).filter(AccSupplier.tenant_id == tenant_id)}
    rows = sorted(by_sup.values(), key=lambda r: -r["total"])
    for r in rows:
        r["supplier"] = names.get(r["supplier_id"], "?")
    totals = {k: round(sum(r[k] for r in rows), 2) for k, *_ in buckets}
    return {"as_at": as_at.isoformat(), "buckets": [k for k, *_ in buckets], "rows": rows,
            "totals": {**totals, "total": round(sum(r["total"] for r in rows), 2)}}


# --------------------------------------------------------------------------- Finyl's invoices
def finyl_supplier(db, tenant_id) -> AccSupplier:
    s = db.query(AccSupplier).filter(AccSupplier.tenant_id == tenant_id, AccSupplier.name == FINYL_SUPPLIER).first()
    if s is None:
        s = AccSupplier(tenant_id=tenant_id, name=FINYL_SUPPLIER, payment_method="mpesa",
                        notes="Created automatically: Finyl's monthly platform invoices arrive as bills.")
        db.add(s)
        db.flush()
    return s


def sync_finyl_bills(db, b: L.Books, limit: int = 500) -> dict:
    """Finyl's invoices to this DCP become approved bills; payments Finyl records
    against them become payments of those bills."""
    tenant_id = b.tenant_id
    if L.is_internal(db, tenant_id):
        return {}
    made = paid = voided = 0
    have = {sid for (sid,) in db.query(AccBill.source_id).filter(AccBill.tenant_id == tenant_id,
                                                                AccBill.source_type == "finyl_invoice")}
    invoices = (db.query(PlatformInvoice).filter(PlatformInvoice.tenant_id == tenant_id,
                                                 PlatformInvoice.status != "void")
                .order_by(PlatformInvoice.id).all())
    for inv in invoices:
        if inv.id in have or made >= limit:
            continue
        if b.settings.books_start and inv.period_end < b.settings.books_start:
            continue
        sup = finyl_supplier(db, tenant_id)
        lines = []
        for ln in db.query(PlatformInvoiceLine).filter(PlatformInvoiceLine.invoice_id == inv.id):
            role = "sms_expense" if ln.kind == "sms" else "platform_fees"
            lines.append({"account_id": b.account(role).id, "description": ln.description[:300],
                          "amount": D(ln.amount)})
        lines = [ln for ln in lines if D(ln["amount"]) > 0]
        if not lines:
            continue
        bill_date = L.nairobi_date(inv.issued_at) or inv.period_end
        if b.settings.lock_date and bill_date <= b.settings.lock_date:
            bill_date = b.settings.lock_date + timedelta(days=1)
        with db.begin_nested():
            create_bill(db, tenant_id, user=None, supplier=sup, bill_date=bill_date,
                        due_date=inv.due_date, supplier_ref=inv.number,
                        description=f"Finyl invoice {inv.number} ({inv.period_start:%b %Y})", branch_id=None,
                        lines=lines, wht_rate=0, source_type="finyl_invoice", source_id=inv.id, auto_approve=True)
        made += 1
    # Voided by Finyl after the bill was made
    for bill in (db.query(AccBill).join(PlatformInvoice, PlatformInvoice.id == AccBill.source_id)
                 .filter(AccBill.tenant_id == tenant_id, AccBill.source_type == "finyl_invoice",
                         AccBill.status.notin_(("void", "rejected")), PlatformInvoice.status == "void")):
        if D(bill.paid) == 0:
            void_bill(db, bill, None, "Finyl voided the invoice")
            voided += 1
    # Payments Finyl received for these invoices
    bills = {bl.source_id: bl for bl in db.query(AccBill).filter(AccBill.tenant_id == tenant_id,
                                                                 AccBill.source_type == "finyl_invoice",
                                                                 AccBill.status.in_(("approved", "part_paid")))}
    if bills:
        done = {(r, bid) for r, bid in db.query(AccPaymentOut.reference, AccPaymentOut.bill_id)
                .filter(AccPaymentOut.tenant_id == tenant_id, AccPaymentOut.bill_id.in_([x.id for x in bills.values()]))}
        for p in (db.query(PlatformPayment).filter(PlatformPayment.invoice_id.in_(list(bills)))
                  .order_by(PlatformPayment.id)):
            bill = bills[p.invoice_id]
            if (p.reference[:60], bill.id) in done:
                continue
            amt = min(money(p.amount), outstanding(db, bill))
            if amt <= 0:
                continue
            paid_on = min(L.nairobi_date(p.paid_at), L.today())
            if b.settings.lock_date and paid_on <= b.settings.lock_date:
                paid_on = b.settings.lock_date + timedelta(days=1)
            with db.begin_nested():
                create_payment(db, tenant_id, user=None, kind="bill", target_id=bill.id, amount=amt,
                               paid_on=paid_on,
                               account_id=b.account("platform_payment_account").id,
                               method=p.channel, reference=p.reference, auto_approve=True)
            paid += 1
    return {"finyl_bills": made, "finyl_bill_payments": paid, "finyl_bills_voided": voided}
