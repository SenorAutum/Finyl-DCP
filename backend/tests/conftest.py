"""
Pytest fixtures for the Finyl-DCP money-path test suite.

ISOLATION GUARANTEE
-------------------
These tests NEVER touch the live PostgreSQL `finyl_dcp` database/schema. They run
against a throwaway, in-memory SQLite database created fresh for every test:

  * We build our OWN SQLAlchemy engine here (from TEST_DATABASE_URL, default
    ``sqlite://`` in-memory) and NEVER import/use ``app.core.database.engine`` or
    its Postgres-specific ``SET search_path`` connection hooks.
  * Business logic is exercised by calling the service/router functions DIRECTLY
    with a test session (no HTTP, no JWT), so production source is untouched.
  * A hard guard below refuses to run if the resolved test DB URL points at the
    live database, so a mis-set env var can never mutate real data.

The tests validate CURRENT behaviour of the code as written.
"""
import os

# ---------------------------------------------------------------------------
# 1. Environment MUST be set before importing any `app.*` module, because
#    app.core.config builds a module-level Settings() at import time that
#    refuses weak/short JWT secrets and validates the field-encryption key.
# ---------------------------------------------------------------------------
os.environ.setdefault("JWT_SECRET", "finyl-dcp-test-secret-key-0123456789abcdef")  # >=32 chars, non-weak
os.environ.setdefault("SCHEDULER_ENABLED", "false")  # never start the APScheduler worker in tests
os.environ.setdefault("EKYC_MOCK", "true")
# Keep external providers credential-gated (placeholders) so no network is ever hit.

# A valid Fernet key is required for the PII field-encryption / blind-index used
# by the Borrower model. Generate a fresh one per run (DB is ephemeral).
if not os.environ.get("FIELD_ENCRYPTION_KEY"):
    from cryptography.fernet import Fernet
    os.environ["FIELD_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

# The isolated test database. Default: in-memory SQLite. Overridable via env for
# a throwaway Postgres, but NEVER the live DB (guarded below).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

# Import config first to know what the LIVE url is, so we can refuse to touch it.
from app.core.config import settings  # noqa: E402

# ---------------------------------------------------------------------------
# 2. Safety guard — refuse to run against the live database.
# ---------------------------------------------------------------------------
_live_url = (settings.DATABASE_URL or "").strip()
if TEST_DATABASE_URL.strip() == _live_url or (
    "finyl_dcp" in TEST_DATABASE_URL and not TEST_DATABASE_URL.startswith("sqlite")
):
    raise RuntimeError(
        "Refusing to run the test suite against a non-isolated database. "
        "TEST_DATABASE_URL must be a throwaway DB (default sqlite://), never the "
        f"live finyl_dcp database. Got: {TEST_DATABASE_URL!r}"
    )

# ---------------------------------------------------------------------------
# 3. Build the isolated engine (our own — NOT app.core.database.engine).
# ---------------------------------------------------------------------------
_connect_args = {}
_engine_kwargs = {}
if TEST_DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}
    _engine_kwargs = {"poolclass": StaticPool}

engine = create_engine(TEST_DATABASE_URL, connect_args=_connect_args, **_engine_kwargs)


# SQLite does not enforce foreign keys by default; enabling it keeps the test DB
# behaviour close to Postgres for referential integrity.
if TEST_DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_con, _):  # noqa: ARG001
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Import the model package so every table registers on Base.metadata before
# create_all. (Base is defined in app.core.database; we only borrow the metadata,
# never the live engine.)
import app.models  # noqa: E402,F401
from app.core.database import Base  # noqa: E402


@pytest.fixture()
def db():
    """A fresh, fully-isolated DB session per test.

    Tables are created before the test and dropped after, so no state leaks
    between tests. Router/service code under test calls ``.commit()`` on this
    same session; that is fine — the schema is torn down afterwards.
    """
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# 4. Seed helpers — minimal graph of tenant/org/product/borrower/loan/users.
# ---------------------------------------------------------------------------
from types import SimpleNamespace  # noqa: E402


@pytest.fixture()
def seed(db):
    """Create a minimal but complete object graph and return a namespace with
    the created rows plus small factory callables for per-test variations."""
    from app.models import (Tenant, TenantModule, User, Region, Branch, Staff,
                            Product, Borrower, Loan)

    tenant = Tenant(name="Test DCP", code="TDCP")
    db.add(tenant)
    db.flush()

    for mod in ("lending", "payments"):
        db.add(TenantModule(tenant_id=tenant.id, module_key=mod, enabled=True))

    region = Region(tenant_id=tenant.id, name="Nairobi")
    db.add(region)
    db.flush()
    branch = Branch(tenant_id=tenant.id, region_id=region.id, name="CBD")
    db.add(branch)
    db.flush()
    staff = Staff(tenant_id=tenant.id, branch_id=branch.id, name="Officer One",
                  role="loan_officer")
    db.add(staff)
    db.flush()

    # Product with fee rules used by the pricing-quote tests.
    product = Product(
        tenant_id=tenant.id, name="Biashara 4wk", code="BIZ4",
        interest_rate=10.0, interest_method="flat",
        tenure_value=4, tenure_unit="weeks",
        min_amount=1000, max_amount=100000,
        rules={"processing_fee_rate": 2.5, "facility_fee": 150},
    )
    db.add(product)
    db.flush()

    borrower = Borrower(
        tenant_id=tenant.id, first_name="Jane", last_name="Wanjiku",
        national_id="12345678", phone="0712345678",
        branch_id=branch.id, region_id=region.id, officer_staff_id=staff.id,
    )
    db.add(borrower)
    db.flush()

    # --- users -------------------------------------------------------------
    def make_user(role, email, **kw):
        u = User(email=email, hashed_password="x", full_name=email.split("@")[0],
                 role=role, tenant_id=tenant.id, **kw)
        db.add(u)
        db.flush()
        return u

    maker = make_user("disbursement_officer", "maker@test.dcp")
    checker = make_user("disbursement_officer", "checker@test.dcp")
    recon = make_user("reconciliation_officer", "recon@test.dcp")

    _loan_seq = {"n": 0}

    def make_loan(status="approved", principal="10000.00", interest_rate="10.0",
                  outstanding_balance=None, **kw):
        _loan_seq["n"] += 1
        loan = Loan(
            tenant_id=tenant.id,
            account_number=f"FL/T/{_loan_seq['n']:04d}",
            borrower_id=borrower.id, product_id=product.id,
            staff_id=staff.id, branch_id=branch.id,
            principal=principal, interest_rate=interest_rate,
            status=status,
            outstanding_balance=(outstanding_balance
                                 if outstanding_balance is not None else 0),
            **kw,
        )
        db.add(loan)
        db.flush()
        return loan

    db.commit()

    return SimpleNamespace(
        db=db, tenant=tenant, region=region, branch=branch, staff=staff,
        product=product, borrower=borrower,
        maker=maker, checker=checker, recon=recon,
        make_user=make_user, make_loan=make_loan,
    )


@pytest.fixture()
def mock_b2c(monkeypatch):
    """Deterministic, network-free stub for the Daraja B2C payout that also
    records every call so idempotency/lock tests can assert the payout count."""
    calls = []

    def _fake_b2c_disburse(phone, amount, remarks, creds=None):
        calls.append({"phone": phone, "amount": amount, "remarks": remarks})
        conv = f"AG_TEST_{len(calls)}"
        return {
            "request": {"Amount": int(amount)},
            "response": {"ConversationID": conv, "ResponseCode": "0"},
            "result": {
                "ResultCode": None,  # async ack, not settled yet (matches sandbox mock)
                "ConversationID": conv,
                "OriginatorConversationID": f"ORIG_{len(calls)}",
                "TransactionReceipt": conv,
            },
        }

    import app.services.mpesa as mpesa
    monkeypatch.setattr(mpesa, "b2c_disburse", _fake_b2c_disburse)
    # execute_disbursement imports the module and calls mpesa.b2c_disburse, so the
    # module-level patch above is picked up.
    return SimpleNamespace(calls=calls)
