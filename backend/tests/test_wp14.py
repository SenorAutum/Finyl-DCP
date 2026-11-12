"""
WP14 - ERP accounting: real double-entry books for every DCP and for Finyl.

Loans, repayments, unallocated money and Finyl's invoices post themselves; manual
journals, bills, payments and expense claims need a second person above the
DCP's limit; statements are reconciled line by line; assets depreciate monthly;
budgets compare with actuals; the month-end close posts the provision and
accrued interest and locks the period.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import (AccBill, AccJournal, AccJournalLine, AccSettings, ChartOfAccount,
                        PaymentTransaction, PlatformInvoice, PlatformInvoiceLine, PlatformPayment, Repayment,
                        SuspenseEntry, Tenant, TenantModule, User)
from app.services import books_sources, ledger as L


@pytest.fixture()
def w(seed, db):
    import app.main as main_mod

    db.add(TenantModule(tenant_id=seed.tenant.id, module_key="accounting", enabled=True))
    acct = seed.make_user("accountant", "acct@test.dcp")
    fm = seed.make_user("finance_manager", "fm@test.dcp")
    ro = seed.make_user("relationship_officer", "ro@test.dcp")
    sa = User(email="sa14@finyl.app", hashed_password="x", full_name="Finyl SA", role="super_admin")
    db.add(sa)
    db.commit()
    L.ensure_books(db, seed.tenant.id)     # the chart of accounts, as the first screen visit would
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app, raise_server_exceptions=True)

    def acc(code):
        return db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == seed.tenant.id,
                                               ChartOfAccount.code == code).one()

    def bal(code, tenant_id=None):
        a = db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == (tenant_id or seed.tenant.id),
                                            ChartOfAccount.code == code).one()
        return L.account_balance(db, a.tenant_id, a.id)

    def paid_loan(principal="10000.00", days_ago=10, term=28, b2c=True, **kw):
        loan = seed.make_loan(status="active", principal=principal,
                              disbursement_date=L.today() - timedelta(days=days_ago),
                              due_date=L.today() - timedelta(days=days_ago) + timedelta(days=term),
                              outstanding_balance="11000.00", **kw)
        if b2c:
            db.add(PaymentTransaction(tenant_id=seed.tenant.id, type="b2c", loan_id=loan.id,
                                      amount=9520, status="success"))
        db.commit()
        return loan

    def repay(loan, amount, principal, interest, fee=0, method="mpesa_c2b", days_ago=1, ref=None):
        r = Repayment(tenant_id=seed.tenant.id, loan_id=loan.id, amount=amount, principal_component=principal,
                      interest_component=interest, fee_component=fee,
                      payment_date=datetime.utcnow() - timedelta(days=days_ago), method=method,
                      mpesa_ref=ref or f"R{loan.id}{amount}")
        db.add(r)
        db.commit()
        return r

    yield SimpleNamespace(seed=seed, db=db, T=seed.tenant, acct=acct, fm=fm, ro=ro, sa=sa, api=api, acc=acc,
                          bal=bal, paid_loan=paid_loan, repay=repay)
    main_mod.app.dependency_overrides.clear()


def _sync(w, tenant_id=None):
    out = books_sources.sync(w.db, tenant_id or w.T.id)
    w.db.commit()
    return out


def _balanced(db, tenant_id):
    dr, cr = db.query(L.func.sum(AccJournalLine.debit), L.func.sum(AccJournalLine.credit)).filter(
        AccJournalLine.tenant_id == tenant_id).one()
    return Decimal(dr or 0) == Decimal(cr or 0)


# =========================================================================== automatic posting
def test_loan_payout_and_repayment_post_themselves_once(w):
    loan = w.paid_loan()
    w.repay(loan, 2200, 2000, 200)
    out = _sync(w)
    assert out["loans_paid_out"] == 1 and out["repayments"] == 1
    # 10,000 principal: 2.5% + 150 = 400 fees, 80 excise, 9,520 reached M-Pesa
    assert w.bal("1000") == Decimal("8000.00")            # 10,000 out, 2,000 back
    assert w.bal("1110") == Decimal("-9520.00")           # B2C float paid out
    assert w.bal("4010") == Decimal("-400.00")            # fee income (credit)
    assert w.bal("2000") == Decimal("-80.00")             # excise payable (credit)
    assert w.bal("1100") == Decimal("2200.00")            # paybill collections
    assert w.bal("4000") == Decimal("-200.00")            # interest income
    assert _balanced(w.db, w.T.id)
    again = _sync(w)
    assert again["posted"] == 0
    assert w.db.query(AccJournal).filter(AccJournal.source_type == "loan_disbursement").count() == 1


def test_fees_added_to_balance_are_collected_before_penalties(w):
    w.seed.product.rules = {"processing_fee_rate": 2.5, "facility_fee": 150, "fee_collection": "add_to_balance"}
    w.db.commit()
    loan = w.paid_loan(b2c=False)
    w.repay(loan, 1000, 400, 100, fee=500)
    _sync(w)
    assert w.bal("1010") == Decimal("-10000.00")          # paid from the bank (no B2C record)
    assert w.bal("1020") == Decimal("0.00")               # 480 fees+excise receivable, 480 collected
    assert w.bal("4020") == Decimal("-20.00")             # the rest of the fee part is penalty
    assert _balanced(w.db, w.T.id)


def test_suspense_in_allocation_and_refund(w):
    loan = w.paid_loan()
    s1 = SuspenseEntry(tenant_id=w.T.id, source="c2b", mpesa_ref="SUS1", amount=500, reason="unmatched",
                       status="allocated", paid_at=datetime.utcnow() - timedelta(days=2))
    s2 = SuspenseEntry(tenant_id=w.T.id, source="bank", mpesa_ref="SUS2", amount=300, reason="unmatched",
                       status="refunded", paid_at=datetime.utcnow() - timedelta(days=2),
                       resolved_at=datetime.utcnow() - timedelta(days=1))
    w.db.add_all([s1, s2])
    w.db.commit()
    w.repay(loan, 500, 450, 50, method="suspense_allocation")
    _sync(w)
    assert w.bal("1900") == Decimal("0.00")               # 500 in, 500 allocated; 300 in, 300 refunded
    assert w.bal("1100") == Decimal("500.00")
    assert w.bal("1010") == Decimal("0.00")               # bank money in and refunded


# =========================================================================== manual journals
def _journal(w, user, amount=1000, on=None, dr="6100", cr="1010"):
    return w.api(user).post("/api/v1/books/journals", json={
        "date": (on or L.today()).isoformat(), "description": "Rent for the month", "reference": "RENT",
        "lines": [{"account_id": w.acc(dr).id, "debit": amount}, {"account_id": w.acc(cr).id, "credit": amount}]})


def test_manual_journal_needs_a_second_person_and_can_be_reversed(w):
    w.api(w.acct).get("/api/v1/books/settings")
    bad = w.api(w.acct).post("/api/v1/books/journals", json={
        "date": L.today().isoformat(), "description": "Unbalanced",
        "lines": [{"account_id": w.acc("6100").id, "debit": 100}, {"account_id": w.acc("1010").id, "credit": 90}]})
    assert bad.status_code == 422
    r = _journal(w, w.acct)
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["number"].startswith("JV-")
    jid = r.json()["id"]
    assert w.bal("6100") == 0                              # pending journals are not in the books
    assert w.api(w.acct).post(f"/api/v1/books/journals/{jid}/approve").status_code == 403
    assert w.api(w.fm).post(f"/api/v1/books/journals/{jid}/approve").status_code == 200
    assert w.bal("6100") == Decimal("1000.00")
    rev = w.api(w.fm).post(f"/api/v1/books/journals/{jid}/reverse", json={"reason": "entered twice"})
    assert rev.status_code == 200
    assert w.bal("6100") == 0
    assert w.db.get(AccJournal, jid).status == "reversed"


def test_journal_under_the_limit_posts_at_once_and_auto_journals_cannot_be_reversed_by_hand(w):
    w.api(w.fm).put("/api/v1/books/settings", json={"journal_approval_limit": 5000})
    r = _journal(w, w.acct, amount=4000)
    assert r.json()["status"] == "posted"
    loan = w.paid_loan()
    _sync(w)
    j = w.db.query(AccJournal).filter(AccJournal.source_type == "loan_disbursement",
                                      AccJournal.source_id == loan.id).one()
    assert w.api(w.fm).post(f"/api/v1/books/journals/{j.id}/reverse", json={"reason": "try it"}).status_code == 409


def test_closed_period_refuses_manual_journals_and_moves_late_auto_postings(w):
    lock = L.today() - timedelta(days=5)
    s = L.ensure_books(w.db, w.T.id)
    s.lock_date = lock
    w.db.commit()
    assert _journal(w, w.acct, on=lock).status_code == 409
    loan = w.paid_loan(days_ago=20)
    _sync(w)
    j = w.db.query(AccJournal).filter(AccJournal.source_type == "loan_disbursement",
                                      AccJournal.source_id == loan.id).one()
    assert j.entry_date == lock + timedelta(days=1) and "period closed" in j.description


# =========================================================================== statements
def test_statements_balance(w):
    loan = w.paid_loan(days_ago=40)
    w.repay(loan, 5500, 5000, 500, days_ago=5)
    w.api(w.fm).put("/api/v1/books/settings", json={"journal_approval_limit": 10**6})
    _journal(w, w.acct, amount=3000)                      # rent paid from the bank
    _journal(w, w.acct, amount=50000, dr="1010", cr="3000")  # capital paid in
    tb = w.api(w.acct).get("/api/v1/books/reports/trial-balance",
                           params={"from": (L.today() - timedelta(days=60)).isoformat()}).json()
    assert tb["balanced"] and tb["rows"]
    bs = w.api(w.acct).get("/api/v1/books/reports/balance-sheet").json()
    assert bs["balanced"], bs
    pl = w.api(w.acct).get("/api/v1/books/reports/profit-loss",
                           params={"from": (L.today() - timedelta(days=60)).isoformat(), "compare": True}).json()
    assert pl["total_income"] == 900.0                    # 400 fees + 500 interest
    assert pl["total_expenses"] == 3000.0 and pl["net_profit"] == -2100.0
    cf = w.api(w.acct).get("/api/v1/books/reports/cash-flow",
                           params={"from": (L.today() - timedelta(days=60)).isoformat()}).json()
    assert cf["check"] == 0.0
    assert cf["closing_cash"] - cf["opening_cash"] == cf["net_change"]
    led = w.api(w.acct).get(f"/api/v1/books/reports/ledger/{w.acc('1000').id}",
                            params={"from": (L.today() - timedelta(days=60)).isoformat()}).json()
    assert led["closing"] == 5000.0 and len(led["rows"]) == 2
    csv_text = w.api(w.acct).get("/api/v1/books/export/ledger.csv",
                                 params={"from": (L.today() - timedelta(days=60)).isoformat()}).text
    assert "Loans Receivable" in csv_text
    ov = w.api(w.acct).get("/api/v1/books/overview").json()
    assert ov["loan_book"] == 5000.0 and ov["pending"]["journals"] == 0


# =========================================================================== payables
def _supplier(w, **kw):
    r = w.api(w.acct).post("/api/v1/books/suppliers", json={"name": "Nairobi Office Ltd", "wht_rate": 5, **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_bill_with_vat_and_withholding_tax_paid_in_two_parts(w):
    sid = _supplier(w)
    r = w.api(w.acct).post("/api/v1/books/bills", json={
        "supplier_id": sid, "bill_date": L.today().isoformat(), "supplier_ref": "INV-77",
        "lines": [{"account_id": w.acc("6160").id, "amount": 10000, "vat_rate": 16}]})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    bid = r.json()["id"]
    assert w.api(w.acct).post(f"/api/v1/books/bills/{bid}/approve").status_code == 403
    assert w.api(w.fm).post(f"/api/v1/books/bills/{bid}/approve").status_code == 200
    bill = w.db.get(AccBill, bid)
    assert (bill.subtotal, bill.vat, bill.wht, bill.total) == (Decimal("10000.00"), Decimal("1600.00"),
                                                                Decimal("500.00"), Decimal("11100.00"))
    assert w.bal("2100") == Decimal("-11100.00") and w.bal("2400") == Decimal("-500.00")
    assert w.bal("1400") == Decimal("1600.00")
    pay = lambda amt: w.api(w.acct).post("/api/v1/books/payments", json={  # noqa: E731
        "kind": "bill", "target_id": bid, "amount": amt, "paid_on": L.today().isoformat(),
        "account_id": w.acc("1010").id, "method": "bank", "reference": f"CHQ{amt}"})
    p1 = pay(6000)
    assert p1.json()["status"] == "pending"
    assert pay(6000).status_code == 422                   # 6,000 waiting + 6,000 > 11,100
    assert w.api(w.fm).post(f"/api/v1/books/payments/{p1.json()['id']}/approve").status_code == 200
    assert w.db.get(AccBill, bid).status == "part_paid"
    assert w.api(w.fm).post(f"/api/v1/books/bills/{bid}/void", json={"reason": "wrong supplier"}).status_code == 409
    p2 = pay(5100)
    w.api(w.fm).post(f"/api/v1/books/payments/{p2.json()['id']}/approve")
    assert w.db.get(AccBill, bid).status == "paid" and w.bal("2100") == 0
    aged = w.api(w.acct).get("/api/v1/books/reports/aged-payables").json()
    assert aged["totals"]["total"] == 0


def test_expense_claim_by_any_staff_member(w):
    accts = w.api(w.ro).get("/api/v1/books/expense-accounts").json()["accounts"]
    transport = next(a for a in accts if a["code"] == "6130")
    r = w.api(w.ro).post("/api/v1/books/claims", json={
        "claim_date": L.today().isoformat(), "description": "Field visits Kawangware",
        "lines": [{"account_id": transport["id"], "amount": 1200, "description": "Boda boda"}]})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert w.api(w.ro).get("/api/v1/books/overview").status_code == 403       # no access to the books
    assert len(w.api(w.ro).get("/api/v1/books/claims").json()["claims"]) == 1
    assert w.api(w.fm).post(f"/api/v1/books/claims/{cid}/approve").status_code == 200
    assert w.bal("2120") == Decimal("-1200.00") and w.bal("6130") == Decimal("1200.00")
    w.api(w.fm).put("/api/v1/books/settings", json={"payment_approval_limit": 5000})
    p = w.api(w.acct).post("/api/v1/books/payments", json={
        "kind": "claim", "target_id": cid, "amount": 1200, "paid_on": L.today().isoformat(),
        "account_id": w.acc("1100").id, "method": "mpesa"})
    assert p.json()["status"] == "posted"
    assert w.api(w.ro).get(f"/api/v1/books/claims/{cid}").json()["status"] == "paid"


def test_finyl_invoice_becomes_a_bill_and_finyl_books_record_the_income(w):
    finyl = Tenant(name="Finyl (Afridon IT Solutions)", code="FINYL", is_internal=True)
    w.db.add(finyl)
    w.db.flush()
    inv = PlatformInvoice(number="INV-2026-10-0001", tenant_id=w.T.id, period_start=date(2026, 10, 1),
                          period_end=date(2026, 10, 31), issued_at=datetime.utcnow() - timedelta(days=3),
                          due_date=L.today() + timedelta(days=11), status="part_paid", total=32000, paid=10000)
    w.db.add(inv)
    w.db.flush()
    w.db.add_all([PlatformInvoiceLine(invoice_id=inv.id, kind="subscription", description="Subscription",
                                      quantity=1, unit_price=30000, amount=30000),
                  PlatformInvoiceLine(invoice_id=inv.id, kind="sms", description="SMS", quantity=2000,
                                      unit_price=1, amount=2000),
                  PlatformPayment(tenant_id=w.T.id, invoice_id=inv.id, amount=10000, reference="QKF123",
                                  channel="mpesa_paybill", paid_at=datetime.utcnow() - timedelta(days=1))])
    w.db.commit()
    _sync(w)
    bill = w.db.query(AccBill).filter(AccBill.source_type == "finyl_invoice").one()
    assert bill.status == "part_paid" and bill.total == Decimal("32000.00") and bill.paid == Decimal("10000.00")
    assert w.bal("6150") == Decimal("30000.00") and w.bal("6120") == Decimal("2000.00")
    assert w.bal("2100") == Decimal("-22000.00")
    _sync(w, finyl.id)
    assert w.bal("1210", finyl.id) == Decimal("22000.00")     # still owed by the DCP
    assert w.bal("4100", finyl.id) == Decimal("-30000.00") and w.bal("4130", finyl.id) == Decimal("-2000.00")
    assert w.bal("1100", finyl.id) == Decimal("10000.00")
    # Finyl's workspace is not a DCP in the command centre
    r = w.api(w.sa).get("/api/v1/platform/overview")
    assert all(t["id"] != finyl.id for t in r.json()["tenants"])


# =========================================================================== reconciliation
def test_statement_import_matches_and_books_bank_charges(w):
    loan = w.paid_loan()
    w.repay(loan, 2200, 2000, 200, method="manual_bank", days_ago=2, ref="FT26001")
    _sync(w)
    day = (L.today() - timedelta(days=2)).strftime("%d/%m/%Y")
    csv = ("Account statement\nTransaction Date,Description,Reference,Debit,Credit,Balance\n"
           f"{day},Loan repayment {loan.account_number},FT26001,,2200.00,2200.00\n"
           f"{day},Ledger fee,CHG01,150.00,,2050.00\n")
    files = {"file": ("stmt.csv", csv.encode(), "text/csv")}
    r = w.api(w.acct).post(f"/api/v1/books/reconciliation/{w.acc('1010').id}/import", files=files)
    assert r.status_code == 200, r.text
    assert r.json()["lines_added"] == 2 and r.json()["auto_matched"] == 1 and r.json()["closing_balance"] == 2050.0
    again = w.api(w.acct).post(f"/api/v1/books/reconciliation/{w.acc('1010').id}/import",
                               files={"file": ("stmt.csv", csv.encode(), "text/csv")})
    assert again.json()["lines_added"] == 0 and again.json()["duplicates_skipped"] == 2
    rep = w.api(w.acct).get(f"/api/v1/books/reconciliation/{w.acc('1010').id}").json()
    assert rep["difference"] == 0.0 or rep["in_statement_not_in_books_total"] == -150.0
    charge = rep["in_statement_not_in_books"][0]
    j = w.api(w.acct).post(f"/api/v1/books/reconciliation/lines/{charge['id']}/journal",
                           json={"account_id": w.acc("6140").id})
    assert j.status_code == 200
    rep = w.api(w.acct).get(f"/api/v1/books/reconciliation/{w.acc('1010').id}").json()
    assert rep["difference"] == 0.0 and rep["in_statement_not_in_books"] == []
    assert w.bal("6140") == Decimal("150.00")


# =========================================================================== assets
def test_asset_depreciation_and_disposal(w):
    cats = w.api(w.acct).get("/api/v1/books/asset-categories").json()["categories"]
    comp = next(c for c in cats if c["name"].startswith("Computers"))
    bought = L.today().replace(day=1) - timedelta(days=40)
    r = w.api(w.acct).post("/api/v1/books/assets", json={
        "name": "Laptops x3", "category_id": comp["id"], "purchase_date": bought.isoformat(), "cost": 120000,
        "funding": "paid", "paid_from_account_id": w.acc("1010").id})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert w.bal("1510") == Decimal("120000.00")
    period = bought.strftime("%Y-%m")
    prev = w.api(w.acct).get("/api/v1/books/depreciation/preview", params={"period": period}).json()
    assert prev["total"] == 3333.33
    assert w.api(w.acct).post("/api/v1/books/depreciation/run", json={"period": period}).status_code == 200
    assert w.api(w.acct).post("/api/v1/books/depreciation/run", json={"period": period}).status_code == 409
    assert w.bal("1590") == Decimal("-3333.33") and w.bal("6190") == Decimal("3333.33")
    d = w.api(w.fm).post(f"/api/v1/books/assets/{aid}/dispose", json={
        "date": L.today().isoformat(), "proceeds": 100000, "received_into_account_id": w.acc("1130").id})
    assert d.status_code == 200
    assert w.bal("1510") == 0 and w.bal("1590") == 0
    assert w.bal("6900") == Decimal("16666.67")           # NBV 116,666.67 sold for 100,000
    assert _balanced(w.db, w.T.id)


# =========================================================================== budgets
def test_budget_versus_actual(w):
    w.api(w.fm).put("/api/v1/books/settings", json={"journal_approval_limit": 10**6})
    _journal(w, w.acct, amount=3000)
    s = L.ensure_books(w.db, w.T.id)
    year = L.fy_start(L.today(), s.fiscal_year_start_month).year
    b = w.api(w.fm).post("/api/v1/books/budgets", json={"name": "Head office", "fiscal_year": year})
    bid = b.json()["id"]
    month_index = (L.today().month - (s.fiscal_year_start_month or 1)) % 12
    amounts = [0] * 12
    amounts[month_index] = 2500
    r = w.api(w.fm).put(f"/api/v1/books/budgets/{bid}/lines",
                        json={"lines": [{"account_id": w.acc("6100").id, "amounts": amounts}]})
    assert r.status_code == 200 and r.json()["lines"][0]["total"] == 2500
    va = w.api(w.acct).get(f"/api/v1/books/budgets/{bid}/vs-actual").json()
    rent = next(x for x in va["rows"] if x["code"] == "6100")
    assert rent["actual_ytd"] == 3000 and rent["budget_ytd"] == 2500 and rent["favourable"] is False
    assert w.api(w.fm).post(f"/api/v1/books/budgets/{bid}/approve").status_code == 200
    assert w.api(w.fm).put(f"/api/v1/books/budgets/{bid}/lines", json={"lines": []}).status_code == 409


# =========================================================================== month-end
def test_month_end_provision_accrual_and_lock(w):
    late = w.paid_loan(principal="10000.00", days_ago=100, term=28)    # 72 days late -> stage 2
    late.status = "overdue"
    current = w.paid_loan(principal="20000.00", days_ago=10, term=28)  # stage 1, mid-term
    w.db.commit()
    end = L.today()
    st = w.api(w.acct).get("/api/v1/books/close", params={"period": end.strftime("%Y-%m")}).json()
    prov = st["provision"]
    assert prov["stages"][1]["exposure"] == 10000.0 and prov["stages"][0]["exposure"] == 20000.0
    assert prov["required"] == 2200.0                                   # 20% x 10,000 + 1% x 20,000
    assert w.api(w.ro).post("/api/v1/books/close/provision", json={"period": end.strftime("%Y-%m")}).status_code == 403
    assert w.api(w.acct).post("/api/v1/books/close/provision", json={"period": end.strftime("%Y-%m")}).status_code == 200
    assert w.bal("1005") == Decimal("-2200.00") and w.bal("5010") == Decimal("2200.00")
    assert w.api(w.acct).post("/api/v1/books/close/provision", json={"period": end.strftime("%Y-%m")}).status_code == 409
    acc = w.api(w.acct).post("/api/v1/books/close/accrual", json={"period": end.strftime("%Y-%m")})
    assert acc.status_code == 200 and acc.json()["posted"]
    month_end = L.month_end(end)
    accrued = L.account_balance(w.db, w.T.id, w.acc("1030").id, month_end)
    assert accrued > 0                                                    # the current loan's earned interest
    assert L.account_balance(w.db, w.T.id, w.acc("1030").id, month_end + timedelta(days=1)) == 0
    assert current.id
    # Closing before the month ends is refused; an earlier month can be closed with a reason
    assert w.api(w.acct).post("/api/v1/books/close/lock", json={"period": end.strftime("%Y-%m")}).status_code == 422
    prev = (end.replace(day=1) - timedelta(days=1))
    r = w.api(w.acct).post("/api/v1/books/close/lock", json={"period": prev.strftime("%Y-%m")})
    if r.status_code == 409:
        r = w.api(w.acct).post("/api/v1/books/close/lock", json={"period": prev.strftime("%Y-%m"),
                                                                  "override_reason": "Auditor asked to lock it"})
    assert r.status_code == 200
    assert w.db.query(AccSettings).get(w.T.id).lock_date == prev
    assert w.api(w.fm).post("/api/v1/books/close/reopen", json={"reason": "correction needed"}).status_code == 200


# =========================================================================== access & accounts
def test_module_switch_and_chart_of_accounts_rules(w):
    w.db.query(TenantModule).filter(TenantModule.module_key == "accounting").update({"enabled": False})
    w.db.commit()
    assert w.api(w.acct).get("/api/v1/books/overview").status_code == 403
    w.db.query(TenantModule).filter(TenantModule.module_key == "accounting").update({"enabled": True})
    w.db.commit()
    accts = w.api(w.acct).get("/api/v1/books/accounts").json()
    assert any(a["code"] == "1000" and "loan_book" in a["roles"] for a in accts["accounts"])
    assert w.api(w.acct).post("/api/v1/books/accounts", json={"code": "6400", "name": "Fuel", "type": "expense"}).status_code == 403
    r = w.api(w.fm).post("/api/v1/books/accounts", json={"code": "6400", "name": "Fuel", "type": "expense"})
    assert r.status_code == 200
    assert w.api(w.fm).post("/api/v1/books/accounts", json={"code": "6400", "name": "Dup", "type": "expense"}).status_code == 409
    # an account the system posts to cannot be switched off
    assert w.api(w.fm).put(f"/api/v1/books/accounts/{w.acc('1000').id}", json={"active": False}).status_code == 409
    # posting roles can be pointed at another account of the right kind only
    new_bank = w.api(w.fm).post("/api/v1/books/accounts", json={"code": "1011", "name": "Equity Bank", "type": "asset",
                                                                 "subtype": "cash", "is_cash": True}).json()["id"]
    assert new_bank
    assert w.api(w.fm).put("/api/v1/books/settings", json={"posting_map": {"bank": "6400"}}).status_code == 422
    r = w.api(w.fm).put("/api/v1/books/settings", json={"posting_map": {"bank": "1011"}})
    assert r.status_code == 200 and r.json()["posting_map"]["bank"] == "1011"
    loan = w.paid_loan(b2c=False)
    _sync(w)
    assert w.bal("1011") == Decimal("-9520.00") and loan.id
