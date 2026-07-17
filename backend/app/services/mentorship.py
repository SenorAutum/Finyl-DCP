"""
Peer-to-Peer Mentorship Engine.

Selects veteran borrowers (repayment rate > 95%, 2+ completed cycles, strong
sales growth from impact surveys) and pairs them with early-stage borrowers in
the same business sector — preferring the same region — with a human-readable
match rationale.
"""
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session


def recommend_pairings(db: Session, tenant_id: int, limit: int = 20) -> list[dict]:
    borrowers = pd.read_sql(text("""
        SELECT b.id, b.first_name || ' ' || b.last_name AS name, b.business_sector,
               b.region_id, r.name AS region_name, b.phone
        FROM borrowers b LEFT JOIN regions r ON r.id = b.region_id
        WHERE b.tenant_id = :t
    """), db.connection(), params={"t": tenant_id})
    loans = pd.read_sql(text("""
        SELECT id, borrower_id, principal::float, interest_rate, status,
               outstanding_balance::float, loan_cycle_number
        FROM loans WHERE tenant_id = :t
    """), db.connection(), params={"t": tenant_id})
    surveys = pd.read_sql(text("""
        SELECT borrower_id, monthly_sales_pre::float, monthly_sales_post::float
        FROM impact_surveys WHERE tenant_id = :t
    """), db.connection(), params={"t": tenant_id})

    veterans, rookies = [], []
    for _, b in borrowers.iterrows():
        bl = loans[loans["borrower_id"] == b["id"]]
        if bl.empty:
            continue
        completed = int((bl["status"] == "paid").sum())
        expected = (bl[bl["status"].isin(["paid", "active", "overdue", "defaulted"])]["principal"]
                    * (1 + bl["interest_rate"] / 100)).sum()
        outstanding = bl[bl["status"].isin(["active", "overdue", "defaulted"])]["outstanding_balance"].sum()
        repay_rate = 100.0 * (expected - outstanding) / expected if expected else 0
        sv = surveys[surveys["borrower_id"] == b["id"]]
        growth = 0.0
        if not sv.empty and sv["monthly_sales_pre"].sum() > 0:
            growth = 100.0 * (sv["monthly_sales_post"].sum() - sv["monthly_sales_pre"].sum()) / sv["monthly_sales_pre"].sum()
        rec = {**b.to_dict(), "completed_cycles": completed, "repayment_rate": round(repay_rate, 1),
               "sales_growth_pct": round(growth, 1), "max_cycle": int(bl["loan_cycle_number"].max())}
        if completed >= 2 and repay_rate > 95 and growth > 10:
            veterans.append(rec)
        elif rec["max_cycle"] <= 1 and (bl["status"].isin(["active", "pending", "underwriting", "approved"])).any():
            rookies.append(rec)

    veterans.sort(key=lambda v: (v["sales_growth_pct"], v["repayment_rate"]), reverse=True)
    pairings, used_rookies = [], set()
    for vet in veterans:
        # same sector; prefer same region
        candidates = [r for r in rookies if r["id"] not in used_rookies and r["business_sector"] == vet["business_sector"]]
        candidates.sort(key=lambda r: (r["region_id"] == vet["region_id"], -r["id"]), reverse=True)
        for rook in candidates[:2]:  # a mentor can take up to 2 mentees
            used_rookies.add(rook["id"])
            same_region = rook["region_id"] == vet["region_id"]
            reasons = [
                f"Mentor has {vet['completed_cycles']} completed loan cycles with {vet['repayment_rate']}% repayment rate",
                f"Mentor grew monthly sales by {vet['sales_growth_pct']}%",
                f"Both operate in the {vet['business_sector']} sector",
            ]
            if same_region:
                reasons.append(f"Both are in {vet['region_name']} region — easy in-person meetups")
            pairings.append({
                "mentor": {k: vet[k] for k in ("id", "name", "business_sector", "region_name",
                                               "repayment_rate", "completed_cycles", "sales_growth_pct")},
                "mentee": {k: rook[k] for k in ("id", "name", "business_sector", "region_name", "max_cycle")},
                "match_score": round(min(99, 60 + (10 if same_region else 0)
                                         + vet["sales_growth_pct"] / 10 + vet["repayment_rate"] / 10), 0),
                "reasons": reasons,
            })
            if len(pairings) >= limit:
                return pairings
    return pairings
