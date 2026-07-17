"""
CBK regulatory export generators — SIMULATED templates built from live ledger data.

These produce plausible layouts for the Asset Quality return, Capital Adequacy
return and a daily CRB submission file. They are clearly labeled simulations —
swap the column layouts with the official CBK/CRB specs before real filing.
"""
import csv
import io
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session


def _loans_df(db: Session, tenant_id: int) -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT l.account_number, l.principal::float, l.interest_rate, l.status,
               l.disbursement_date, l.due_date, l.outstanding_balance::float,
               b.first_name, b.last_name, b.national_id, b.phone, b.date_of_birth, b.gender,
               p.name AS product_name
        FROM loans l JOIN borrowers b ON b.id = l.borrower_id
        JOIN products p ON p.id = l.product_id
        WHERE l.tenant_id = :t
    """), db.connection(), params={"t": tenant_id})


def asset_quality_csv(db: Session, tenant_id: int) -> str:
    """CBK Asset Quality return (simulation): loan book classified by risk bucket."""
    df = _loans_df(db, tenant_id)
    today = date.today()

    def classify(row):
        if row["status"] == "paid":
            return "Normal (Closed)"
        if row["status"] == "defaulted":
            return "Loss"
        if row["status"] in ("active", "overdue") and pd.notna(row["due_date"]):
            overdue_days = (today - row["due_date"]).days
            if overdue_days <= 0:
                return "Normal"
            if overdue_days <= 30:
                return "Watch"
            if overdue_days <= 90:
                return "Substandard"
            if overdue_days <= 180:
                return "Doubtful"
            return "Loss"
        return "Normal"

    open_df = df[df["status"].isin(["active", "overdue", "defaulted", "paid"])].copy()
    open_df["risk_class"] = open_df.apply(classify, axis=1)
    buckets = open_df.groupby("risk_class").agg(
        loans=("account_number", "count"),
        gross_amount=("principal", "sum"),
        outstanding=("outstanding_balance", "sum"),
    ).reset_index()
    provisions = {"Normal": 0.01, "Normal (Closed)": 0.0, "Watch": 0.03,
                  "Substandard": 0.20, "Doubtful": 0.50, "Loss": 1.00}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["# CBK ASSET QUALITY RETURN — SIMULATED TEMPLATE (Finyl-DCP)"])
    w.writerow(["# Reporting date", today.isoformat()])
    w.writerow(["Risk Classification", "No. of Loans", "Gross Amount (KES)",
                "Outstanding (KES)", "Provision Rate", "Required Provision (KES)"])
    for _, r in buckets.iterrows():
        rate = provisions.get(r["risk_class"], 0.01)
        w.writerow([r["risk_class"], int(r["loans"]), f"{r['gross_amount']:.2f}",
                    f"{r['outstanding']:.2f}", f"{rate:.0%}", f"{r['outstanding'] * rate:.2f}"])
    return out.getvalue()


def capital_adequacy_csv(db: Session, tenant_id: int) -> str:
    """CBK Capital Adequacy return (simulation) using book-derived proxies."""
    df = _loans_df(db, tenant_id)
    open_df = df[df["status"].isin(["active", "overdue", "defaulted"])]
    gross_loans = open_df["outstanding_balance"].sum()
    npl = open_df[open_df["status"].isin(["overdue", "defaulted"])]["outstanding_balance"].sum()
    core_capital = gross_loans * 0.25          # simulated paid-up capital proxy
    risk_weighted = gross_loans * 1.0 + npl * 0.5
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["# CBK CAPITAL ADEQUACY RETURN — SIMULATED TEMPLATE (Finyl-DCP)"])
    w.writerow(["# Reporting date", date.today().isoformat()])
    w.writerow(["Line Item", "Amount (KES)"])
    w.writerow(["Core Capital (Tier 1) [proxy]", f"{core_capital:.2f}"])
    w.writerow(["Supplementary Capital (Tier 2) [proxy]", f"{core_capital * 0.2:.2f}"])
    w.writerow(["Total Capital", f"{core_capital * 1.2:.2f}"])
    w.writerow(["Gross Loan Portfolio", f"{gross_loans:.2f}"])
    w.writerow(["Non-Performing Loans", f"{npl:.2f}"])
    w.writerow(["Total Risk-Weighted Assets", f"{risk_weighted:.2f}"])
    ratio = (core_capital * 1.2 / risk_weighted * 100) if risk_weighted else 0
    w.writerow(["Capital Adequacy Ratio (%)", f"{ratio:.2f}"])
    w.writerow(["Minimum Statutory Ratio (%)", "14.50"])
    w.writerow(["Compliance Status", "COMPLIANT" if ratio >= 14.5 else "NON-COMPLIANT"])
    return out.getvalue()


def crb_daily_txt(db: Session, tenant_id: int) -> str:
    """Daily CRB submission file (simulation) — pipe-delimited in a plausible
    Metropol/TransUnion-style layout."""
    df = _loans_df(db, tenant_id)
    active = df[df["status"].isin(["active", "overdue", "defaulted", "paid"])]
    today = date.today()
    lines = [
        "H|FINYLDCP|" + today.strftime("%Y%m%d") + "|CRB-DAILY|SIMULATED-TEMPLATE",
    ]
    status_map = {"active": "A", "paid": "F", "overdue": "D", "defaulted": "W"}
    for _, r in active.iterrows():
        overdue_days = max(0, (today - r["due_date"]).days) if pd.notna(r["due_date"]) and r["status"] in ("overdue", "defaulted") else 0
        lines.append("|".join([
            "D",
            r["account_number"],
            r["national_id"] or "",
            (r["first_name"] or "").upper(),
            (r["last_name"] or "").upper(),
            (r["gender"] or "")[:1].upper(),
            r["date_of_birth"].strftime("%Y%m%d") if pd.notna(r["date_of_birth"]) else "",
            r["phone"] or "",
            f"{r['principal']:.2f}",
            f"{r['outstanding_balance']:.2f}",
            str(overdue_days),
            status_map.get(r["status"], "A"),
            r["disbursement_date"].strftime("%Y%m%d") if pd.notna(r["disbursement_date"]) else "",
            r["due_date"].strftime("%Y%m%d") if pd.notna(r["due_date"]) else "",
        ]))
    lines.append(f"T|{len(active)}|" + today.strftime("%Y%m%d"))
    return "\n".join(lines)
