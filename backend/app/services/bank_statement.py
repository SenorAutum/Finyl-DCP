"""Bank-statement ingestion + affordability analysis (Phase 2).

Accepts already-parsed transaction rows (the ingestion endpoint / uploader is
responsible for turning a PDF/CSV into rows). Computes cashflow + affordability
metrics and runs lightweight tampering heuristics. Transaction shape:
    {"date": "YYYY-MM-DD", "description": str, "amount": float, "type": "credit"|"debit"}
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.crypto import pii_hash
from app.models import BankStatement

# Common Kenyan digital-lender footprints for detected_lenders.
_LENDER_HINTS = ["tala", "branch", "zenka", "okash", "mshwari", "kcb-mpesa",
                 "fuliza", "timiza", "izwe", "stawi", "loop", "hustler"]


def _to_dec(v) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def analyse(transactions: list[dict]) -> dict:
    credits = [_to_dec(t.get("amount")) for t in transactions if t.get("type") == "credit"]
    debits = [_to_dec(t.get("amount")) for t in transactions if t.get("type") == "debit"]
    dates = sorted([t.get("date") for t in transactions if t.get("date")])
    months = 1
    if len(dates) >= 2:
        try:
            d0 = datetime.fromisoformat(dates[0]).date()
            d1 = datetime.fromisoformat(dates[-1]).date()
            months = max(1, (d1.year - d0.year) * 12 + (d1.month - d0.month) + 1)
        except Exception:
            months = 1
    total_credit = sum(credits, Decimal("0"))
    total_debit = sum(debits, Decimal("0"))
    avg_credit = (total_credit / months).quantize(Decimal("0.01"))
    avg_debit = (total_debit / months).quantize(Decimal("0.01"))
    net = (avg_credit - avg_debit).quantize(Decimal("0.01"))
    # Affordability: comfortable installment ≈ 33% of net monthly cashflow.
    comfortable = (net * Decimal("0.33")).quantize(Decimal("0.01")) if net > 0 else Decimal("0")
    score = Decimal("0")
    if avg_credit > 0:
        score = (net / avg_credit * 100).quantize(Decimal("0.01"))
        score = max(Decimal("0"), min(Decimal("100"), score))

    # Detected lenders.
    detected = sorted({h for t in transactions
                       for h in _LENDER_HINTS
                       if h in (t.get("description", "") or "").lower()})

    # Tampering heuristics.
    flags = []
    if any(_to_dec(t.get("amount")) < 0 for t in transactions):
        flags.append("negative_amount_present")
    running = None
    for t in transactions:
        bal = t.get("balance")
        if bal is not None:
            b = _to_dec(bal)
            if running is not None and t.get("type") == "credit" and b < running:
                flags.append("balance_inconsistency")
                break
            running = b
    if dates != sorted(dates):
        flags.append("out_of_order_dates")

    return {
        "months_covered": months, "transactions_count": len(transactions),
        "avg_monthly_credit": avg_credit, "avg_monthly_debit": avg_debit,
        "net_monthly_cashflow": net, "affordability_score": score,
        "comfortable_installment": comfortable,
        "detected_lenders": detected,
        "tampering_suspected": bool(flags), "integrity_flags": flags,
        "period_start": dates[0] if dates else None,
        "period_end": dates[-1] if dates else None,
    }


def ingest(db: Session, *, tenant_id: int, client_id: int, bank_name: str,
           transactions: list[dict], account_number: str | None = None,
           source_filename: str | None = None, created_by: int | None = None) -> BankStatement:
    a = analyse(transactions or [])
    row = BankStatement(
        tenant_id=tenant_id, client_id=client_id, bank_name=bank_name,
        account_number_hash=pii_hash(account_number) if account_number else None,
        period_start=a["period_start"], period_end=a["period_end"],
        months_covered=a["months_covered"], transactions_count=a["transactions_count"],
        avg_monthly_credit=a["avg_monthly_credit"], avg_monthly_debit=a["avg_monthly_debit"],
        net_monthly_cashflow=a["net_monthly_cashflow"],
        affordability_score=a["affordability_score"],
        comfortable_installment=a["comfortable_installment"],
        summary={"analysed_at": datetime.utcnow().isoformat()},
        detected_lenders=a["detected_lenders"],
        tampering_suspected=a["tampering_suspected"], integrity_flags=a["integrity_flags"],
        source_filename=source_filename, created_by=created_by,
    )
    db.add(row)
    db.commit()
    return row
