"""
WP11 - the last open items: till (Buy Goods) payments, bank statement import,
and the location rule for the whole working session.
"""
import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import (ApprovalThreshold, AuditLog, Borrower, Loan, PendingApproval,
                        Repayment, SuspenseEntry, Tenant, TenantIntegrationConfig,
                        TenantModule, TenantSecurityConfig, User)
from app.routers import payments
from app.services import webhook_security as ws


@pytest.fixture()
def w(seed, db):
    import app.main as main_mod

    def mk(role, email, tenant=seed.tenant):
        u = User(email=email, hashed_password="x", full_name=email.split("@")[0], role=role,
                 tenant_id=tenant.id if tenant else None)
        db.add(u)
        db.flush()
        return u

    B = Tenant(name="Other DCP", code="ODCP")
    db.add(B)
    db.flush()
    for key in ("lending", "payments"):
        db.add(TenantModule(tenant_id=B.id, module_key=key, enabled=True))
    b_client = Borrower(tenant_id=B.id, first_name="Otieno", last_name="Other",
                        national_id="99887766", phone="0799000111")
    db.add(b_client)
    db.flush()
    b_loan = Loan(tenant_id=B.id, account_number="FL/O/0001", borrower_id=b_client.id,
                  product_id=seed.product.id, principal="5000.00", interest_rate="10.0",
                  status="active", outstanding_balance="5500.00")
    db.add(b_loan)
    loan = seed.make_loan(status="active", outstanding_balance="11000.00")
    # A second client whose number shares the first and last digits with Jane's.
    peter = Borrower(tenant_id=seed.tenant.id, first_name="Peter", last_name="Kamau",
                     national_id="55667788", phone="0712999678", branch_id=seed.branch.id)
    db.add(peter)
    db.flush()
    peter_loan = Loan(tenant_id=seed.tenant.id, account_number="FL/T/0900", borrower_id=peter.id,
                      product_id=seed.product.id, principal="2000.00", interest_rate="10.0",
                      status="active", outstanding_balance="2200.00")
    db.add(peter_loan)
    seed.borrower.phone = "0712345678"
    u = SimpleNamespace(
        sa=mk("super_admin", "sa11@finyl.app", tenant=None),
        admin=mk("system_admin", "admin11@a.dcp"),
        admin_b=mk("system_admin", "admin11@b.dcp", tenant=B),
        recon=seed.recon,
        recon_b=mk("reconciliation_officer", "recon11@b.dcp", tenant=B),
        ro=mk("relationship_officer", "ro11@a.dcp"),
    )
    db.commit()

    def api(user):
        main_mod.app.dependency_overrides[get_db] = lambda: db
        main_mod.app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(main_mod.app)

    yield SimpleNamespace(seed=seed, db=db, A=seed.tenant, B=B, u=u, api=api, loan=loan,
                          peter=peter, peter_loan=peter_loan, b_loan=b_loan,
                          H={"X-Tenant-Id": str(seed.tenant.id)},
                          HB={"X-Tenant-Id": str(B.id)})
    main_mod.app.dependency_overrides.clear()


def _mpesa(w, tenant, paybill, tills=()):
    w.db.add(TenantIntegrationConfig(tenant_id=tenant.id, integration="daraja",
                                     config={"shortcode": paybill, "till_numbers": list(tills)},
                                     secrets={}, enabled=True))
    w.db.commit()


def _c2b(shortcode, bill_ref="", msisdn="254712345678", trans_id="TILL0001", amount="700",
         first="JANE", kind="Buy Goods"):
    return {"TransactionType": kind, "TransID": trans_id, "TransTime": "20261105101500",
            "TransAmount": amount, "BusinessShortCode": shortcode, "BillRefNumber": bill_ref,
            "MSISDN": msisdn, "FirstName": first}


def _ingest(db, body):
    return payments._ingest_and_process(
        db, ws.ENDPOINT_C2B_CALLBACK, body, payments._process_c2b_callback,
        {"ResultCode": 0, "ResultDesc": "Confirmation received successfully"})


# =========================================================================== #
# Till numbers
# =========================================================================== #
def test_till_numbers_are_saved_checked_and_never_shared(w):
    # (Each call builds its client: the signed-in user is set per client.)
    a = SimpleNamespace(put=lambda *x, **k: w.api(w.u.admin).put(*x, **k))
    b = SimpleNamespace(put=lambda *x, **k: w.api(w.u.admin_b).put(*x, **k))
    r = a.put("/api/v1/settings/daraja", json={"shortcode": "600111",
                                                "till_numbers": "5123456, 5123457 5123456"},
              headers=w.H)
    assert r.status_code == 200, r.text
    assert r.json()["till_numbers"] == ["5123456", "5123457"]
    assert a.put("/api/v1/settings/daraja", json={"till_numbers": "51234AB"},
                 headers=w.H).status_code == 422
    # Another DCP can use neither the till nor the paybill numbers.
    r = b.put("/api/v1/settings/daraja", json={"till_numbers": "5123457"}, headers=w.HB)
    assert r.status_code == 409 and "till number" in r.json()["detail"]
    r = b.put("/api/v1/settings/daraja", json={"shortcode": "5123456"}, headers=w.HB)
    assert r.status_code == 409
    # Blank clears.
    r = a.put("/api/v1/settings/daraja", json={"till_numbers": ""}, headers=w.H)
    assert r.json()["till_numbers"] == []


def test_till_payment_reaches_the_dcp_and_waits_for_review_with_a_suggestion(w):
    _mpesa(w, w.A, "600111", ["5123456"])
    _mpesa(w, w.B, "600222")
    before = w.loan.outstanding_balance
    # Safaricom masks the number: 25471****678 fits both Jane and Peter; the
    # payer's first name picks out Jane.
    _ingest(w.db, _c2b("5123456", msisdn="25471****678", first="JANE"))
    e = w.db.query(SuspenseEntry).filter_by(mpesa_ref="TILL0001").one()
    assert e.tenant_id == w.A.id and e.source == "till" and e.reason == "till_payment"
    assert e.suggested_loan_id == w.loan.id
    w.db.refresh(w.loan)
    assert w.loan.outstanding_balance == before          # never applied by itself
    # A name that fits neither: no suggestion.
    _ingest(w.db, _c2b("5123456", msisdn="25471****678", first="MARY", trans_id="TILL0002"))
    assert w.db.query(SuspenseEntry).filter_by(mpesa_ref="TILL0002").one().suggested_loan_id is None
    # Till receipts are not sent back through Safaricom's paybill reversal.
    from app.services.payment_approvals import reversal_eligibility
    assert reversal_eligibility(e)[0] is False


def test_paybill_still_matches_and_a_hashed_number_is_suggested(w):
    _mpesa(w, w.A, "600111", ["5123456"])
    _ingest(w.db, _c2b("600111", bill_ref=w.loan.account_number, trans_id="PB0001",
                       kind="Pay Bill"))
    rep = w.db.query(Repayment).filter_by(mpesa_ref="PB0001").one()
    assert rep.loan_id == w.loan.id and rep.method == "mpesa_c2b"
    hashed = hashlib.sha256(b"254712999678").hexdigest()
    _ingest(w.db, _c2b("600111", bill_ref="WRONG", msisdn=hashed, trans_id="PB0002",
                       kind="Pay Bill"))
    e = w.db.query(SuspenseEntry).filter_by(mpesa_ref="PB0002").one()
    assert e.source == "c2b" and e.suggested_loan_id == w.peter_loan.id
    assert e.phone is None                     # a hash does not fit the phone column


def test_unknown_till_is_not_guessed(w):
    _mpesa(w, w.A, "600111", ["5123456"])
    with pytest.raises(ws.WebhookUnresolved):
        payments._process_c2b_callback(w.db, _c2b("5999999"))


def test_registration_covers_the_paybill_and_every_till(w, monkeypatch):
    from app.services import mpesa
    _mpesa(w, w.A, "600111", ["5123456", "5123457"])
    creds = mpesa.DarajaCreds("key", "secret", "600111", "pk", "init", "cred", "sandbox")
    monkeypatch.setattr(mpesa, "resolve_creds", lambda _db, tid: creds)
    seen = []

    def fake(c, shortcode=None):
        seen.append(shortcode or c.shortcode)
        ok = (shortcode or c.shortcode) != "5123457"
        return {"request": {"ShortCode": shortcode or c.shortcode, "ConfirmationURL": "https://x/hooks/T/c2b"},
                "response": {"ResponseCode": "0" if ok else "1",
                             "ResponseDescription": "Success" if ok else "Invalid ShortCode"}}
    monkeypatch.setattr(mpesa, "register_c2b_urls", fake)
    r = w.api(w.u.sa).post("/api/v1/integrations/daraja/register-c2b", json={"tenant_id": w.A.id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen == ["600111", "5123456", "5123457"]
    assert body["ok"] is False                           # one till was refused
    assert [x["ok"] for x in body["results"]] == [True, True, False]
    assert w.db.query(AuditLog).filter_by(action="mpesa.c2b_register_failed").count() == 1


# =========================================================================== #
# Bank statements
# =========================================================================== #
def _statement(w, extra_lines=()):
    cust = w.seed.borrower.customer_number
    lines = [
        "Equity Bank Kenya,,,,,,",
        "Account,0123456789 Collections,,,,,",
        ",,,,,,",
        "Transaction Date,Value Date,Narrative,Reference,Debit,Credit,Balance",
        f"05/11/2026,05/11/2026,DEP {w.loan.account_number} JANE,EQ001,,\"1,000.00\",\"5,000.00\"",
        f"05/11/2026,05/11/2026,CASH DEPOSIT CUST NO {cust},EQ002,,500.00,\"5,500.00\"",
        "06/11/2026,06/11/2026,TRANSFER FROM JOHN,EQ003,,300.00,\"5,800.00\"",
        "06/11/2026,06/11/2026,ATM WITHDRAWAL,EQ004,200.00,,\"5,600.00\"",
        "06/11/2026,06/11/2026,PAYMENT FL/O/0001,EQ005,,250.00,\"5,850.00\"",
        *extra_lines,
        ",,TOTAL,,200.00,\"2,050.00\",",
    ]
    return ("\n".join(lines)).encode()


def _post(w, user, path, data, name="statement.csv", headers=None, **form):
    return w.api(user).post(f"/api/v1/bank-import/{path}", headers=headers or w.H,
                            files={"file": (name, data)}, data=form)


def test_bank_preview_finds_the_columns_and_changes_nothing(w):
    r = _post(w, w.u.recon, "preview", _statement(w))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["detected"] is True and d["layout"]["header_row"] == 4
    assert d["layout"]["date"] == "Transaction Date" and d["layout"]["credit"] == "Credit"
    by = {ln["reference"]: ln for ln in d["lines"] if ln["reference"]}
    assert by["EQ001"]["status"] == "matched" and by["EQ001"]["loan_id"] == w.loan.id
    assert by["EQ001"]["amount"] == 1000.0 and by["EQ001"]["date"] == "2026-11-05"
    assert by["EQ002"]["status"] == "matched" and by["EQ002"]["matched_by"] == "customer_number"
    assert by["EQ003"]["status"] == "review" and by["EQ003"]["reason"] == "bank_unmatched"
    # Another DCP's loan number is never matched here.
    assert by["EQ005"]["status"] == "review"
    assert d["summary"]["matched"]["count"] == 2 and d["summary"]["skipped"]["count"] >= 2
    assert w.db.query(SuspenseEntry).count() == 0 and w.db.query(Repayment).count() == 0
    assert w.db.query(PendingApproval).count() == 0


def test_bank_import_records_each_line_once(w):
    w.db.add(ApprovalThreshold(tenant_id=w.A.id, scope_type="role", scope_key="all",
                               threshold_type="manual_payment", amount=800))
    w.db.commit()
    r = _post(w, w.u.recon, "import", _statement(w), bank_account="Equity collections")
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["applied"], d["pending_approval"], d["suspense"]) == (1, 1, 2)
    p = w.db.query(PendingApproval).filter_by(action_type="manual_payment").one()
    assert p.loan_id == w.loan.id and float(p.amount) == 1000.0 and p.details["channel"] == "bank"
    rep = w.db.query(Repayment).filter_by(mpesa_ref="EQ002").one()
    assert rep.loan_id == w.loan.id
    e = w.db.query(SuspenseEntry).filter_by(mpesa_ref="EQ003").one()
    assert e.source == "bank" and e.reason == "bank_unmatched" and e.tenant_id == w.A.id
    assert w.db.query(AuditLog).filter_by(action="payments.bank_import").count() == 1
    # The same statement again: everything is already recorded.
    again = _post(w, w.u.recon, "import", _statement(w)).json()
    assert (again["applied"], again["pending_approval"], again["suspense"]) == (0, 0, 0)
    assert again["duplicates"] == 4


def test_lines_left_out_are_not_recorded_and_can_come_later(w):
    r = _post(w, w.u.recon, "import", _statement(w), leave_out="7,9")
    assert r.status_code == 200, r.text
    assert r.json()["left_out"] == 2
    assert w.db.query(SuspenseEntry).count() == 0
    later = _post(w, w.u.recon, "import", _statement(w)).json()
    assert later["suspense"] == 2


def _xlsx(rows, date_cols=()):
    """A minimal .xlsx: inline strings, numbers, and dates stored as day counts."""
    def cell(ref, v, col):
        if isinstance(v, (int, float)):
            style = ' s="1"' if col in date_cols else ""
            return f'<c r="{ref}"{style}><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'
    body = ""
    for i, row in enumerate(rows, start=1):
        cells = "".join(cell(f"{chr(65 + j)}{i}", v, j) for j, v in enumerate(row) if v != "")
        body += f'<row r="{i}">{cells}</row>'
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>")
        z.writestr("xl/styles.xml", f'<styleSheet {ns}><cellXfs count="2"><xf numFmtId="0"/>'
                                    f'<xf numFmtId="14"/></cellXfs></styleSheet>')
    return buf.getvalue()


def test_excel_statement_with_odd_headings_and_a_saved_layout(w):
    # 46331 = 5 Nov 2026 as Excel counts days.
    rows = [["Txn Dt", "Details", "Money In", "Money Out"],
            [46331, f"MOBILE DEP {w.loan.account_number}", 600, ""],
            [46331, "CHARGES", "", 50]]
    data = _xlsx(rows, date_cols={0})
    r = _post(w, w.u.recon, "preview", data, name="coop.xlsx")
    assert r.status_code == 200, r.text
    assert r.json()["layout"] is None and r.json()["first_rows"][0][0] == "Txn Dt"
    layout = {"header_row": 1, "date": "Txn Dt", "description": "Details",
              "credit": "Money In", "debit": "Money Out"}
    r = _post(w, w.u.recon, "preview", data, name="coop.xlsx", layout=json.dumps(layout))
    lines = r.json()["lines"]
    assert lines[0]["status"] == "matched" and lines[0]["date"] == "2026-11-05"
    assert lines[1]["status"] == "skipped"
    r = _post(w, w.u.recon, "import", data, name="coop.xlsx", layout=json.dumps(layout),
              save_as="Co-op collections")
    # No manual-payment threshold set: every payment waits for a second person.
    assert r.status_code == 200 and r.json()["pending_approval"] == 1
    saved = w.api(w.u.recon).get("/api/v1/bank-import/layouts", headers=w.H).json()["layouts"]
    assert saved["Co-op collections"]["credit"] == "Money In"
    # Next time the saved layout is used by name; the line is already recorded.
    r = _post(w, w.u.recon, "preview", data, name="coop.xlsx", layout_name="Co-op collections")
    assert r.json()["lines"][0]["status"] == "duplicate"


def test_bank_import_needs_permission_and_stays_inside_the_dcp(w):
    assert _post(w, w.u.ro, "preview", _statement(w)).status_code == 403
    assert _post(w, w.u.recon, "preview", b"%PDF-1.4 ...", name="s.pdf").status_code == 422
    # DCP B's statement naming DCP A's loan number: not matched in B.
    r = _post(w, w.u.recon_b, "import", _statement(w), headers=w.HB)
    assert r.status_code == 200, r.text
    assert w.db.query(Repayment).filter(Repayment.loan_id == w.loan.id).count() == 0
    b_lines = w.db.query(SuspenseEntry).filter_by(tenant_id=w.B.id).count()
    assert b_lines >= 1 and w.db.query(SuspenseEntry).filter_by(tenant_id=w.A.id).count() == 0


def test_amounts_and_dates_in_bank_formats():
    from decimal import Decimal
    from app.services.bank_import import parse_amount, parse_date
    assert parse_amount("KES 1,250.50") == Decimal("1250.50")
    assert parse_amount("(300.00)") == Decimal("-300.00")
    assert parse_amount("1,000.00 CR") == Decimal("1000.00")
    assert parse_amount("-") is None
    assert parse_date("05-Nov-2026") == datetime(2026, 11, 4, 21, 0)
    assert parse_date("05/11/2026 14:30") == datetime(2026, 11, 5, 11, 30)
    assert parse_date("11/05/2026", "mdy") == datetime(2026, 11, 4, 21, 0)
    assert parse_date("2026-11-05") == datetime(2026, 11, 4, 21, 0)


# =========================================================================== #
# Location for the whole session
# =========================================================================== #
def _req(headers=None, path="/api/v1/loans"):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": path, "headers": raw,
                    "query_string": b"", "server": ("test", 80), "scheme": "http"})


def _token(user):
    from app.core.security import create_access_token
    return create_access_token(user.id, user.role, user.tenant_id, token_version=0)


def test_location_is_needed_for_the_whole_session(w):
    from app.core import deps
    w.db.add(TenantSecurityConfig(tenant_id=w.A.id, geofence_enabled=True,
                                  geofence_center_lat=-1.2864, geofence_center_lng=36.8172,
                                  geofence_radius_km=20, location_max_age_minutes=10))
    w.db.commit()
    me = lambda h=None, path="/api/v1/loans": deps.get_current_user(  # noqa: E731
        token=_token(w.u.ro), db=w.db, request=_req(h, path))
    inside = {"X-Geo-Lat": "-1.29", "X-Geo-Lng": "36.82"}
    # No location, or one without an age, or an old one: ask for it again.
    for h in (None, inside, {**inside, "X-Geo-Age": "900"}):
        with pytest.raises(HTTPException) as e:
            me(h)
        assert e.value.status_code == 428 and e.value.detail["code"] == "location_required"
    assert me({**inside, "X-Geo-Age": "30"}).id == w.u.ro.id
    # Outside the area: signed out.
    with pytest.raises(HTTPException) as e:
        me({"X-Geo-Lat": "-4.05", "X-Geo-Lng": "39.66", "X-Geo-Age": "5"})
    assert e.value.status_code == 401
    # Signing out always works.
    assert me(None, "/api/v1/auth/logout").id == w.u.ro.id


def test_location_rule_off_changes_nothing(w):
    from app.core import deps
    w.db.add(TenantSecurityConfig(tenant_id=w.A.id, geofence_enabled=False))
    w.db.commit()
    assert deps.get_current_user(token=_token(w.u.ro), db=w.db, request=_req()).id == w.u.ro.id


def test_sign_in_location_is_kept_and_age_setting_is_saved(w):
    from app.core.security import hash_password
    w.db.add(TenantSecurityConfig(tenant_id=w.A.id, geofence_enabled=True,
                                  geofence_center_lat=-1.2864, geofence_center_lng=36.8172,
                                  geofence_radius_km=20))
    w.u.ro.hashed_password = hash_password("Passw0rd!x")
    w.db.commit()
    login = lambda h: w.api(w.u.ro).post("/api/v1/auth/login", headers=h,  # noqa: E731
                                         json={"email": w.u.ro.email, "password": "Passw0rd!x"})
    old = login({"X-Geo-Lat": "-1.29", "X-Geo-Lng": "36.82", "X-Geo-Age": "3600"})
    assert old.status_code == 428 and old.json()["detail"]["code"] == "location_required"
    r = login({"X-Geo-Lat": "-1.29", "X-Geo-Lng": "36.82", "X-Geo-Age": "4", "X-Geo-Acc": "35"})
    assert r.status_code == 200, r.text
    log = w.db.query(AuditLog).filter_by(action="auth.login", user_id=w.u.ro.id).one()
    assert log.details["location"] == {"lat": -1.29, "lng": 36.82, "accuracy_m": 35, "age_s": 4}
    adm = User(email="tadm11@a.dcp", hashed_password="x", full_name="T", role="tenant_admin",
               tenant_id=w.A.id)
    w.db.add(adm)
    w.db.commit()
    r = w.api(adm).put("/api/v1/settings/security", json={"location_max_age_minutes": 5},
                       headers=w.H)
    assert r.status_code == 200 and r.json()["location_max_age_minutes"] == 5
    assert w.api(adm).put("/api/v1/settings/security", json={"location_max_age_minutes": 0},
                          headers=w.H).status_code == 422
