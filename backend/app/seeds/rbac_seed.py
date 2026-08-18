"""
Additive RBAC demo seeder — does NOT wipe existing data.

Creates the seven per-role demo users under Mular Credit, wires their
branch/region/staff scope, seeds approval thresholds (loan-approval limits +
maker-checker money thresholds), assigns a batch of Mular clients/loans to the
Relationship Officer so portfolio scoping is demonstrable, and parks a few
client profiles as pending_approval so the Branch Manager inbox is non-empty.

Run:  python -m app.seeds.rbac_seed
Idempotent: re-running only fills gaps (skips users that already exist).
"""
from datetime import datetime

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (ApprovalThreshold, Borrower, Branch, Loan, Region, Staff,
                        Tenant, User)
from app.seeds.credentials import SeedCredentials

# Module-level collector so both seed_rbac() and main() share one credential set.
_CREDS = SeedCredentials()


def _get_or_create_user(db, tenant_id, email, full_name, role, **scope):
    u = db.query(User).filter(User.email == email).first()
    if u:
        # keep scope fresh on re-run
        u.role = role
        for k, v in scope.items():
            setattr(u, k, v)
        return u, False
    # AUTH-03: per-user password (or SEED_DEFAULT_PASSWORD), recorded to a
    # gitignored file — never printed — and force_password_reset=True.
    u = User(email=email, hashed_password=hash_password(_CREDS.password_for(email)),
             full_name=full_name, role=role, tenant_id=tenant_id, active=True,
             force_password_reset=True, **scope)
    db.add(u)
    return u, True


def seed_rbac(db):
    mular = db.query(Tenant).filter(Tenant.name == "Mular Credit").first()
    if not mular:
        raise SystemExit("Mular Credit tenant not found — run the base seeder first.")
    tid = mular.id

    # Pick concrete org anchors ------------------------------------------------
    nairobi = db.query(Region).filter(Region.tenant_id == tid, Region.name == "Nairobi").first()
    westlands = db.query(Branch).filter(Branch.tenant_id == tid, Branch.name == "Westlands Branch").first()
    # Relationship-officer staff record inside Westlands
    ro_staff = (db.query(Staff)
                .filter(Staff.tenant_id == tid, Staff.role == "loan_officer",
                        Staff.branch_id == westlands.id).first()
                if westlands else None)
    if ro_staff is None:  # fall back to any loan officer
        ro_staff = db.query(Staff).filter(Staff.tenant_id == tid, Staff.role == "loan_officer").first()

    region_id = nairobi.id if nairobi else None
    branch_id = westlands.id if westlands else None

    created = []
    demo = [
        ("sysadmin@mularcredit.co.ke", "Sysadmin Mwangi", "system_admin", {}),
        ("ro@mularcredit.co.ke", "Rita Officer", "relationship_officer",
         {"staff_id": ro_staff.id if ro_staff else None,
          "branch_id": branch_id, "region_id": region_id}),
        ("branchmgr@mularcredit.co.ke", "Brian Manager", "branch_manager",
         {"branch_id": branch_id, "region_id": region_id}),
        ("regionalmgr@mularcredit.co.ke", "Regina Regional", "regional_manager",
         {"region_id": region_id}),
        ("disburse@mularcredit.co.ke", "Derrick Disburser", "disbursement_officer", {}),
        ("reconcile@mularcredit.co.ke", "Rachel Reconcile", "reconciliation_officer", {}),
        ("hqops@mularcredit.co.ke", "Harriet HQ", "hq_operations", {}),
    ]
    for email, name, role, scope in demo:
        _, is_new = _get_or_create_user(db, tid, email, name, role, **scope)
        if is_new:
            created.append(email)
    db.commit()

    # Approval thresholds ------------------------------------------------------
    def _upsert_threshold(scope_type, scope_key, threshold_type, amount):
        row = (db.query(ApprovalThreshold)
               .filter(ApprovalThreshold.tenant_id == tid,
                       ApprovalThreshold.scope_type == scope_type,
                       ApprovalThreshold.scope_key == str(scope_key),
                       ApprovalThreshold.threshold_type == threshold_type).first())
        if row:
            row.amount = amount
        else:
            db.add(ApprovalThreshold(tenant_id=tid, scope_type=scope_type,
                                     scope_key=str(scope_key), threshold_type=threshold_type,
                                     amount=amount))

    # Loan-approval limits per role (KES). relationship_officer cannot approve.
    _upsert_threshold("role", "relationship_officer", "loan_approval", 0)
    _upsert_threshold("role", "branch_manager", "loan_approval", 100000)
    _upsert_threshold("role", "regional_manager", "loan_approval", 500000)
    # Maker-checker money thresholds (tenant-wide)
    _upsert_threshold("role", "all", "disbursement", 200000)
    _upsert_threshold("role", "all", "refund", 50000)
    db.commit()

    # Portfolio scoping demo: give the RO a book inside Westlands ---------------
    assigned_clients = 0
    assigned_loans = 0
    if ro_staff and branch_id:
        clients = (db.query(Borrower)
                   .filter(Borrower.tenant_id == tid, Borrower.branch_id == branch_id)
                   .order_by(Borrower.id).all())
        for c in clients:
            c.officer_staff_id = ro_staff.id
            assigned_clients += 1
        # A few of them go into the Branch Manager approval inbox
        for c in clients[:5]:
            c.profile_status = "pending_approval"
        # Point that officer's loans at their staff id so loan portfolio scopes too
        client_ids = [c.id for c in clients]
        if client_ids:
            loans = (db.query(Loan)
                     .filter(Loan.tenant_id == tid, Loan.borrower_id.in_(client_ids)).all())
            for l in loans:
                l.staff_id = ro_staff.id
                l.branch_id = branch_id
                assigned_loans += 1
        db.commit()

    return {
        "created_users": created,
        "ro_staff_id": ro_staff.id if ro_staff else None,
        "branch_id": branch_id, "region_id": region_id,
        "assigned_clients": assigned_clients, "assigned_loans": assigned_loans,
        "credentials": _CREDS.flush("rbac_seed.py demo users"),
    }


def main():
    db = SessionLocal()
    try:
        result = seed_rbac(db)
        print("RBAC seed complete:")
        for k, v in result.items():
            print(f"  {k}: {v}")
        print("Passwords are never printed — see the gitignored "
              "storage/seed_credentials.txt file. All demo users must reset "
              "their password on first login.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
