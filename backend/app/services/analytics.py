"""
Analytics engine — plain Pandas over SQLAlchemy result sets.

Powers the executive dashboard KPIs, product×region matrix, staff net margin,
call-center collection efficiency, impact/investor metrics and the AI agent's
live analytics snapshot. No vendor SDKs — pure SQL + pandas.
"""
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

TODAY = None  # resolved per-call so long-lived processes stay correct


def _today() -> date:
    return date.today()


def _read_df(db: Session, sql: str, params: dict) -> pd.DataFrame:
    return pd.read_sql(text(sql), db.connection(), params=params)


def load_loans_df(db: Session, tenant_id: int) -> pd.DataFrame:
    return _read_df(db, """
        SELECT l.id, l.account_number, l.borrower_id, l.product_id, l.staff_id, l.branch_id,
               l.principal::float, l.interest_rate, l.status, l.application_date, l.approval_date,
               l.disbursement_date, l.due_date, l.outstanding_balance::float, l.loan_cycle_number,
               b.region_id, p.name AS product_name, s.name AS staff_name, br.name AS branch_name,
               r.name AS region_name
        FROM loans l
        JOIN borrowers b ON b.id = l.borrower_id
        JOIN products p ON p.id = l.product_id
        LEFT JOIN staff s ON s.id = l.staff_id
        LEFT JOIN branches br ON br.id = l.branch_id
        LEFT JOIN regions r ON r.id = b.region_id
        WHERE l.tenant_id = :t
    """, {"t": tenant_id})


def load_repayments_df(db: Session, tenant_id: int) -> pd.DataFrame:
    return _read_df(db, """
        SELECT rp.id, rp.loan_id, rp.amount::float, rp.principal_component::float,
               rp.interest_component::float, rp.payment_date, rp.method,
               l.staff_id, l.branch_id, l.product_id, b.region_id
        FROM repayments rp
        JOIN loans l ON l.id = rp.loan_id
        JOIN borrowers b ON b.id = l.borrower_id
        WHERE rp.tenant_id = :t
    """, {"t": tenant_id})


def apply_filters(loans: pd.DataFrame, f: dict) -> pd.DataFrame:
    """Apply the global filter bar (region/branch/product/staff/date range)."""
    df = loans
    if f.get("region_id"):
        df = df[df["region_id"] == int(f["region_id"])]
    if f.get("branch_id"):
        df = df[df["branch_id"] == int(f["branch_id"])]
    if f.get("product_id"):
        df = df[df["product_id"] == int(f["product_id"])]
    if f.get("staff_id"):
        df = df[df["staff_id"] == int(f["staff_id"])]
    if f.get("date_from"):
        df = df[pd.to_datetime(df["application_date"]) >= pd.to_datetime(f["date_from"])]
    if f.get("date_to"):
        df = df[pd.to_datetime(df["application_date"]) <= pd.to_datetime(f["date_to"])]
    return df


def _par(loans: pd.DataFrame, days: int) -> float:
    """Portfolio-at-risk: outstanding of loans overdue > `days` / total outstanding."""
    open_loans = loans[loans["status"].isin(["active", "overdue", "defaulted"])]
    total_out = open_loans["outstanding_balance"].sum()
    if total_out <= 0:
        return 0.0
    cutoff = _today() - timedelta(days=days)
    due = pd.to_datetime(open_loans["due_date"]).dt.date
    at_risk = open_loans[(due < cutoff) & (open_loans["outstanding_balance"] > 0)]
    return round(100.0 * at_risk["outstanding_balance"].sum() / total_out, 2)


def portfolio_kpis(loans: pd.DataFrame, repayments: pd.DataFrame) -> dict:
    disbursed = loans[loans["status"].isin(["active", "paid", "overdue", "defaulted"])]
    open_loans = loans[loans["status"].isin(["active", "overdue", "defaulted"])]
    total_disbursed = disbursed["principal"].sum()
    total_collected = repayments[repayments["loan_id"].isin(loans["id"])]["amount"].sum()
    total_expected = (disbursed["principal"] * (1 + disbursed["interest_rate"] / 100)).sum()
    interest_income = repayments[repayments["loan_id"].isin(loans["id"])]["interest_component"].sum()
    avg_outstanding = open_loans["outstanding_balance"].sum() or 1
    return {
        "par_1": _par(loans, 1),
        "par_30": _par(loans, 30),
        "par_90": _par(loans, 90),
        "disbursement_volume": round(float(total_disbursed), 2),
        "repayment_rate": round(100.0 * total_collected / total_expected, 2) if total_expected else 0,
        "yield_on_portfolio": round(100.0 * interest_income / avg_outstanding, 2),
        "active_loans": int((loans["status"] == "active").sum()),
        "overdue_loans": int((loans["status"] == "overdue").sum()),
        "total_outstanding": round(float(open_loans["outstanding_balance"].sum()), 2),
        "total_collected": round(float(total_collected), 2),
    }


# ------------------------- IFRS 9 ECL provisioning --------------------------------

# Default stage rates (fractions) — a per-tenant ecl_provision_config row overrides.
ECL_DEFAULTS = {"stage1_rate": 0.01, "stage2_rate": 0.20, "stage3_rate": 0.60}


def ecl_provisioning(loans: pd.DataFrame, config: dict | None = None) -> dict:
    """IFRS 9 three-stage Expected-Credit-Loss provisioning over the open book.

    Buckets each OPEN loan (active/overdue/defaulted with outstanding > 0) by
    days-past-due relative to its due_date:
        Stage 1: 0-30 dpd (and not-yet-due)        — default 1%
        Stage 2: 31-90 dpd                          — default 20%
        Stage 3: 90+ dpd OR status == 'defaulted'   — default 60%
    Provision = stage exposure x stage rate. Returns an ADDITIVE dict attached to
    the dashboard overview payload; it never alters existing KPIs.

    Money math for the provisions is done in Decimal (money()) so the figures are
    currency-exact; exposures/ratios are rounded floats consistent with the rest
    of the analytics payload.
    """
    from app.core.money import D, money

    cfg = {**ECL_DEFAULTS, **(config or {})}
    r1, r2, r3 = D(cfg["stage1_rate"]), D(cfg["stage2_rate"]), D(cfg["stage3_rate"])

    open_loans = loans[loans["status"].isin(["active", "overdue", "defaulted"])].copy()
    open_loans = open_loans[open_loans["outstanding_balance"] > 0]

    e1 = e2 = e3 = 0.0
    if not open_loans.empty:
        today = pd.Timestamp(_today())
        due = pd.to_datetime(open_loans["due_date"])
        dpd = (today - due).dt.days.fillna(0)          # NaT (no due date) -> 0 dpd
        is_default = open_loans["status"] == "defaulted"
        stage3 = is_default | (dpd > 90)
        stage2 = (~stage3) & (dpd > 30)
        stage1 = ~(stage3 | stage2)
        e1 = float(open_loans.loc[stage1, "outstanding_balance"].sum())
        e2 = float(open_loans.loc[stage2, "outstanding_balance"].sum())
        e3 = float(open_loans.loc[stage3, "outstanding_balance"].sum())

    p1 = money(D(e1) * r1)
    p2 = money(D(e2) * r2)
    p3 = money(D(e3) * r3)
    total_prov = money(p1 + p2 + p3)
    total_exp = D(e1) + D(e2) + D(e3)
    coverage = round(float(total_prov / total_exp * 100), 2) if total_exp > 0 else 0.0

    return {
        "stage1_exposure": round(e1, 2),
        "stage2_exposure": round(e2, 2),
        "stage3_exposure": round(e3, 2),
        "stage1_provision": float(p1),
        "stage2_provision": float(p2),
        "stage3_provision": float(p3),
        "total_ecl_provision": float(total_prov),
        "coverage_ratio": coverage,
        "rates": {"stage1_rate": float(r1), "stage2_rate": float(r2),
                  "stage3_rate": float(r3)},
    }


def monthly_trend(loans: pd.DataFrame, repayments: pd.DataFrame, months: int = 12) -> list[dict]:
    """Disbursement vs collections per month for the trend chart."""
    out = []
    today = _today()
    for i in range(months - 1, -1, -1):
        m_start = (pd.Timestamp(today) - pd.DateOffset(months=i)).replace(day=1)
        m_end = m_start + pd.DateOffset(months=1)
        dd = pd.to_datetime(loans["disbursement_date"])
        disb = loans[(dd >= m_start) & (dd < m_end)]["principal"].sum()
        pdt = pd.to_datetime(repayments["payment_date"])
        coll = repayments[(pdt >= m_start) & (pdt < m_end)]["amount"].sum()
        out.append({"month": m_start.strftime("%b %y"),
                    "disbursed": round(float(disb), 2), "collected": round(float(coll), 2)})
    return out


def status_mix(loans: pd.DataFrame) -> list[dict]:
    counts = loans["status"].value_counts()
    return [{"status": k, "count": int(v)} for k, v in counts.items()]


def product_region_matrix(loans: pd.DataFrame) -> dict:
    """Heatmap: product success rate (paid + active on-time vs overdue/defaulted) per region."""
    df = loans[loans["status"].isin(["paid", "active", "overdue", "defaulted"])].copy()
    if df.empty:
        return {"regions": [], "products": [], "cells": []}
    df["good"] = df["status"].isin(["paid", "active"])
    grp = df.groupby(["region_name", "product_name"]).agg(total=("id", "count"), good=("good", "sum"))
    cells = []
    for (region, product), row in grp.iterrows():
        cells.append({
            "region": region or "Unassigned", "product": product,
            "total": int(row["total"]),
            "success_rate": round(100.0 * row["good"] / row["total"], 1),
        })
    return {
        "regions": sorted(df["region_name"].dropna().unique().tolist()),
        "products": sorted(df["product_name"].unique().tolist()),
        "cells": cells,
    }


def staff_performance(db: Session, tenant_id: int, loans: pd.DataFrame, repayments: pd.DataFrame) -> list[dict]:
    """Staff Net Margin = (interest + fees recovered on officer's loans)
       − (salary + petty_cash + principal defaulted under the officer)."""
    staff_df = _read_df(db, """
        SELECT st.id, st.name, st.role, st.salary::float, st.petty_cash::float, b.name AS branch_name
        FROM staff st LEFT JOIN branches b ON b.id = st.branch_id
        WHERE st.tenant_id = :t AND st.active
    """, {"t": tenant_id})
    rows = []
    for _, st in staff_df.iterrows():
        officer_loans = loans[loans["staff_id"] == st["id"]]
        officer_reps = repayments[repayments["staff_id"] == st["id"]]
        interest_recovered = officer_reps["interest_component"].sum()
        defaulted_principal = officer_loans[officer_loans["status"] == "defaulted"]["outstanding_balance"].sum()
        cost = st["salary"] + st["petty_cash"]
        net_margin = float(interest_recovered) - float(cost) - float(defaulted_principal)
        rows.append({
            "staff_id": int(st["id"]), "name": st["name"], "role": st["role"],
            "branch": st["branch_name"],
            "loans_managed": int(len(officer_loans)),
            "portfolio": round(float(officer_loans["principal"].sum()), 2),
            "interest_recovered": round(float(interest_recovered), 2),
            "defaulted_principal": round(float(defaulted_principal), 2),
            "cost": round(float(cost), 2),
            "net_margin": round(net_margin, 2),
        })
    rows.sort(key=lambda r: r["net_margin"], reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


# ------------------------- Call center -----------------------------------------

def agent_scorecard(db: Session, tenant_id: int) -> list[dict]:
    """Per-agent call stats + Collection Efficiency: % of promises-to-pay where a
    repayment of >=70% of the promised amount landed within ±3 days of the promise date."""
    calls = _read_df(db, """
        SELECT c.id, c.agent_id, s.name AS agent_name, c.borrower_id, c.loan_id,
               c.duration_seconds, c.call_outcome, c.promise_to_pay_date, c.promise_amount::float
        FROM call_logs c JOIN staff s ON s.id = c.agent_id
        WHERE c.tenant_id = :t
    """, {"t": tenant_id})
    reps = _read_df(db, """
        SELECT loan_id, amount::float, payment_date FROM repayments WHERE tenant_id = :t
    """, {"t": tenant_id})
    out = []
    for agent_id, grp in calls.groupby("agent_id"):
        promises = grp[grp["call_outcome"] == "promise_to_pay"].dropna(subset=["promise_to_pay_date"])
        kept = 0
        for _, p in promises.iterrows():
            window_lo = pd.Timestamp(p["promise_to_pay_date"]) - pd.Timedelta(days=3)
            window_hi = pd.Timestamp(p["promise_to_pay_date"]) + pd.Timedelta(days=3)
            candidates = reps[(reps["loan_id"] == p["loan_id"]) &
                              (pd.to_datetime(reps["payment_date"]) >= window_lo) &
                              (pd.to_datetime(reps["payment_date"]) <= window_hi)]
            target = (p["promise_amount"] or 0) * 0.7
            if not candidates.empty and candidates["amount"].sum() >= target:
                kept += 1
        n_prom = len(promises)
        out.append({
            "agent_id": int(agent_id),
            "agent_name": grp["agent_name"].iloc[0],
            "total_calls": int(len(grp)),
            "avg_talk_time_sec": round(float(grp["duration_seconds"].mean() or 0), 1),
            "promises": n_prom,
            "promises_kept": kept,
            "collection_efficiency": round(100.0 * kept / n_prom, 1) if n_prom else 0.0,
        })
    out.sort(key=lambda r: r["collection_efficiency"], reverse=True)
    return out


# ------------------------- Complaints / SLA ---------------------------------------

def complaint_sla_stats(db: Session, tenant_id: int) -> dict:
    df = _read_df(db, """
        SELECT id, status, created_at, sla_deadline, resolved_at FROM complaints WHERE tenant_id = :t
    """, {"t": tenant_id})
    if df.empty:
        return {"total": 0, "open": 0, "resolved": 0, "breached": 0,
                "avg_resolution_days": 0, "within_sla_pct": 0}
    now = pd.Timestamp(datetime.utcnow())
    resolved = df[df["resolved_at"].notna()].copy()
    resolved["days"] = (pd.to_datetime(resolved["resolved_at"]) - pd.to_datetime(resolved["created_at"])).dt.total_seconds() / 86400
    resolved["within"] = pd.to_datetime(resolved["resolved_at"]) <= pd.to_datetime(resolved["sla_deadline"])
    open_df = df[df["resolved_at"].isna()]
    breached_open = (pd.to_datetime(open_df["sla_deadline"]) < now).sum()
    return {
        "total": int(len(df)),
        "open": int(len(open_df)),
        "resolved": int(len(resolved)),
        "breached": int(breached_open + (~resolved["within"]).sum() if len(resolved) else breached_open),
        "avg_resolution_days": round(float(resolved["days"].mean()), 1) if len(resolved) else 0,
        "within_sla_pct": round(100.0 * resolved["within"].mean(), 1) if len(resolved) else 0,
    }


# ------------------------- Impact / investor ---------------------------------------

AGE_GROUPS = [("18-25", 18, 25), ("26-35", 26, 35), ("36-50", 36, 50), ("50+", 51, 120)]


def impact_analytics(db: Session, tenant_id: int) -> dict:
    surveys = _read_df(db, """
        SELECT s.id, s.borrower_id, s.monthly_sales_pre::float, s.monthly_sales_post::float,
               s.jobs_created, s.sales_improved, s.survey_date, b.date_of_birth
        FROM impact_surveys s JOIN borrowers b ON b.id = s.borrower_id
        WHERE s.tenant_id = :t
    """, {"t": tenant_id})
    loans = _read_df(db, """
        SELECT id, borrower_id, principal::float, loan_cycle_number, status
        FROM loans WHERE tenant_id = :t
    """, {"t": tenant_id})
    if surveys.empty:
        return {"age_groups": [], "totals": {}, "summary": {}}
    today = _today()
    surveys["age"] = surveys["date_of_birth"].apply(
        lambda d: (today - d).days // 365 if pd.notna(d) else 0)
    groups = []
    for label, lo, hi in AGE_GROUPS:
        g = surveys[(surveys["age"] >= lo) & (surveys["age"] <= hi)]
        if g.empty:
            groups.append({"group": label, "surveys": 0, "revenue_growth_pct": 0, "jobs_created": 0})
            continue
        pre, post = g["monthly_sales_pre"].sum(), g["monthly_sales_post"].sum()
        groups.append({
            "group": label, "surveys": int(len(g)),
            "revenue_growth_pct": round(100.0 * (post - pre) / pre, 1) if pre else 0,
            "jobs_created": int(g["jobs_created"].sum()),
        })
    total_lent = loans[loans["status"].isin(["active", "paid", "overdue", "defaulted"])]["principal"].sum()
    uplift = (surveys["monthly_sales_post"] - surveys["monthly_sales_pre"]).sum()
    repeat_borrowers = loans[loans["loan_cycle_number"] >= 2]["borrower_id"].nunique()
    all_borrowers = loans["borrower_id"].nunique() or 1
    return {
        "age_groups": sorted(groups, key=lambda g: g["revenue_growth_pct"], reverse=True),
        "totals": {
            "total_jobs_created": int(surveys["jobs_created"].sum()),
            "avg_uplift_per_1000_kes": round(1000.0 * uplift / total_lent, 2) if total_lent else 0,
            "pct_sales_improved": round(100.0 * surveys["sales_improved"].mean(), 1),
            "repeat_borrower_retention_pct": round(100.0 * repeat_borrowers / all_borrowers, 1),
            "surveys_collected": int(len(surveys)),
        },
    }


# ------------------------- AI agent snapshot ---------------------------------------

def build_ai_snapshot(db: Session, tenant_id: int) -> dict:
    """Live analytics snapshot fed to the LLM as grounded context."""
    loans = load_loans_df(db, tenant_id)
    reps = load_repayments_df(db, tenant_id)
    kpis = portfolio_kpis(loans, reps)
    # PAR30 by region and by product
    par_region, par_product = [], []
    for region, grp in loans.groupby("region_name"):
        par_region.append({"region": region, "par_30": _par(grp, 30),
                           "outstanding": round(float(grp[grp['status'].isin(['active','overdue','defaulted'])]['outstanding_balance'].sum()), 0)})
    for product, grp in loans.groupby("product_name"):
        par_product.append({"product": product, "par_30": _par(grp, 30),
                            "loans": int(len(grp))})
    staff = staff_performance(db, tenant_id, loans, reps)
    aml = _read_df(db, "SELECT flag_type, severity, reviewed FROM aml_flags WHERE tenant_id=:t", {"t": tenant_id})
    return {
        "kpis": kpis,
        "par30_by_region": par_region,
        "par30_by_product": par_product,
        "staff_margins_top5": staff[:5],
        "staff_margins_bottom5": staff[-5:],
        "complaint_sla": complaint_sla_stats(db, tenant_id),
        "aml_flags": {"total": int(len(aml)), "unreviewed": int((~aml["reviewed"]).sum()) if len(aml) else 0,
                      "by_type": aml["flag_type"].value_counts().to_dict() if len(aml) else {}},
        "impact": impact_analytics(db, tenant_id).get("totals", {}),
    }
