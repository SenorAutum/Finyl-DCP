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


def main(force=False):
    ensure_schema()
    Base.metadata.create_all(bind=engine)
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
        db.commit()

        # Fill the KYC onboarding fields (ID details, wallets, next of kin)
        from app.seeds.client_kyc import enrich
        print("Client KYC enrichment:", enrich(db))

        # RBAC demo users, approval thresholds and portfolio/scoping wiring
        from app.seeds.rbac_seed import seed_rbac
        print("RBAC seed:", seed_rbac(db))

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
