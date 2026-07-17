"""
AML monitoring — structuring & velocity pattern detection over the repayment ledger.

Rules (CBK/POCAMLA-inspired simulation):
  * STRUCTURING: >=3 repayments on one loan within 48h, each just below the
    KES 10,000 reporting threshold (between 8,000 and 9,999).
  * RAPID SMALL REPAYMENTS: >=5 repayments under KES 2,000 within 24h on one loan.
  * VELOCITY: total repayments on one loan within 24h exceeding KES 100,000.
"""
from datetime import datetime

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import AmlFlag

REPORT_THRESHOLD = 10_000
STRUCT_LO = 8_000


def run_aml_scan(db: Session, tenant_id: int) -> list[AmlFlag]:
    """Scan repayments and insert new AML flags (idempotent per loan+type)."""
    df = pd.read_sql(text("""
        SELECT rp.loan_id, l.borrower_id, rp.amount::float, rp.payment_date
        FROM repayments rp JOIN loans l ON l.id = rp.loan_id
        WHERE rp.tenant_id = :t
        ORDER BY rp.loan_id, rp.payment_date
    """), db.connection(), params={"t": tenant_id})
    existing = {(f.loan_id, f.flag_type) for f in
                db.query(AmlFlag).filter(AmlFlag.tenant_id == tenant_id).all()}
    created: list[AmlFlag] = []

    def add_flag(loan_id, borrower_id, ftype, severity, details):
        if (loan_id, ftype) in existing:
            return
        flag = AmlFlag(tenant_id=tenant_id, loan_id=int(loan_id), borrower_id=int(borrower_id),
                       flag_type=ftype, severity=severity, details=details,
                       flagged_at=datetime.utcnow(), reviewed=False)
        db.add(flag)
        existing.add((loan_id, ftype))
        created.append(flag)

    for loan_id, grp in df.groupby("loan_id"):
        grp = grp.sort_values("payment_date")
        ts = pd.to_datetime(grp["payment_date"])
        borrower_id = grp["borrower_id"].iloc[0]

        # STRUCTURING — sliding 48h window of sub-threshold amounts
        sub = grp[(grp["amount"] >= STRUCT_LO) & (grp["amount"] < REPORT_THRESHOLD)]
        if len(sub) >= 3:
            sub_ts = pd.to_datetime(sub["payment_date"]).sort_values().reset_index(drop=True)
            for i in range(len(sub_ts) - 2):
                if (sub_ts[i + 2] - sub_ts[i]).total_seconds() <= 48 * 3600:
                    add_flag(loan_id, borrower_id, "structuring", "high",
                             f"{len(sub)} repayments of KES {STRUCT_LO:,}–{REPORT_THRESHOLD - 1:,} "
                             f"within 48h — possible structuring below the KES {REPORT_THRESHOLD:,} reporting threshold.")
                    break

        # RAPID SMALL REPAYMENTS — >=5 payments < 2,000 in 24h
        small = grp[grp["amount"] < 2_000]
        if len(small) >= 5:
            sm_ts = pd.to_datetime(small["payment_date"]).sort_values().reset_index(drop=True)
            for i in range(len(sm_ts) - 4):
                if (sm_ts[i + 4] - sm_ts[i]).total_seconds() <= 24 * 3600:
                    add_flag(loan_id, borrower_id, "rapid_small_repayments", "medium",
                             f"{len(small)} micro-repayments (< KES 2,000) within 24h — unusual repayment velocity.")
                    break

        # VELOCITY — >100k total within any 24h window
        if len(grp) >= 2:
            for i in range(len(grp)):
                window = grp[(ts >= ts.iloc[i]) & (ts <= ts.iloc[i] + pd.Timedelta(hours=24))]
                if window["amount"].sum() > 100_000:
                    add_flag(loan_id, borrower_id, "velocity", "high",
                             f"KES {window['amount'].sum():,.0f} repaid within 24h — exceeds velocity threshold KES 100,000.")
                    break

    db.commit()
    return created
