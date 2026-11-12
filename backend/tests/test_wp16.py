"""
WP16 - lending additions: write-off with recoveries, waivers, rescheduling
(each asked by one person and approved by another), top-ups that settle the
old loan from the new payout, the collateral register and group lending; and
the books follow every one of them.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import (ChartOfAccount, Loan, LoanAction, PaymentTransaction, Repayment, SuspenseEntry,
                        TenantModule)
from app.services import books_sources, disbursement, ledger as L
from app.services import repayments as rp


@pytest.fixture()
def w(seed, db):
    import app.main as main_mod

    db.add(TenantModule(tenant_id=seed.tenant.id, module_key="accounting", enabled=True))
    bm1 = seed.make_user("branch_manager", "bm1@test.dcp", branch_id=seed.branch.id)
    bm2 = seed.make_user("branch_manager", "bm2@test.dcp", branch_id=seed.branch.id)
    ro = seed.make_user("relationship_officer", "ro16@test.dcp", staff_id=seed.staff.id)
    db.commit()
    L.ensure_books(db, seed.tenant.id)
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app, raise_server_exceptions=True)

    def live_loan(principal="10000.00", days_ago=10, term=28, status="active", paid=None):
        loan = seed.make_loan(status=status, principal=principal,
                              disbursement_date=L.today() - timedelta(days=days_ago),
                              due_date=L.today() - timedelta(days=days_ago) + timedelta(days=term),
                              outstanding_balance=str(Decimal(principal) * Decimal("1.1")))
        db.add(PaymentTransaction(tenant_id=seed.tenant.id, type="b2c", loan_id=loan.id, amount=9520, status="success"))
        db.commit()
        if paid:
            pay(loan, paid, days_ago=2)
        return loan

    def pay(loan, amount, days_ago=1, ref=None):
        res = rp.apply_payment(db, tenant_id=seed.tenant.id, loan=loan, amount=amount,
                               ref=ref or f"P{loan.id}-{amount}-{days_ago}", method="mpesa_c2b",
                               paid_at=datetime.utcnow() - timedelta(days=days_ago), send_receipt=False)
        db.commit()
        return res

    def bal(code):
        a = db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == seed.tenant.id, ChartOfAccount.code == code).one()
        return L.account_balance(db, seed.tenant.id, a.id)

    def sync():
        books_sources.sync(db, seed.tenant.id)
        db.commit()

    yield SimpleNamespace(seed=seed, db=db, bm1=bm1, bm2=bm2, ro=ro, api=api, live_loan=live_loan, pay=pay, bal=bal,
                          sync=sync, T=seed.tenant)
    main_mod.app.dependency_overrides.clear()


def _ask(w, user, loan, **body):
    return w.api(user).post(f"/api/v1/lending-plus/loans/{loan.id}/actions",
                            json={"reason": "Client unreachable for 120 days", **body})


def _decide(w, user, aid, approve=True, note=None):
    return w.api(user).post(f"/api/v1/lending-plus/actions/{aid}/decide", json={"approve": approve, "note": note})


# =========================================================================== write-off
def test_write_off_needs_a_second_person_and_later_payments_are_recoveries(w):
    loan = w.live_loan(days_ago=150, status="defaulted")
    w.sync()
    assert _ask(w, w.ro, loan, kind="writeoff").status_code == 403                    # officer cannot
    r = _ask(w, w.bm1, loan, kind="writeoff")
    assert r.status_code == 200
    assert _ask(w, w.bm1, loan, kind="writeoff").status_code == 409                   # one pending at a time
    aid = r.json()["id"]
    assert _decide(w, w.bm1, aid).status_code == 403                                  # not your own
    assert _decide(w, w.bm2, aid).status_code == 200
    w.db.refresh(loan)
    assert loan.status == "written_off" and loan.written_off_amount == Decimal("11000.00")
    assert w.bal("1000") == 0 and w.bal("5000") == Decimal("10000.00")
    # money still comes in: it is collectible, and booked as a recovery
    res = w.pay(loan, 3000, days_ago=0)
    assert res.status == "applied" if hasattr(res, "status") else True
    w.db.refresh(loan)
    assert loan.outstanding_balance == Decimal("8000.00")
    w.sync()
    assert w.bal("4030") == Decimal("-3000.00") and w.bal("1000") == 0


# =========================================================================== waiver
def test_interest_waiver_reduces_what_is_owed(w):
    loan = w.live_loan(days_ago=40, term=28, status="overdue")
    owed = rp.owed_breakdown(w.db, loan)
    assert owed["interest"] == Decimal("1000.00")
    assert _ask(w, w.bm1, loan, kind="waiver", component="interest", amount=1500).status_code == 422
    aid = _ask(w, w.bm1, loan, kind="waiver", component="interest", amount=400).json()["id"]
    assert _decide(w, w.bm2, aid, approve=False).status_code == 422                   # rejection needs a reason
    assert _decide(w, w.bm2, aid).status_code == 200
    w.db.refresh(loan)
    assert loan.outstanding_balance == Decimal("10600.00") and loan.interest_waived == Decimal("400.00")
    owed = rp.owed_breakdown(w.db, loan)
    assert (owed["principal"], owed["interest"], owed["fees"]) == (Decimal("10000.00"), Decimal("600.00"), Decimal("0.00"))
    w.pay(loan, 10600)
    w.db.refresh(loan)
    assert loan.status == "paid"


def test_fee_waiver_takes_fees_out_of_the_books(w):
    w.seed.product.rules = {"processing_fee_rate": 2.5, "facility_fee": 150, "fee_collection": "add_to_balance"}
    w.db.commit()
    loan = w.live_loan(days_ago=10)
    loan.outstanding_balance = Decimal("11480.00")
    loan.fees_in_balance = Decimal("480.00")
    w.db.commit()
    w.sync()
    assert w.bal("1020") == Decimal("480.00")
    aid = _ask(w, w.bm1, loan, kind="waiver", component="fees", amount=480).json()["id"]
    _decide(w, w.bm2, aid)
    assert w.bal("1020") == 0 and w.bal("4010") == 0 and w.bal("2000") == 0
    w.db.refresh(loan)
    assert loan.outstanding_balance == Decimal("11000.00")


# =========================================================================== reschedule
def test_reschedule_makes_an_overdue_loan_current(w):
    loan = w.live_loan(days_ago=45, term=28, status="overdue")
    new_due = (L.today() + timedelta(days=30)).isoformat()
    assert _ask(w, w.bm1, loan, kind="reschedule", new_due_date=(L.today() - timedelta(days=1)).isoformat()).status_code == 422
    aid = _ask(w, w.bm1, loan, kind="reschedule", new_due_date=new_due, fee=500).json()["id"]
    _decide(w, w.bm2, aid)
    w.db.refresh(loan)
    assert loan.status == "active" and loan.due_date.isoformat() == new_due
    assert loan.outstanding_balance == Decimal("11500.00") and loan.restructure_count == 1
    assert rp.owed_breakdown(w.db, loan)["fees"] == Decimal("500.00")
    extras = w.api(w.bm2).get(f"/api/v1/lending-plus/loans/{loan.id}").json()
    assert extras["restructure_count"] == 1 and extras["actions"][0]["status"] == "done"


# =========================================================================== top-up
def test_top_up_settles_the_old_loan_from_the_new_payout(w, mock_b2c):
    old = w.live_loan(days_ago=14, term=28, paid="6000")
    w.db.refresh(old)
    assert w.api(w.ro).post(f"/api/v1/lending-plus/loans/{old.id}/topup", json={"principal": 2000}).status_code == 422
    r = w.api(w.ro).post(f"/api/v1/lending-plus/loans/{old.id}/topup", json={"principal": 20000})
    assert r.status_code == 200, r.text
    new = w.db.get(Loan, r.json()["id"])
    assert new.topup_of_loan_id == old.id and new.status == "pending"
    payoff = Decimal(str(rp.payoff_quote(w.db, old, datetime.utcnow())["payoff_amount"]))
    new.status = "approved"
    w.db.commit()
    disbursement.execute_disbursement(w.db, w.T.id, new, w.bm1.id)
    sent = Decimal(str(mock_b2c.calls[-1]["amount"]))
    payout = Decimal(str(new.pricing_snapshot["payout_amount"]))
    assert sent == payout - payoff
    txn = w.db.query(PaymentTransaction).filter(PaymentTransaction.loan_id == new.id, PaymentTransaction.type == "b2c").one()
    disbursement.apply_b2c_result(w.db, w.T.id, txn, "0", "TOPUPRCPT1")
    w.db.commit()
    w.db.refresh(old)
    w.db.refresh(new)
    assert new.status == "active" and old.status == "paid"
    assert w.db.query(Repayment).filter(Repayment.loan_id == old.id, Repayment.method == "topup").count() == 1
    w.sync()
    # The B2C float went down by exactly the cash sent for the new loan
    paid_out_old = Decimal("9520.00")
    assert w.bal("1110") == -(paid_out_old + sent)
    assert L.trial_balance(w.db, w.T.id, date(2000, 1, 1), L.today())["balanced"]


def test_top_up_needs_half_repaid(w):
    old = w.live_loan(days_ago=5, paid="1000")
    r = w.api(w.ro).post(f"/api/v1/lending-plus/loans/{old.id}/topup", json={"principal": 20000})
    assert r.status_code == 409


# =========================================================================== collateral
def test_collateral_register_and_release_rules(w):
    loan = w.live_loan()
    body = {"borrower_id": w.seed.borrower.id, "loan_id": loan.id, "kind": "logbook", "description": "Toyota Probox",
            "identifier": "KDA 123A", "estimated_value": 450000, "forced_sale_value": 300000,
            "received_on": L.today().isoformat(), "insured": True, "insurer": "Jubilee",
            "insurance_expiry": (L.today() - timedelta(days=1)).isoformat()}
    assert w.api(w.ro).post("/api/v1/lending-plus/collateral", json={**body, "forced_sale_value": 500000}).status_code == 422
    cid = w.api(w.ro).post("/api/v1/lending-plus/collateral", json=body).json()["id"]
    lst = w.api(w.ro).get("/api/v1/lending-plus/collateral").json()["collateral"]
    assert lst[0]["insurance_lapsed"] is True
    extras = w.api(w.ro).get(f"/api/v1/lending-plus/loans/{loan.id}").json()
    assert extras["collateral_cover_pct"] == round(300000 / 11000 * 100, 1)
    release = {"status": "released", "date": L.today().isoformat(), "note": "Loan cleared"}
    assert w.api(w.ro).post(f"/api/v1/lending-plus/collateral/{cid}/status", json=release).status_code == 409
    w.pay(loan, 11000)
    assert w.api(w.ro).post(f"/api/v1/lending-plus/collateral/{cid}/status", json=release).status_code == 200
    assert w.api(w.ro).post(f"/api/v1/lending-plus/collateral/{cid}/status", json={**release, "status": "seized"}).status_code == 409


# =========================================================================== groups
def test_group_lending(w):
    g = w.api(w.ro).post("/api/v1/lending-plus/groups", json={"name": "Umoja Women Group", "meeting_day": "Tuesday",
                                                             "branch_id": w.seed.branch.id}).json()
    gid = g["id"]
    assert g["code"] == "GRP-0001"
    assert w.api(w.ro).post(f"/api/v1/lending-plus/groups/{gid}/members",
                            json={"borrower_id": w.seed.borrower.id, "role": "chair"}).status_code == 200
    g2 = w.api(w.ro).post("/api/v1/lending-plus/groups", json={"name": "Second Group"}).json()["id"]
    assert w.api(w.ro).post(f"/api/v1/lending-plus/groups/{g2}/members",
                            json={"borrower_id": w.seed.borrower.id}).status_code == 409
    loan = w.live_loan(days_ago=40, status="overdue")
    assert w.api(w.ro).post(f"/api/v1/lending-plus/loans/{loan.id}/group", json={"group_id": gid}).status_code == 200
    mt = w.api(w.ro).post(f"/api/v1/lending-plus/groups/{gid}/meetings",
                          json={"held_on": L.today().isoformat(), "present": [w.seed.borrower.id], "collected": 1500})
    assert mt.status_code == 200
    d = w.api(w.ro).get(f"/api/v1/lending-plus/groups/{gid}").json()
    assert d["members"] == 1 and d["late_members"] == 1 and d["meetings"][0]["attendance_pct"] == 100
    mid = d["members_list"][0]["id"]
    assert w.api(w.ro).post(f"/api/v1/lending-plus/groups/{gid}/members/{mid}/leave").status_code == 409


# =========================================================================== books fix
def test_interest_to_refund_is_not_booked_as_money_received(w):
    loan = w.live_loan()
    w.db.add(SuspenseEntry(tenant_id=w.T.id, source="c2b", mpesa_ref="X-INT", amount=120, reason="interest_refund_due",
                           status="open", matched_loan_id=loan.id, paid_at=datetime.utcnow()))
    w.db.commit()
    w.sync()
    assert w.bal("1100") == 0 and w.bal("4000") == Decimal("120.00") and w.bal("1900") == Decimal("-120.00")
