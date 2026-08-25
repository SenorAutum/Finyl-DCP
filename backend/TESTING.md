# Finyl-DCP — Money-Path Test Suite

Automated pytest suite focused on the **money paths**: interest/fee/excise math,
the in-duplum cap, disbursement idempotency, repayment/reconciliation allocation,
and the maker-checker approvals state machine.

The suite validates the **current behaviour** of the code as written. Any genuine
money-logic defect is captured as an `xfail` test that asserts the *correct*
behaviour, with a reason (see [Known defects](#known-defects)).

## Isolation guarantee

The tests **never** touch the live PostgreSQL `finyl_dcp` database/schema:

* `conftest.py` builds its **own** SQLAlchemy engine from `TEST_DATABASE_URL`
  (default `sqlite://`, an in-memory throwaway DB) and **never** imports or uses
  `app.core.database.engine` or its Postgres `SET search_path` connection hooks.
* Each test creates the full schema on that engine and drops it afterwards, so no
  state leaks between tests and nothing persists.
* A hard guard in `conftest.py` **refuses to run** if the resolved test DB URL
  equals the live `DATABASE_URL` (or otherwise points at `finyl_dcp` on a real
  server), so a mis-set env var can never mutate production data.
* Business logic is exercised by calling the service/router functions **directly**
  with the test session (no HTTP, no JWT). Production source is untouched.
* All external I/O (Daraja/M-Pesa B2C, SMS) is stubbed — **no network** is hit.

## Requirements

Test-only dependencies live in `requirements-dev.txt` (production
`requirements.txt` is unchanged). Install them into the backend venv:

```bash
cd backend
venv/bin/pip install -r requirements-dev.txt
```

## Running

```bash
cd backend
venv/bin/python -m pytest -q          # or:  venv/bin/pytest -q
```

`conftest.py` sets the required env vars automatically before importing the app
(a strong test `JWT_SECRET`, a fresh `FIELD_ENCRYPTION_KEY`, `SCHEDULER_ENABLED=false`,
`EKYC_MOCK=true`). To run against a throwaway Postgres instead of in-memory SQLite:

```bash
TEST_DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/finyl_dcp_test" \
  venv/bin/python -m pytest -q
```

(The guard will reject any URL that resolves to the live database.)

## Test files

| File | Covers |
|------|--------|
| `tests/conftest.py` | Isolated engine + safety guard; `db`, `seed`, `mock_b2c` fixtures. |
| `tests/test_money.py` | Decimal interest/principal split, fee + excise pricing quote, `total_due`; rounding (ROUND_HALF_UP), no float drift; ≥2 loan scenarios. |
| `tests/test_in_duplum.py` | In-duplum cap: accrued interest never exceeds principal at/beyond the boundary; headroom logic. |
| `tests/test_disbursement_idempotency.py` | Same approved loan disbursed twice → atomic approved→processing guard yields exactly one payout / one b2c transaction; `apply_b2c_result` is a no-op on an already-settled txn. |
| `tests/test_reconciliation.py` | Repayment interest/principal allocation & balance reduction; duplicate M-Pesa receipt is ignored (no double-credit); unmatched payment → suspense (idempotent on ref); suspense allocation is terminal. |
| `tests/test_approvals.py` | Maker-checker state machine: SoD (maker≠checker), valid checker approval runs the side-effect once, reject is terminal, terminal actions can't be re-actioned; `requires_maker_checker` fails closed with no threshold and honours a configured one. |

## Known defects

None gating the money paths at the time of writing — the suite is green. If a
future defect is found, the corresponding test is kept asserting the **correct**
behaviour and marked `@pytest.mark.xfail(reason=...)` so it is visible without
masking the failure.
