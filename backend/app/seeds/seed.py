"""
Finyl-DCP seed script — realistic Kenya-flavored demo data.

Run:  python -m app.seeds.seed
Idempotent-ish: aborts if tenants already exist (pass --force to wipe & reseed).
"""
import random
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import text

from app.core.database import Base, SessionLocal, engine, ensure_schema
from app.core.security import hash_password
from app.models import (AmlFlag, Borrower, Branch, CallLog, Complaint, CrmLead,
                        ImpactSurvey, Loan, MODULE_KEYS, PaymentTransaction, Product,
                        Region, Repayment, SiteVisit, SmsLog, Staff, Tenant,
                        TenantModule, User)

from app.seeds.credentials import SeedCredentials

random.seed(2026)
TODAY = date.today()

FIRST_M = ["Brian", "Kevin", "Dennis", "Collins", "Nelson", "Samuel", "Peter", "John", "James",
           "David", "Daniel", "Joseph", "Elijah", "Victor", "Felix", "George", "Moses", "Isaac",
           "Anthony", "Stephen", "Titus", "Kelvin", "Emmanuel", "Martin", "Patrick"]
FIRST_F = ["Mary", "Esther", "Grace", "Faith", "Mercy", "Joyce", "Ann", "Catherine", "Lucy",
           "Sarah", "Naomi", "Ruth", "Agnes", "Beatrice", "Caroline", "Diana", "Everlyne",
           "Florence", "Gladys", "Hellen", "Irene", "Janet", "Winnie", "Purity", "Susan"]
LAST = ["Mwangi", "Kamau", "Otieno", "Ochieng", "Wanjiku", "Njoroge", "Kiprop", "Cheruiyot",
        "Mutua", "Mwanzia", "Odhiambo", "Auma", "Wafula", "Barasa", "Chebet", "Kilonzo",
        "Ndungu", "Karanja", "Onyango", "Achieng", "Kariuki", "Gitau", "Koech", "Langat",
        "Muthoni", "Nyambura", "Omondi", "Owino", "Simiyu", "Wekesa"]
SECTORS = ["retail_trade", "agriculture", "boda_boda", "food_services", "salon_beauty",
           "tailoring", "hardware", "mitumba_clothing", "electronics", "dairy"]
REGION_DATA = {
    "Nairobi": ["Westlands Branch", "Eastleigh Branch", "Kawangware Branch"],
    "Central": ["Thika Branch", "Nyeri Branch"],
    "Coast": ["Mombasa CBD Branch", "Malindi Branch"],
    "Western": ["Kisumu Branch", "Kakamega Branch"],
}
# Nairobi-area coordinates for site visits
NAIROBI_GEO = (-1.2864, 36.8172)


def _seed_command_centre(db, partner, mular, pesaf, jenga):
    """WP13 demo: billing switched on, one invoice, a loan facility from Finyl,
    a few potential DCPs and tonight's figures, so every Finyl screen has data."""
    from datetime import timedelta as _td
    from app.models import PlatformLead, PlatformUserAccess
    from app.services import platform_billing, platform_lending, platform_metrics
    from app.services.platform_access import PRESETS
    db.add(PlatformUserAccess(user_id=partner.id, preset="director", title="Partner",
                              permissions=PRESETS["director"]["permissions"], all_tenants=False,
                              tenant_ids=[mular.id]))
    today = platform_metrics.today_kenya()
    first = today.replace(day=1)
    prev = (first - _td(days=1)).replace(day=1)
    for t in (mular, pesaf):
        platform_billing.switch_on(db, platform_billing.billing_for(db, t.id), prev)
    db.flush()
    platform_metrics.snapshot(db, today)
    platform_billing.generate(db, mular.id, f"{prev:%Y-%m}")
    fac = platform_lending.create(db, mular.id, {"limit_amount": 2000000, "interest_rate_pct": 2.5,
                                                 "term_months": 12, "interest_method": "reducing",
                                                 "start_date": prev.isoformat(),
                                                 "purpose": "Working capital for on-lending"}, None)
    platform_lending.drawdown(db, fac, 1000000, prev, "DEMO-DD-1", None, None)
    for name, stage, book in (("Imara Capital", "demo", 12000000), ("Tujenge Loans", "proposal", 30000000),
                              ("Mwangaza Credit", "lead", 5000000)):
        db.add(PlatformLead(company_name=name, stage=stage, expected_loan_book=book, source="Referral",
                            next_step="Follow up call", next_step_date=today + _td(days=7)))
    db.commit()


def kname(gender):
    first = random.choice(FIRST_F if gender == "female" else FIRST_M)
    middle = random.choice(LAST) if random.random() < 0.4 else None
    return first, middle, random.choice(LAST)


def kphone():
    return "07" + str(random.randint(10000000, 99999999))


def mref():
    import string
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


def seed_tenant(db, tenant, scale=1.0, disabled_modules=()):
    """Seed a full dataset for one tenant. `scale` shrinks record counts."""
    t = tenant.id
    # Module flags
    for key in MODULE_KEYS:
        db.add(TenantModule(tenant_id=t, module_key=key, enabled=key not in disabled_modules))

    # Regions & branches
    regions, branches = [], []
    for rname, brs in REGION_DATA.items():
        r = Region(tenant_id=t, name=rname)
        db.add(r); db.flush()
        regions.append(r)
        for bname in brs:
            b = Branch(tenant_id=t, region_id=r.id, name=bname)
            db.add(b); db.flush()
            branches.append(b)

    # Staff
    staff, officers, agents = [], [], []
    n_staff = int(20 * scale)
    for i in range(max(8, n_staff)):
        gender = random.choice(["male", "female"])
        fn, _, ln = kname(gender)
        role = "call_agent" if i % 4 == 3 else ("branch_manager" if i % 7 == 6 else "loan_officer")
        s = Staff(tenant_id=t, branch_id=random.choice(branches).id, name=f"{fn} {ln}",
                  role=role, phone=kphone(),
                  salary=random.choice([35000, 42000, 48000, 55000, 65000, 80000]),
                  petty_cash=random.choice([3000, 5000, 8000, 10000]),
                  hire_date=TODAY - timedelta(days=random.randint(120, 2000)))
        db.add(s); db.flush()
        staff.append(s)
        (agents if role == "call_agent" else officers).append(s)

    # Products — "Liquipay"-style short-tenure digital loans
    product_defs = [
        ("Liquipay 4-weeks loan", "L4", 8.0, 4, "weeks", "weekly", 1000, 50000),
        ("Liquipay 6-weeks loan", "L6", 10.0, 6, "weeks", "weekly", 2000, 80000),
        ("Liquipay 8-weeks loan", "L8", 12.0, 8, "weeks", "weekly", 3000, 100000),
        ("Biashara Boost 3-month", "BB3", 15.0, 3, "months", "monthly", 10000, 250000),
        ("Biashara Boost 6-month", "BB6", 22.0, 6, "months", "monthly", 20000, 500000),
        ("Kilimo Msimu loan", "KM4", 14.0, 4, "months", "monthly", 5000, 150000),
        ("Hustler Daily Float", "HDF", 6.0, 2, "weeks", "daily", 500, 20000),
    ]
    products = []
    for name, code, rate, tv, tu, freq, mn, mx in product_defs:
        p = Product(tenant_id=t, name=name, code=code, interest_rate=rate, interest_method="flat",
                    tenure_value=tv, tenure_unit=tu, repayment_frequency=freq,
                    min_amount=mn, max_amount=mx, min_age=18, max_age=65,
                    penalty_rate=round(random.uniform(0.5, 2.0), 1),
                    rules={"requires_kyc": True, "max_active_loans": 1}, active=True)
        db.add(p); db.flush()
        products.append(p)

    # Borrowers
    borrowers = []
    n_borrowers = int(200 * scale)
    for i in range(max(40, n_borrowers)):
        gender = random.choice(["male", "female"])
        fn, mid, ln = kname(gender)
        branch = random.choice(branches)
        age = random.randint(18, 65)
        b = Borrower(tenant_id=t, first_name=fn, middle_name=mid, last_name=ln,
                     national_id=str(random.randint(20000000, 39999999)), phone=kphone(),
                     gender=gender,
                     date_of_birth=TODAY - timedelta(days=age * 365 + random.randint(0, 364)),
                     region_id=branch.region_id, branch_id=branch.id,
                     business_sector=random.choice(SECTORS),
                     baseline_monthly_sales=random.choice([15000, 25000, 40000, 60000, 90000, 120000]),
                     baseline_employees=random.choice([0, 0, 1, 1, 2, 3]),
                     kyc_status=random.choices(["validated", "draft", "failed"], [0.75, 0.2, 0.05])[0],
                     credit_score=random.randint(320, 850))
        db.add(b); db.flush()
        borrowers.append(b)

    # Loans + repayments over the last 18 months
    loans, repayments = [], []
    n_loans = int(550 * scale)
    seq = 0
    # give ~35% of borrowers multiple cycles
    cycle_borrowers = random.sample(borrowers, k=int(len(borrowers) * 0.35))
    loan_plan = []
    for b in borrowers:
        loan_plan.append((b, 1))
    for b in cycle_borrowers:
        for cyc in range(2, random.choice([2, 3, 3, 4]) + 1):
            loan_plan.append((b, cyc))
    random.shuffle(loan_plan)
    loan_plan = loan_plan[:max(80, n_loans)]
    loan_plan.sort(key=lambda x: x[1])  # earlier cycles first

    for b, cycle in loan_plan:
        seq += 1
        p = random.choice(products)
        principal = round(random.uniform(float(p.min_amount), min(float(p.max_amount), 120000)), -2)
        app_offset = random.randint(0, 540 - cycle * 30) if cycle == 1 else random.randint(0, 360)
        app_date = TODAY - timedelta(days=max(3, app_offset))
        step = 7 if p.tenure_unit == "weeks" else 30
        tenure_days = step * p.tenure_value
        officer = random.choice(officers)
        status_roll = random.random()
        total_due = principal * (1 + p.interest_rate / 100)

        loan = Loan(tenant_id=t, account_number=f"FL/FY{app_date.year}/{t}/{seq}",
                    borrower_id=b.id, product_id=p.id, staff_id=officer.id, branch_id=b.branch_id,
                    principal=principal, interest_rate=p.interest_rate,
                    application_date=app_date, loan_cycle_number=cycle, outstanding_balance=0)

        if status_roll < 0.05:
            loan.status = random.choice(["pending", "underwriting"])
        elif status_roll < 0.08:
            loan.status = "rejected"
        else:
            loan.approval_date = app_date + timedelta(days=random.randint(1, 4))
            loan.disbursement_date = loan.approval_date
            loan.due_date = loan.disbursement_date + timedelta(days=tenure_days)
            paid_fraction = 1.0
            if loan.due_date > TODAY:  # still running
                elapsed = max(0, (TODAY - loan.disbursement_date).days) / tenure_days
                paid_fraction = min(1.0, max(0.0, elapsed * random.uniform(0.5, 1.1)))
                loan.status = "active"
            else:  # matured
                r2 = random.random()
                if r2 < 0.68:
                    loan.status = "paid"; paid_fraction = 1.0
                elif r2 < 0.90:
                    loan.status = "overdue"; paid_fraction = random.uniform(0.3, 0.85)
                else:
                    loan.status = "defaulted"; paid_fraction = random.uniform(0.0, 0.4)
            collected = round(total_due * paid_fraction, 2)
            loan.outstanding_balance = round(max(0, total_due - collected), 2)
            if loan.status == "paid":
                loan.outstanding_balance = 0
        db.add(loan); db.flush()
        loans.append(loan)

        # Repayments for disbursed loans
        if loan.disbursement_date and loan.status in ("active", "paid", "overdue", "defaulted"):
            collected = total_due - float(loan.outstanding_balance)
            n_inst = max(1, p.tenure_value)
            per = collected / n_inst if n_inst else 0
            paid_so_far = 0.0
            i_share = p.interest_rate / (100 + p.interest_rate)
            for i in range(n_inst):
                if paid_so_far >= collected - 1:
                    break
                amt = round(min(per * random.uniform(0.8, 1.2), collected - paid_so_far), 2)
                if amt <= 0:
                    break
                pay_dt = datetime.combine(loan.disbursement_date + timedelta(days=step * (i + 1) + random.randint(-2, 4)),
                                          datetime.min.time()) + timedelta(hours=random.randint(7, 20))
                if pay_dt.date() > TODAY:
                    break
                rp = Repayment(tenant_id=t, loan_id=loan.id, amount=amt,
                               interest_component=round(amt * i_share, 2),
                               principal_component=round(amt * (1 - i_share), 2),
                               payment_date=pay_dt,
                               method=random.choices(["mpesa_c2b", "stk_push", "cash"], [0.7, 0.2, 0.1])[0],
                               mpesa_ref=mref())
                db.add(rp)
                repayments.append(rp)
                paid_so_far += amt
            # B2C disbursement transaction + approval SMS
            db.add(PaymentTransaction(tenant_id=t, type="b2c", loan_id=loan.id, amount=principal,
                                      phone=b.phone, mpesa_ref=mref(), status="success",
                                      raw_payload={"ResultCode": 0, "note": "seeded"},
                                      created_at=datetime.combine(loan.disbursement_date, datetime.min.time())))
            db.add(SmsLog(tenant_id=t, recipient_phone=b.phone,
                          message=f"Dear {b.first_name}, your loan {loan.account_number} of KES {principal:,.0f} has been APPROVED and disbursed. Finyl-DCP.",
                          trigger_type="loan_approval", status="sent",
                          sent_at=datetime.combine(loan.disbursement_date, datetime.min.time()) + timedelta(hours=9)))

    # AML structuring pattern: pick 2 active loans, add rapid sub-threshold repayments
    struct_loans = [l for l in loans if l.status in ("active", "overdue")][:2]
    for sl in struct_loans:
        base = datetime.now() - timedelta(days=random.randint(5, 20))
        for k in range(4):
            db.add(Repayment(tenant_id=t, loan_id=sl.id, amount=random.randint(8500, 9900),
                             interest_component=800, principal_component=8500,
                             payment_date=base + timedelta(hours=k * 10),
                             method="mpesa_c2b", mpesa_ref=mref()))

    # CRM leads + site visits
    stages = ["lead", "contacted", "field_visit", "app_setup", "disbursed"]
    n_leads = int(48 * scale)
    for i in range(max(15, n_leads)):
        gender = random.choice(["male", "female"])
        fn, _, ln = kname(gender)
        stage = random.choices(stages, [0.3, 0.25, 0.2, 0.15, 0.1])[0]
        lead = CrmLead(tenant_id=t, name=f"{fn} {ln}", phone=kphone(),
                       sector=random.choice(SECTORS), region_id=random.choice(regions).id,
                       stage=stage, assigned_staff_id=random.choice(officers).id,
                       estimated_loan_amount=random.choice([10000, 25000, 50000, 80000, 150000]),
                       notes=random.choice(["Met at market day", "Referred by existing client",
                                            "Walk-in enquiry", "Chama group member", "Saw SMS campaign"]),
                       created_at=datetime.now() - timedelta(days=random.randint(1, 90)))
        db.add(lead); db.flush()
        if stage in ("field_visit", "app_setup", "disbursed") or random.random() < 0.3:
            for _ in range(random.randint(1, 2)):
                db.add(SiteVisit(tenant_id=t, lead_id=lead.id, staff_id=lead.assigned_staff_id,
                                 visit_date=TODAY - timedelta(days=random.randint(1, 60)),
                                 latitude=NAIROBI_GEO[0] + random.uniform(-0.15, 0.15),
                                 longitude=NAIROBI_GEO[1] + random.uniform(-0.15, 0.15),
                                 outcome=random.choice(["positive", "needs_follow_up", "not_viable"]),
                                 notes=random.choice(["Busy stall, good stock levels", "Business closed, retry",
                                                      "Verified premises and inventory", "Owner keen on 4-week product"])))

    # Call logs — collections calls with promises kept/broken
    open_loans = [l for l in loans if l.status in ("active", "overdue", "defaulted")]
    n_calls = int(240 * scale)
    for i in range(max(60, n_calls)):
        if not open_loans or not agents:
            break
        loan = random.choice(open_loans)
        agent = random.choice(agents)
        outcome = random.choices(["promise_to_pay", "no_answer", "paid", "dispute", "call_back", "wrong_number"],
                                 [0.35, 0.25, 0.12, 0.08, 0.15, 0.05])[0]
        call_dt = datetime.now() - timedelta(days=random.randint(0, 120), hours=random.randint(0, 10))
        cl = CallLog(tenant_id=t, agent_id=agent.id, borrower_id=loan.borrower_id, loan_id=loan.id,
                     call_date=call_dt, duration_seconds=random.randint(20, 600),
                     call_outcome=outcome,
                     notes=random.choice(["Client aware of balance", "Requested payment plan",
                                          "Promised after market day", "Disputed penalty charge", ""]))
        if outcome == "promise_to_pay":
            promise_date = (call_dt + timedelta(days=random.randint(1, 7))).date()
            cl.promise_to_pay_date = promise_date
            cl.promise_amount = random.choice([1000, 2000, 3000, 5000, 8000])
            # ~55% of promises kept: create a matching repayment near the promise date
            if random.random() < 0.55 and promise_date <= TODAY:
                db.add(Repayment(tenant_id=t, loan_id=loan.id, amount=float(cl.promise_amount),
                                 interest_component=round(float(cl.promise_amount) * 0.1, 2),
                                 principal_component=round(float(cl.promise_amount) * 0.9, 2),
                                 payment_date=datetime.combine(promise_date, datetime.min.time())
                                 + timedelta(days=random.randint(-1, 2), hours=12),
                                 method="mpesa_c2b", mpesa_ref=mref()))
        db.add(cl)

    # Complaints — spread across categories/statuses, some near/past SLA
    cats = ["system_error", "collection_harassment", "balance_dispute", "fraud", "service_quality", "other"]
    n_compl = int(28 * scale)
    for i in range(max(10, n_compl)):
        cat = random.choice(cats)
        # mix: old resolved, in-progress, fresh, and some breaching SLA
        age_days = random.choices([random.randint(1, 5), random.randint(10, 13), random.randint(15, 40)],
                                  [0.4, 0.25, 0.35])[0]
        created = datetime.now() - timedelta(days=age_days)
        b = random.choice(borrowers)
        c = Complaint(tenant_id=t, ticket_id=f"{cat.upper()}/{t}/{i + 1}", borrower_id=b.id,
                      category=cat,
                      description=random.choice([
                          "Deducted twice for the same installment via M-Pesa.",
                          "Agent called repeatedly outside allowed hours.",
                          "Loan balance does not reflect last payment.",
                          "Received SMS for a loan I never applied for.",
                          "App shows error when trying to repay.",
                          "Interest charged differs from what was quoted."]),
                      status="open", created_at=created,
                      sla_deadline=created + timedelta(days=14),
                      assigned_staff_id=random.choice(staff).id)
        roll = random.random()
        if roll < 0.5:
            c.status = random.choice(["resolved", "closed"])
            c.resolved_at = created + timedelta(days=random.randint(2, 16))
            c.remedial_action = random.choice(["Reversed duplicate charge", "Agent retrained and warned",
                                               "Balance recomputed and corrected", "Refund processed via B2C"])
        elif roll < 0.75:
            c.status = "in_progress"
        db.add(c)

    # Impact surveys for cycle-2+ borrowers — sales growth & jobs by age
    cycle2 = [l for l in loans if l.loan_cycle_number >= 2]
    n_surv = int(70 * scale)
    surveyed = set()
    si = 0
    for loan in cycle2:
        if si >= max(20, n_surv):
            break
        key = (loan.borrower_id, loan.loan_cycle_number)
        if key in surveyed:
            continue
        surveyed.add(key)
        si += 1
        b = next(x for x in borrowers if x.id == loan.borrower_id)
        pre = float(b.baseline_monthly_sales) * random.uniform(0.9, 1.3) * (1.1 ** (loan.loan_cycle_number - 2))
        growth = random.uniform(-0.05, 0.6)
        post = pre * (1 + growth)
        db.add(ImpactSurvey(tenant_id=t, survey_id=f"IMP/{t}/{si}", borrower_id=b.id, loan_id=loan.id,
                            loan_cycle_number=loan.loan_cycle_number,
                            monthly_sales_pre=round(pre, 2), monthly_sales_post=round(post, 2),
                            jobs_created=random.choices([0, 1, 2, 3], [0.4, 0.35, 0.18, 0.07])[0],
                            sales_improved=growth > 0.02,
                            next_capital_plan=random.choice([
                                "Add a second stall at the market", "Buy a chest freezer for cold drinks",
                                "Stock more fast-moving items", "Hire one more assistant",
                                "Buy a motorbike for deliveries", "Open agrovet corner shop"]),
                            survey_date=loan.application_date))

    # A few extra manual SMS + reminders in the log
    for _ in range(int(30 * scale)):
        b = random.choice(borrowers)
        db.add(SmsLog(tenant_id=t, recipient_phone=b.phone,
                      message=f"Hi {b.first_name}, your loan installment is due soon. Pay via Paybill 888999. Finyl-DCP.",
                      trigger_type=random.choice(["repayment_reminder", "overdue_alert", "manual"]),
                      status="sent", sent_at=datetime.now() - timedelta(days=random.randint(0, 60))))

    db.flush()
    return {"staff": staff, "borrowers": borrowers, "loans": loans}


def _apply_migrations() -> None:
    """WP7 (review 7.12): build the schema with the numbered migrations, the
    same way every real environment does, instead of SQLAlchemy create_all
    (which made tables the migrations never recorded and hid missing ones)."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_migrations.py"
    spec = importlib.util.spec_from_file_location("finyl_run_migrations", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if mod.main() != 0:
        raise SystemExit("Migrations failed; not seeding.")


def _seed_books(db, mular, mk):
    """WP14 demo: Finyl's own workspace, finance logins, and Mular Credit's books
    (every loan and payment posted, a supplier bill paid, a laptop bought, a
    staff expense claim waiting for approval)."""
    from datetime import timedelta as _td
    from app.models import AccSupplier, ChartOfAccount, TenantModule
    from app.services import acc_ops, books_sources, ledger, payables

    finyl = Tenant(name="Finyl (Afridon IT Solutions)", code="FINYL", logo_color="#0F172A", active=True,
                   is_internal=True)
    db.add(finyl)
    db.flush()
    for key in ("dashboard", "accounting"):
        db.add(TenantModule(tenant_id=finyl.id, module_key=key, enabled=True))
    accountant = mk("accountant@mularcredit.co.ke", "Grace Akinyi", "accountant", mular.id)
    finance = mk("finance@mularcredit.co.ke", "Peter Mutua", "finance_manager", mular.id)
    finyl_fin = mk("accounts@finyl.app", "Finyl Accounts (demo)", "finance_manager", finyl.id)
    db.add_all([accountant, finance, finyl_fin])
    db.commit()

    for tid in (mular.id, finyl.id):
        for _ in range(40):                 # posts in batches of 500 per kind
            out = books_sources.sync(db, tid, limit=2000, force=True)
            db.commit()
            if not out.get("posted"):
                break

    b = ledger.Books(db, mular.id)
    from sqlalchemy import func as _f
    from app.models import Loan as _Loan
    first = db.query(_f.min(_Loan.disbursement_date)).filter(_Loan.tenant_id == mular.id).scalar() or ledger.today()
    capital = ledger.post_journal(db, mular.id, entry_date=first - _td(days=1), description="Share capital paid in by the owners",
                                  lines=[ledger.line(b.account("bank"), debit=20000000),
                                         ledger.line(db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == mular.id,
                                                                                      ChartOfAccount.code == "3000").one(),
                                                     credit=20000000)],
                                  kind="manual", user=finance, reference="CAPITAL")
    capital.approved_by_user_id = finance.id
    # The DCP keeps its M-Pesa B2C float topped up from the bank (one entry for the demo).
    b2c = b.account("mpesa_disbursement")
    paid_out = -ledger.account_balance(db, mular.id, b2c.id)
    if paid_out > 0:
        top = ledger.post_journal(db, mular.id, entry_date=first, description="M-Pesa B2C float top-ups from the bank",
                                  lines=[ledger.line(b2c, debit=paid_out + 50000), ledger.line(b.account("bank"), credit=paid_out + 50000)],
                                  kind="manual", user=finance, reference="FLOAT")
        top.approved_by_user_id = finance.id
    acct = lambda code: db.query(ChartOfAccount).filter(ChartOfAccount.tenant_id == mular.id,  # noqa: E731
                                                       ChartOfAccount.code == code).one()
    today = ledger.today()
    landlord = AccSupplier(tenant_id=mular.id, name="Kilimani Properties Ltd", kra_pin="P051234567X",
                           payment_method="bank", bank_name="KCB", bank_account="1234567890",
                           default_expense_account_id=acct("6100").id, wht_rate=0)
    auditor = AccSupplier(tenant_id=mular.id, name="Kamau & Partners CPA", kra_pin="P052223334Y",
                          payment_method="bank", default_expense_account_id=acct("6160").id, wht_rate=5)
    db.add_all([landlord, auditor])
    db.flush()
    rent = payables.create_bill(db, mular.id, user=accountant, supplier=landlord, bill_date=today.replace(day=1),
                                due_date=today.replace(day=5), supplier_ref="RENT-" + today.strftime("%b%y").upper(),
                                description="Office rent", branch_id=None,
                                lines=[{"account_id": acct("6100").id, "amount": 85000, "vat_rate": 16}])
    payables.approve_bill(db, rent, finance)
    payables.create_payment(db, mular.id, user=accountant, kind="bill", target_id=rent.id, amount=rent.total,
                            paid_on=today.replace(day=1), account_id=b.account("bank").id, method="bank",
                            reference="EFT-RENT", auto_approve=True)
    payables.create_bill(db, mular.id, user=accountant, supplier=auditor, bill_date=today - _td(days=3),
                         due_date=today + _td(days=27), supplier_ref="KP-2231", description="Audit fee (interim)",
                         branch_id=None, lines=[{"account_id": acct("6160").id, "amount": 120000, "vat_rate": 16}])
    acc_ops.ensure_categories(db, mular.id)
    from app.models import AccAssetCategory, User as _U
    comp = db.query(AccAssetCategory).filter(AccAssetCategory.tenant_id == mular.id,
                                             AccAssetCategory.name.like("Computers%")).first()
    acc_ops.register_asset(db, mular.id, user=accountant, category=comp, name="Laptops for loan officers (x4)",
                           purchase_date=today.replace(day=1) - _td(days=45), in_service_date=None, cost=240000,
                           funding="paid", paid_from_id=b.account("bank").id, custodian="IT desk")
    officer = db.query(_U).filter(_U.email == "officer@mularcredit.co.ke").first()
    if officer:
        payables.create_claim(db, mular.id, user=officer, claim_date=today, description="Field visits, Kawangware",
                              branch_id=None, lines=[{"account_id": acct("6130").id, "amount": 1800,
                                                      "description": "Boda boda fares"}])
    db.commit()


def _seed_hr(db, mular, mk):
    """WP15 demo: an HR manager, employees from Mular's staff records (logins
    linked), last month's payroll approved and paid, a leave request waiting."""
    import random as _r
    from datetime import date as _date, timedelta as _td
    from app.models import HrEmployee, HrLeaveRequest, HrLeaveType, HrPayItem, TenantModule, User as _U
    from app.services import hr as H, ledger, payroll as P

    finyl = db.query(Tenant).filter(Tenant.code == "FINYL").first()
    if finyl and not db.query(TenantModule).filter(TenantModule.tenant_id == finyl.id,
                                                   TenantModule.module_key == "hrm").first():
        db.add(TenantModule(tenant_id=finyl.id, module_key="hrm", enabled=True))
    hrm = mk("hr@mularcredit.co.ke", "Mercy Wambui", "hr_manager", mular.id)
    db.add(hrm)
    db.commit()
    H.ensure_leave_types(db, mular.id)
    rng = _r.Random(15)
    users_by_staff = {u.staff_id: u for u in db.query(_U).filter(_U.tenant_id == mular.id, _U.staff_id.isnot(None))}
    others = {u.email: u for u in db.query(_U).filter(_U.tenant_id == mular.id)}
    today = ledger.today()
    managers = {}
    staff = db.query(Staff).filter(Staff.tenant_id == mular.id).order_by(Staff.id).all()
    female = set(FIRST_F) | {"Mercy", "Grace", "Cynthia", "Faith"}
    for st in staff:
        login = users_by_staff.get(st.id)
        first, _, last = (login.full_name if login else st.name).partition(" ")
        e = HrEmployee(tenant_id=mular.id, employee_no=H.next_employee_no(db, mular.id), first_name=first,
                       last_name=last or first, staff_id=st.id, branch_id=st.branch_id,
                       job_title={"loan_officer": "Loan Officer", "call_agent": "Call Centre Agent",
                                  "branch_manager": "Branch Manager"}.get(st.role, st.role.replace("_", " ").title()),
                       department="Operations" if st.role != "call_agent" else "Customer Care",
                       employment_type="permanent", hire_date=st.hire_date or _date(2024, 1, 15),
                       basic_salary=st.salary or 40000, pay_method=rng.choice(["bank", "bank", "mpesa"]),
                       bank_name="Equity Bank", bank_branch="Kenyatta Avenue",
                       bank_account=f"01802{rng.randint(10**7, 10**8 - 1)}", mpesa_phone=f"07{rng.randint(10**7, 10**8 - 1)}",
                       kra_pin=f"A{rng.randint(10**8, 10**9 - 1)}{chr(65 + rng.randint(0, 25))}",
                       national_id=str(rng.randint(2 * 10**7, 4 * 10**7)), nssf_no=str(rng.randint(10**8, 10**9)),
                       shif_no=f"CR{rng.randint(10**9, 10**10)}", gender="female" if first in female else "male",
                       date_of_birth=_date(rng.randint(1975, 2000), rng.randint(1, 12), rng.randint(1, 28)),
                       user_id=getattr(users_by_staff.get(st.id), "id", None), status="active")
        db.add(e)
        db.flush()
        if st.role == "branch_manager":
            managers[st.branch_id] = e.id
        if rng.random() < 0.5:
            db.add(HrPayItem(tenant_id=mular.id, employee_id=e.id, kind="allowance", name="House allowance",
                             amount=rng.choice([5000, 8000, 10000]), taxable=True))
        if rng.random() < 0.3:
            db.add(HrPayItem(tenant_id=mular.id, employee_id=e.id, kind="deduction", name="Stima SACCO",
                             amount=rng.choice([2000, 3000, 5000]), taxable=False))
    for e in db.query(HrEmployee).filter(HrEmployee.tenant_id == mular.id):
        m = managers.get(e.branch_id)
        if m and m != e.id:
            e.manager_employee_id = m
    # HR's own record, linked to the HR manager's login
    db.add(HrEmployee(tenant_id=mular.id, employee_no=H.next_employee_no(db, mular.id), first_name="Mercy",
                      last_name="Wambui", job_title="HR Manager", department="Human Resources",
                      hire_date=_date(2023, 6, 1), basic_salary=120000, pay_method="bank", bank_name="KCB",
                      bank_account="1200456789", kra_pin="A009876543Z", gender="female", user_id=hrm.id))
    for email, title in (("officer@mularcredit.co.ke", "Loan Officer"), ("accountant@mularcredit.co.ke", "Accountant"),
                         ("finance@mularcredit.co.ke", "Finance Manager")):
        u = others.get(email)
        if u and not db.query(HrEmployee).filter(HrEmployee.user_id == u.id).first():
            fn, _, ln = u.full_name.partition(" ")
            db.add(HrEmployee(tenant_id=mular.id, employee_no=H.next_employee_no(db, mular.id), first_name=fn,
                              last_name=ln or fn, job_title=title, department="Finance" if "Fin" in title or "Acc" in title else "Operations",
                              hire_date=_date(2024, 3, 1), basic_salary={"Loan Officer": 45000, "Accountant": 85000}.get(title, 150000),
                              pay_method="bank", bank_name="NCBA", bank_account=f"77{rng.randint(10**7, 10**8 - 1)}",
                              kra_pin=f"A{rng.randint(10**8, 10**9 - 1)}K", gender="female" if fn in female else "male",
                              user_id=u.id))
    db.commit()
    # Last month's payroll: prepared by HR, approved and paid by finance
    finance = others.get("finance@mularcredit.co.ke")
    prev = (today.replace(day=1) - _td(days=1)).replace(day=1)
    run = P.create_run(db, mular.id, prev, hrm)
    if finance and run.employees:
        P.approve_run(db, run, finance)
        b = ledger.Books(db, mular.id)
        P.pay_run(db, run, paid_on=min(today, prev.replace(day=28) + _td(days=3)), account_id=b.account("bank").id, user=finance)
    officer = others.get("officer@mularcredit.co.ke")
    emp = db.query(HrEmployee).filter(HrEmployee.user_id == getattr(officer, "id", -1)).first()
    annual = db.query(HrLeaveType).filter(HrLeaveType.tenant_id == mular.id, HrLeaveType.name == "Annual leave").first()
    if emp and annual:
        start = today + _td(days=(7 - today.weekday()) + 7)
        db.add(HrLeaveRequest(tenant_id=mular.id, employee_id=emp.id, leave_type_id=annual.id, start_date=start,
                              end_date=start + _td(days=2), days=3, reason="Family visit in Kisii", status="pending",
                              requested_by_user_id=officer.id))
    db.commit()


def _seed_lending_plus(db, mular):
    """WP16 demo: a group with members and a meeting, collateral on a few
    loans, a write-off request waiting for a second manager."""
    from datetime import timedelta as _td
    from app.models import (Borrower, LendingGroup, LendingGroupMeeting, LendingGroupMember, Loan, LoanCollateral,
                            User as _U)
    from app.services import lending_plus as LP, ledger
    today = ledger.today()
    clients = db.query(Borrower).filter(Borrower.tenant_id == mular.id).order_by(Borrower.id).limit(6).all()
    if len(clients) >= 4:
        g = LendingGroup(tenant_id=mular.id, code="GRP-0001", name="Umoja Women Self-Help Group",
                         branch_id=clients[0].branch_id, meeting_day="Tuesday", meeting_frequency="weekly",
                         meeting_place="Kawangware Social Hall", registration_no="SHG/NRB/2024/118",
                         formed_on=today - _td(days=400), status="active")
        db.add(g)
        db.flush()
        for i, c in enumerate(clients[:5]):
            db.add(LendingGroupMember(tenant_id=mular.id, group_id=g.id, borrower_id=c.id,
                                      role=["chair", "secretary", "treasurer", "member", "member"][i],
                                      joined_on=today - _td(days=380), active=True))
            for ln in db.query(Loan).filter(Loan.borrower_id == c.id, Loan.status.in_(("active", "overdue"))):
                ln.group_id = g.id
        db.add(LendingGroupMeeting(tenant_id=mular.id, group_id=g.id, held_on=today - _td(days=(today.weekday() - 1) % 7 or 7),
                                   present=[c.id for c in clients[:4]], collected=12500,
                                   notes="Two members paid in full; one asked for more time"))
    for ln in db.query(Loan).filter(Loan.tenant_id == mular.id, Loan.status.in_(("active", "overdue"))).limit(3):
        db.add(LoanCollateral(tenant_id=mular.id, borrower_id=ln.borrower_id, loan_id=ln.id, kind="business_equipment",
                              description="Posho mill and generator", identifier=f"SN-{ln.id:05d}",
                              estimated_value=float(ln.principal) * 2, forced_sale_value=float(ln.principal) * 1.2,
                              valued_by="Branch manager", valued_on=today - _td(days=60), location="Client's shop",
                              received_on=today - _td(days=60), status="held"))
    bm = db.query(_U).filter(_U.tenant_id == mular.id, _U.role.in_(("branch_manager", "tenant_admin"))) \
        .order_by(_U.role).first()
    bad = (db.query(Loan).filter(Loan.tenant_id == mular.id, Loan.status.in_(("defaulted", "overdue")))
           .order_by(Loan.due_date).first())
    if bm and bad:
        LP.request_action(db, mular.id, bad, "writeoff", {}, "No payment for over 180 days; client relocated, phone off", bm)
    db.commit()


def _seed_crm_plus(db, mular):
    """WP17 demo: contact logged with clients and leads, follow-ups (some
    overdue), marketing consent for half the clients, three segments, a sent
    service campaign and an offer campaign waiting for a manager's approval."""
    import random as _r
    from datetime import datetime as _dt, timedelta as _td
    from app.models import (Borrower, CrmCampaign, CrmInteraction, CrmLead, CrmSegment, CrmTask, KycConsent,
                            User as _U)
    from app.services import crm_plus as C, ledger
    rnd = _r.Random(17)
    today = ledger.today()
    users = {u.email: u for u in db.query(_U).filter(_U.tenant_id == mular.id)}
    officer = users.get("officer@mularcredit.co.ke")
    ro = users.get("ro@mularcredit.co.ke") or officer
    bm = users.get("branchmgr@mularcredit.co.ke")
    admin = users.get("admin@mularcredit.co.ke")
    if not (officer and bm and admin):
        return
    clients = db.query(Borrower).filter(Borrower.tenant_id == mular.id).order_by(Borrower.id).all()
    for i, b in enumerate(clients):
        if i % 2 == 0:
            db.add(KycConsent(tenant_id=mular.id, borrower_id=b.id, consent_data_processing=True, consent_credit_check=True,
                              consent_marketing=True, consent_version="2026-01", consented_at=_dt.utcnow() - _td(days=30)))
    notes = [("call", "Asked about a bigger loan", "Wants KES 40,000 to buy stock before December", "interested"),
             ("visit", "Shop visit", "Stall busy, stock well kept. Spouse helps on weekends", "reached"),
             ("call", "Reminder about Friday's instalment", "Said she will pay on Thursday evening", "promise"),
             ("sms", "Sent paybill details", None, "reached"),
             ("call", "Follow-up on application", "Phone off", "no_answer"),
             ("whatsapp", "Sent photos of new stock", "Client sent photos; looks genuine", "reached")]
    for b in clients[:24]:
        for k in range(rnd.randint(1, 3)):
            kind, subj, note, outcome = rnd.choice(notes)
            who = rnd.choice([officer, ro])
            db.add(CrmInteraction(tenant_id=mular.id, borrower_id=b.id, kind=kind, direction="out", subject=subj, notes=note,
                                  outcome=outcome, user_id=who.id,
                                  happened_at=_dt.utcnow() - _td(days=rnd.randint(1, 45), hours=rnd.randint(0, 8))))
    titles = ["Call to remind about the next instalment", "Visit the shop with the top-up form", "Collect a copy of the business permit",
              "Check whether the new stock arrived", "Ask for a guarantor's ID copy"]
    for i, b in enumerate(clients[:14]):
        db.add(CrmTask(tenant_id=mular.id, title=titles[i % len(titles)], due_date=today + _td(days=rnd.randint(-4, 6)),
                       priority=rnd.choice(["normal", "normal", "high", "low"]), status="open",
                       assigned_user_id=(officer if i % 2 == 0 else ro).id, borrower_id=b.id, created_by_user_id=bm.id))
    for i, b in enumerate(clients[14:18]):
        db.add(CrmTask(tenant_id=mular.id, title=titles[i % len(titles)], due_date=today - _td(days=rnd.randint(2, 20)),
                       status="done", assigned_user_id=officer.id, borrower_id=b.id, created_by_user_id=officer.id,
                       completed_at=_dt.utcnow() - _td(days=1), completion_note="Done; client co-operative"))
    for ld in db.query(CrmLead).filter(CrmLead.tenant_id == mular.id, CrmLead.stage == "disbursed",
                                       CrmLead.conversion_triggered_at.is_(None)):
        ld.conversion_triggered_at = ld.created_at + _td(days=rnd.randint(5, 21))
    for ld in db.query(CrmLead).filter(CrmLead.tenant_id == mular.id).order_by(CrmLead.id).limit(10):
        db.add(CrmInteraction(tenant_id=mular.id, lead_id=ld.id, kind=rnd.choice(["call", "visit", "meeting"]), direction="out",
                              subject="First conversation", notes=f"{ld.notes or 'Enquiry'}. Explained the 4-week product and fees.",
                              outcome="interested", user_id=officer.id, happened_at=_dt.utcnow() - _td(days=rnd.randint(1, 20))))
        if ld.stage in ("lead", "contacted"):
            db.add(CrmTask(tenant_id=mular.id, title="Book a field visit", due_date=today + _td(days=rnd.randint(0, 5)),
                           assigned_user_id=officer.id, lead_id=ld.id, created_by_user_id=officer.id))
    db.flush()
    segs = [("Repaid, idle 30 days", "Good payers with no loan now: offer a repeat loan",
             {"loan_state": "repaid_no_open", "dormant_days": 30}),
            ("Behind on a loan", "Anyone with a loan past its due date", {"loan_state": "in_arrears"}),
            ("Women in retail", None, {"gender": "female", "sector": "retail"})]
    made = []
    for name, desc, rules in segs:
        s = CrmSegment(tenant_id=mular.id, name=name, description=desc, rules=C.clean_rules(rules), created_by_user_id=bm.id)
        db.add(s)
        made.append(s)
    db.flush()
    sent = CrmCampaign(tenant_id=mular.id, name="Paybill reminder", segment_id=made[1].id, purpose="service",
                       message="Hi {{first_name}}, a reminder that {{dcp_name}} payments go to paybill {{paybill}} with your ID number as the account.",
                       status="approved", created_by_user_id=bm.id, approved_by_user_id=admin.id,
                       approved_at=_dt.utcnow() - _td(days=3), created_at=_dt.utcnow() - _td(days=3))
    db.add(sent)
    db.flush()
    C.send_campaign(db, sent)
    sent.started_at = sent.finished_at = _dt.utcnow() - _td(days=3)
    db.add(CrmCampaign(tenant_id=mular.id, name="December repeat-loan offer", segment_id=made[0].id, purpose="marketing",
                       message="Hi {{first_name}}, thank you for repaying on time. {{dcp_name}} has a December offer for you: apply on the app or call your officer.",
                       status="pending", created_by_user_id=bm.id, send_at=None))
    db.commit()


def main(force=False):
    ensure_schema()
    _apply_migrations()
    db = SessionLocal()
    try:
        if db.query(Tenant).count() > 0:
            if not force:
                print("Tenants already exist — pass --force to wipe & reseed.")
                return
            print("Wiping existing data...")
            for tbl in reversed(Base.metadata.sorted_tables):
                db.execute(text(f'TRUNCATE TABLE "{tbl.name}" RESTART IDENTITY CASCADE'))
            db.commit()

        tenants_cfg = [
            # Mular is the licensed / compliant DCP — CBK Reporting stays ON.
            ("Mular Credit", "MULAR", "#10B981", 1.0, ()),
            # PesaFlow & Jenga are non-compliant DCPs — CBK Reporting OFF by default
            # (a DCP must be CBK-licensed before it can file reports).
            ("PesaFlow Capital", "PESAF", "#0D9488", 0.45, ("cbk_reporting",)),
            ("Jenga Micro", "JENGA", "#F59E0B", 0.3, ("crm", "impact", "cbk_reporting")),  # flags OFF to demo enforcement
        ]
        all_t = []
        for name, code, color, scale, disabled in tenants_cfg:
            tenant = Tenant(name=name, code=code, logo_color=color, active=True)
            db.add(tenant); db.flush()
            print(f"Seeding tenant {name} (scale {scale}, disabled={disabled})...")
            seed_tenant(db, tenant, scale=scale, disabled_modules=disabled)
            all_t.append(tenant)
        db.commit()

        # Users
        mular, pesaf, jenga = all_t
        creds = SeedCredentials()

        def _mk(email, full_name, role, tenant_id):
            # AUTH-03: per-user password (or SEED_DEFAULT_PASSWORD), recorded to a
            # gitignored file — never printed — and force_password_reset=True.
            return User(email=email, hashed_password=hash_password(creds.password_for(email)),
                        full_name=full_name, role=role, tenant_id=tenant_id,
                        force_password_reset=True)

        users = [
            _mk("superadmin@finyl.app", "Finyl Super Admin", "super_admin", mular.id),
            _mk("admin@mularcredit.co.ke", "Brandon Otieno", "tenant_admin", mular.id),
            _mk("officer@mularcredit.co.ke", "Nelson Mwanzia", "loan_officer", mular.id),
            _mk("agent@mularcredit.co.ke", "Cynthia Wanjiru", "call_agent", mular.id),
            _mk("admin@pesaflow.co.ke", "Kevin Kiprop", "tenant_admin", pesaf.id),
            _mk("admin@jengamicro.co.ke", "Faith Chebet", "tenant_admin", jenga.id),
        ]
        # Link officer/agent users to staff records for scorecard attribution
        officer_staff = db.query(Staff).filter(Staff.tenant_id == mular.id, Staff.role == "loan_officer").first()
        agent_staff = db.query(Staff).filter(Staff.tenant_id == mular.id, Staff.role == "call_agent").first()
        users[2].staff_id = officer_staff.id if officer_staff else None
        users[3].staff_id = agent_staff.id if agent_staff else None
        db.add_all(users)
        # WP13: a Finyl partner who sees Mular Credit only (the super admin can
        # reassign at any time under Finyl → Finyl people).
        partner = _mk("director@finyl.app", "Finyl Partner (demo)", "platform_user", None)
        db.add(partner)
        db.commit()
        _seed_command_centre(db, partner, mular, pesaf, jenga)
        _seed_books(db, mular, _mk)
        _seed_hr(db, mular, _mk)
        _seed_lending_plus(db, mular)

        # Fill the KYC onboarding fields (ID details, wallets, next of kin)
        from app.seeds.client_kyc import enrich
        print("Client KYC enrichment:", enrich(db))

        # RBAC demo users, approval thresholds and portfolio/scoping wiring
        from app.seeds.rbac_seed import seed_rbac
        print("RBAC seed:", seed_rbac(db))
        _seed_crm_plus(db, mular)   # WP17 (needs the RBAC users)

        # Run AML scan for each tenant to materialise flags from the structuring seeds
        from app.services.aml import run_aml_scan
        for tenant in all_t:
            created = run_aml_scan(db, tenant.id)
            print(f"AML scan {tenant.name}: {len(created)} flags")

        # Summary
        for tbl in ["tenants", "users", "regions", "branches", "staff", "products", "borrowers",
                    "loans", "repayments", "crm_leads", "site_visits", "call_logs", "complaints",
                    "impact_surveys", "sms_logs", "payment_transactions", "aml_flags",
                    "client_mobile_wallets", "client_next_of_kin", "client_documents"]:
            n = db.execute(text(f"SELECT count(*) FROM {tbl}")).scalar()
            print(f"  {tbl}: {n}")
        print("Base seed credentials:", creds.flush("seed.py base users"))
        print("Seed complete. Credentials written to the gitignored "
              "storage/seed_credentials.txt file (passwords are never printed). "
              "All seeded users must reset their password on first login.")
    finally:
        db.close()


if __name__ == "__main__":
    main(force="--force" in sys.argv)
