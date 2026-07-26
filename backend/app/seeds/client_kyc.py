"""
Seed extension — fills the KYC onboarding fields for client records.

Used two ways:
  * from app/seeds/seed.py during a fresh seed, and
  * standalone as a backfill against an already-seeded database:

        python -m app.seeds.client_kyc            # all tenants
        python -m app.seeds.client_kyc --force    # re-enrich even if populated

Idempotent: a client that already has a serial_number is skipped unless --force.
"""
import random
import sys
from datetime import date, timedelta

from app.core.database import SessionLocal
from app.models import (Borrower, ClientMobileWallet, ClientNextOfKin,
                        NEXT_OF_KIN_RELATIONSHIPS, WALLET_OPERATORS)

random.seed(2026)
TODAY = date.today()

# Real Kenyan administrative units, so demo records read plausibly.
ADMIN_UNITS = [
    # (district / county, division, location, sub_location, registration centre)
    ("Nairobi", "Westlands", "Kangemi", "Sodom", "Nairobi"),
    ("Nairobi", "Dagoretti", "Riruta", "Kawangware", "Nairobi"),
    ("Nairobi", "Embakasi", "Kayole", "Matopeni", "Nairobi"),
    ("Kiambu", "Thika", "Kamenu", "Makongeni", "Thika"),
    ("Nyeri", "Tetu", "Aguthi", "Gatitu", "Nyeri"),
    ("Mombasa", "Mvita", "Majengo", "Tononoka", "Mombasa"),
    ("Kilifi", "Malindi", "Shella", "Barani", "Malindi"),
    ("Kisumu", "Winam", "Kondele", "Manyatta", "Kisumu"),
    ("Kakamega", "Lurambi", "Butsotso", "Shirere", "Kakamega"),
    ("Machakos", "Kathiani", "Mitaboni", "Kauti", "Machakos"),
    ("Nakuru", "Nakuru Town", "Kaptembwo", "Rhonda", "Nakuru"),
    ("Uasin Gishu", "Eldoret East", "Kapsoya", "Kimumu", "Eldoret"),
]
CREDIT_RATINGS = ["A", "A-", "B+", "B", "B-", "C+", "C", "CRB clear"]
ADDRESSES = ["P.O. Box 1234, Nairobi", "P.O. Box 88, Thika", "Kawangware, Nairobi",
             "Bamburi, Mombasa", "Milimani, Kisumu", "Kondele, Kisumu",
             "Shirere, Kakamega", "Kaptembwo, Nakuru", "Kimumu, Eldoret"]
ONBOARDERS = ["Brandon Mwanzia", "Grace Njeri", "Peter Kamau", "Mercy Wanjala", "Kevin Otieno"]


def _wallet_number(phone: str) -> str:
    """Wallet/till account number — for M-Pesa it mirrors the MSISDN."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return digits or str(random.randint(700000000, 799999999))


def enrich(db, force: bool = False, tenant_ids: list[int] | None = None) -> dict:
    q = db.query(Borrower)
    if tenant_ids:
        q = q.filter(Borrower.tenant_id.in_(tenant_ids))
    clients = q.all()

    touched = wallets = kin = 0
    for c in clients:
        if c.serial_number and not force:
            continue
        district, division, location, sub_location, issue_place = random.choice(ADMIN_UNITS)
        dob = c.date_of_birth or (TODAY - timedelta(days=random.randint(20, 55) * 365))
        # ID issued between the 18th birthday and 3 years ago.
        earliest = dob + timedelta(days=18 * 365)
        latest = TODAY - timedelta(days=365 * 3)
        issue = (earliest + timedelta(days=random.randint(0, max(1, (latest - earliest).days)))
                 if latest > earliest else latest)

        c.serial_number = str(random.randint(100000000, 999999999))
        c.district_of_birth = district
        c.place_of_issue = issue_place
        c.date_of_issue = issue
        c.district = district
        c.division = division
        c.location = location
        c.sub_location = sub_location
        c.current_credit_rating = random.choice(CREDIT_RATINGS)
        c.is_active = True
        c.onboarded_by = random.choice(ONBOARDERS)
        # ~70% have passed the M-Pesa name lookup, mirroring real onboarding funnels
        if random.random() < 0.7:
            c.mpesa_validated = True
            c.mpesa_validation_name = c.full_name.upper()
            c.mpesa_validated_at = TODAY - timedelta(days=random.randint(1, 300))
            c.ekyc_status = "verified"
            c.ekyc_reference = f"IDM-{random.randint(10**9, 10**10 - 1)}"
            c.ekyc_checked_at = c.mpesa_validated_at
        else:
            c.mpesa_validated = False
        touched += 1

        # 1-2 mobile wallets each (primary M-Pesa + occasional second operator)
        if force or not c.wallets:
            for existing in list(c.wallets):
                db.delete(existing)
            db.add(ClientMobileWallet(tenant_id=c.tenant_id, client_id=c.id,
                                      mobile_number=c.phone, wallet_number=_wallet_number(c.phone),
                                      operator="M-Pesa", active=True))
            wallets += 1
            if random.random() < 0.35:
                alt = "07" + str(random.randint(10000000, 99999999))
                db.add(ClientMobileWallet(tenant_id=c.tenant_id, client_id=c.id,
                                          mobile_number=alt, wallet_number=_wallet_number(alt),
                                          operator=random.choice(WALLET_OPERATORS[1:]),
                                          active=random.random() < 0.7))
                wallets += 1

        # Next of kin for ~80% of clients
        if (force or not c.next_of_kin) and random.random() < 0.8:
            for existing in list(c.next_of_kin):
                db.delete(existing)
            db.add(ClientNextOfKin(
                tenant_id=c.tenant_id, client_id=c.id,
                full_name=f"{random.choice(['James', 'Mary', 'Peter', 'Alice', 'Joseph', 'Esther'])} {c.last_name}",
                relationship_type=random.choice(NEXT_OF_KIN_RELATIONSHIPS),
                mobile_number="07" + str(random.randint(10000000, 99999999)),
                national_id=str(random.randint(20000000, 39999999)),
                address=random.choice(ADDRESSES), active=True))
            kin += 1

    db.commit()
    return {"clients_enriched": touched, "wallets_created": wallets, "next_of_kin_created": kin}


def main(force: bool = False):
    db = SessionLocal()
    try:
        stats = enrich(db, force=force)
        print("Client KYC enrichment:", stats)
    finally:
        db.close()


if __name__ == "__main__":
    main(force="--force" in sys.argv)
