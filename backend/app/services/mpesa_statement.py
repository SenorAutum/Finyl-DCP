"""
M-Pesa statement analysis — creditworthiness from an official Safaricom
M-Pesa statement PDF.

No Safaricom API is involved: the customer downloads their statement from the
M-Pesa app / *334# (delivered as a PASSWORD-PROTECTED PDF, password typically the
customer's National ID number). We decrypt it (pikepdf), extract the transaction
table (pdfplumber), parse each row, then derive:

  * total / average monthly inflows & outflows, net cash flow
  * income regularity, average end-of-day balance
  * loan-repayment-like outflows
  * **borrowing from OTHER lenders** (Fuliza, M-Shwari, KCB M-Pesa, Tala, Branch,
    Zenka, Timiza, Stawi, Okash, and generic loan/"Business Payment" patterns)
  * an affordability score (0-100) + a recommended "comfortable installment"
  * an integrity check (balance continuity, duplicate rows, date gaps → tamper flag)

The whole thing sits behind this single module so a different statement source
(bank statement, aggregator API) can be swapped in without touching the routers.
"""
from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import datetime

# --------------------------------------------------------------------------- #
# Known digital lenders / credit products to scan narratives for.
# category: overdraft | mobile_loan | other
# --------------------------------------------------------------------------- #
KNOWN_LENDERS = [
    {"name": "Fuliza", "category": "overdraft", "patterns": [r"fuliza"]},
    {"name": "M-Shwari", "category": "mobile_loan", "patterns": [r"m-?shwari"]},
    {"name": "KCB M-Pesa", "category": "mobile_loan", "patterns": [r"kcb\s*m-?pesa", r"kcb mpesa"]},
    {"name": "Tala", "category": "mobile_loan", "patterns": [r"\btala\b", r"mkopo rahisi"]},
    {"name": "Branch", "category": "mobile_loan", "patterns": [r"branch(?:\s*int| international| co)"]},
    {"name": "Zenka", "category": "mobile_loan", "patterns": [r"zenka"]},
    {"name": "Timiza", "category": "mobile_loan", "patterns": [r"timiza"]},
    {"name": "Stawi", "category": "mobile_loan", "patterns": [r"stawi"]},
    {"name": "Okash", "category": "mobile_loan", "patterns": [r"okash", r"opesa"]},
    {"name": "Zash / MCo-op", "category": "mobile_loan", "patterns": [r"co-?op\s*cash", r"mco-?op"]},
    {"name": "Hustler Fund", "category": "mobile_loan", "patterns": [r"hustler"]},
]

# Generic loan-like narrative fallbacks (only counted as inflow "borrowing").
_GENERIC_LOAN_IN = re.compile(r"\b(loan|credit|advance|disburse|mkopo)\b", re.I)
_LOAN_REPAY_OUT = re.compile(r"\b(loan|repay|repayment|installment|instalment|mkopo)\b", re.I)


class StatementError(RuntimeError):
    """Raised for unreadable/undecryptable statements — surfaced as a 4xx."""


# --------------------------------------------------------------------------- #
# PDF decrypt + table extraction
# --------------------------------------------------------------------------- #
def decrypt_pdf(data: bytes, password: str | None) -> bytes:
    """Return decrypted PDF bytes. Tries the supplied password (typically the
    client's National ID). Raises StatementError with an actionable message."""
    # Fast path: not encrypted.
    try:
        import pikepdf
    except Exception as exc:  # pragma: no cover
        raise StatementError(f"PDF library not installed ({exc}).")

    try:
        pdf = pikepdf.open(io.BytesIO(data))
        buf = io.BytesIO(); pdf.save(buf); return buf.getvalue()
    except pikepdf.PasswordError:
        pass
    except Exception as exc:
        raise StatementError(f"Could not open the PDF ({exc}).")

    if not password:
        raise StatementError("This statement is password-protected. Provide the "
                             "password (usually the client's National ID number).")
    try:
        pdf = pikepdf.open(io.BytesIO(data), password=str(password))
        buf = io.BytesIO(); pdf.save(buf); return buf.getvalue()
    except pikepdf.PasswordError:
        raise StatementError("Incorrect statement password. The M-Pesa statement "
                             "password is usually the client's National ID number.")
    except Exception as exc:
        raise StatementError(f"Could not decrypt the PDF ({exc}).")


_AMOUNT_RE = re.compile(r"-?\d[\d,]*\.\d{2}")
_DATE_RE = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?")


def _to_float(s) -> float:
    if s is None:
        return 0.0
    t = str(s).replace(",", "").replace(" ", "").strip()
    if t in ("", "-"):
        return 0.0
    try:
        return float(t)
    except ValueError:
        return 0.0


def _parse_date(s):
    if not s:
        return None
    m = _DATE_RE.search(str(s))
    if not m:
        return None
    y, mth, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh = int(m.group(4)) if m.group(4) else 0
    mm = int(m.group(5)) if m.group(5) else 0
    try:
        return datetime(y, mth, d, hh, mm)
    except ValueError:
        return None


def extract_transactions(pdf_bytes: bytes) -> list[dict]:
    """Parse the Safaricom 'DETAILED STATEMENT' transaction table.

    Columns on the official statement:
      Receipt No. | Completion Time | Details | Transaction Status |
      Paid In | Withdrawn | Balance
    Returns a list of row dicts. Robust to the two common layouts (table cells
    and free-text lines)."""
    try:
        import pdfplumber
    except Exception as exc:  # pragma: no cover
        raise StatementError(f"pdfplumber not installed ({exc}).")

    rows: list[dict] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for table in tables:
                for raw in table:
                    row = _row_from_cells(raw)
                    if row:
                        rows.append(row)
            if not tables:
                text = page.extract_text() or ""
                for line in text.splitlines():
                    row = _row_from_text(line)
                    if row:
                        rows.append(row)

    # De-duplicate exact repeats coming from overlapping table/text parsing.
    seen, unique = set(), []
    for r in rows:
        key = (r["receipt"], r["date"].isoformat() if r["date"] else "", r["paid_in"], r["withdrawn"], r["balance"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    unique.sort(key=lambda r: r["date"] or datetime.min)
    return unique


_RECEIPT_RE = re.compile(r"^[A-Z0-9]{10}$")


def _row_from_cells(cells) -> dict | None:
    if not cells:
        return None
    cells = [(c or "").replace("\n", " ").strip() for c in cells]
    joined = " ".join(cells)
    dt = _parse_date(joined)
    if not dt:
        return None
    receipt = ""
    if cells and _RECEIPT_RE.match(cells[0].replace(" ", "")):
        receipt = cells[0].replace(" ", "")
    amounts = _AMOUNT_RE.findall(joined)
    if len(amounts) < 1:
        return None
    # Last amount = running balance; the two before (if present) = paid_in/withdrawn.
    balance = _to_float(amounts[-1])
    paid_in = withdrawn = 0.0
    if len(amounts) >= 3:
        paid_in = _to_float(amounts[-3]); withdrawn = abs(_to_float(amounts[-2]))
    elif len(amounts) == 2:
        val = _to_float(amounts[-2])
        if val >= 0:
            paid_in = val
        else:
            withdrawn = abs(val)
    details = _details_from_cells(cells)
    return {"receipt": receipt, "date": dt, "details": details,
            "paid_in": paid_in, "withdrawn": withdrawn, "balance": balance}


def _row_from_text(line: str) -> dict | None:
    line = (line or "").strip()
    if not line:
        return None
    dt = _parse_date(line)
    if not dt:
        return None
    amounts = _AMOUNT_RE.findall(line)
    if len(amounts) < 2:
        return None
    receipt = ""
    first = line.split()[0]
    if _RECEIPT_RE.match(first):
        receipt = first
    balance = _to_float(amounts[-1])
    paid_in = withdrawn = 0.0
    if len(amounts) >= 3:
        paid_in = _to_float(amounts[-3]); withdrawn = abs(_to_float(amounts[-2]))
    else:
        val = _to_float(amounts[-2])
        (paid_in, withdrawn) = (val, 0.0) if val >= 0 else (0.0, abs(val))
    # Details = text between the completion time and the first amount.
    details = line
    m = _AMOUNT_RE.search(line)
    if m:
        details = line[:m.start()]
    details = re.sub(r"^[A-Z0-9]{10}\s*", "", details)
    details = _DATE_RE.sub("", details).strip(" -\t")
    return {"receipt": receipt, "date": dt, "details": details,
            "paid_in": paid_in, "withdrawn": withdrawn, "balance": balance}


def _details_from_cells(cells) -> str:
    """Pick the wordiest non-amount, non-date cell as the narrative."""
    best = ""
    for c in cells:
        if _AMOUNT_RE.fullmatch(c.replace(",", "")) or _DATE_RE.search(c):
            continue
        if _RECEIPT_RE.match(c.replace(" ", "")):
            continue
        if len(c) > len(best):
            best = c
    return best


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def _month_key(dt: datetime) -> str:
    return f"{dt.year}-{dt.month:02d}"


def detect_lenders(rows: list[dict]) -> list[dict]:
    """Scan narratives for known digital lenders + generic loan patterns."""
    agg: dict[str, dict] = {}
    for r in rows:
        text = (r["details"] or "").lower()
        matched = None
        for lender in KNOWN_LENDERS:
            if any(re.search(p, text) for p in lender["patterns"]):
                matched = lender
                break
        if not matched:
            # Generic loan disbursement inflow or repayment outflow.
            if r["paid_in"] > 0 and _GENERIC_LOAN_IN.search(text):
                matched = {"name": "Other lender (generic)", "category": "other"}
            elif r["withdrawn"] > 0 and _LOAN_REPAY_OUT.search(text) and "fuliza" not in text:
                matched = {"name": "Other lender (generic)", "category": "other"}
        if not matched:
            continue
        a = agg.setdefault(matched["name"], {
            "name": matched["name"], "category": matched["category"],
            "borrowed": 0.0, "repaid": 0.0, "count": 0})
        a["borrowed"] += r["paid_in"]
        a["repaid"] += r["withdrawn"]
        a["count"] += 1
    out = []
    for a in agg.values():
        a["borrowed"] = round(a["borrowed"], 2)
        a["repaid"] = round(a["repaid"], 2)
        a["net"] = round(a["borrowed"] - a["repaid"], 2)
        out.append(a)
    out.sort(key=lambda x: x["repaid"] + x["borrowed"], reverse=True)
    return out


def _integrity_check(rows: list[dict]) -> tuple[list[dict], bool]:
    flags: list[dict] = []
    # Balance continuity: balance[i] should ≈ balance[i-1] + paid_in - withdrawn.
    breaks = 0
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        if prev["balance"] is None or cur["balance"] is None:
            continue
        expected = prev["balance"] + cur["paid_in"] - cur["withdrawn"]
        if abs(expected - cur["balance"]) > 1.0:  # allow rounding
            breaks += 1
    if breaks:
        pct = breaks / max(len(rows) - 1, 1)
        flags.append({"code": "balance_discontinuity",
                      "severity": "high" if pct > 0.15 else "medium",
                      "detail": f"{breaks} of {len(rows)-1} rows break running-balance continuity."})
    # Duplicate receipts.
    seen, dups = set(), 0
    for r in rows:
        if r["receipt"]:
            if r["receipt"] in seen:
                dups += 1
            seen.add(r["receipt"])
    if dups:
        flags.append({"code": "duplicate_receipts", "severity": "medium",
                      "detail": f"{dups} duplicate receipt number(s) found."})
    # Large date gaps (>45 days) mid-statement.
    gaps = 0
    dated = [r["date"] for r in rows if r["date"]]
    for i in range(1, len(dated)):
        if (dated[i] - dated[i - 1]).days > 45:
            gaps += 1
    if gaps:
        flags.append({"code": "date_gaps", "severity": "low",
                      "detail": f"{gaps} gap(s) over 45 days between transactions."})
    tampering = any(f["severity"] == "high" for f in flags)
    return flags, tampering


def analyze(rows: list[dict]) -> dict:
    """Compute the full creditworthiness summary from parsed rows."""
    if not rows:
        raise StatementError("No transactions could be read from this statement. "
                             "Ensure it is the official Safaricom M-Pesa PDF statement.")

    dated = [r for r in rows if r["date"]]
    period_start = dated[0]["date"] if dated else None
    period_end = dated[-1]["date"] if dated else None
    days = (period_end - period_start).days if (period_start and period_end) else 0
    months = max(days / 30.0, 1.0)

    total_in = sum(r["paid_in"] for r in rows)
    total_out = sum(r["withdrawn"] for r in rows)
    net = total_in - total_out

    by_month_in: dict[str, float] = defaultdict(float)
    by_month_out: dict[str, float] = defaultdict(float)
    for r in dated:
        mk = _month_key(r["date"])
        by_month_in[mk] += r["paid_in"]
        by_month_out[mk] += r["withdrawn"]
    n_months = max(len(by_month_in), 1)
    avg_month_in = total_in / n_months
    avg_month_out = total_out / n_months

    # Income regularity: coefficient of variation of monthly inflows (lower = steadier).
    inflows = list(by_month_in.values()) or [0.0]
    mean_in = sum(inflows) / len(inflows)
    var = sum((x - mean_in) ** 2 for x in inflows) / len(inflows)
    std = var ** 0.5
    cov = (std / mean_in) if mean_in else 1.0
    regularity = max(0.0, min(1.0, 1.0 - cov))   # 0..1

    avg_balance = sum(r["balance"] for r in rows) / len(rows)

    lenders = detect_lenders(rows)
    monthly_debt_service = sum(l["repaid"] for l in lenders) / n_months
    total_borrowed = sum(l["borrowed"] for l in lenders)

    flags, tampering = _integrity_check(rows)

    net_monthly = avg_month_in - avg_month_out
    # Comfortable installment: a conservative slice of average monthly surplus,
    # reduced by existing monthly debt-service load.
    disposable = max(net_monthly, 0)
    comfortable = max(0.0, 0.33 * disposable - 0.5 * monthly_debt_service)

    score = _affordability_score(avg_month_in, net_monthly, regularity,
                                 monthly_debt_service, avg_balance, len(lenders), tampering)

    summary = {
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "months_covered": round(months, 1),
        "transactions_count": len(rows),
        "total_inflow": round(total_in, 2),
        "total_outflow": round(total_out, 2),
        "net_cash_flow": round(net, 2),
        "avg_monthly_inflow": round(avg_month_in, 2),
        "avg_monthly_outflow": round(avg_month_out, 2),
        "net_monthly_cash_flow": round(net_monthly, 2),
        "avg_balance": round(avg_balance, 2),
        "income_regularity": round(regularity, 2),
        "monthly_inflows": {k: round(v, 2) for k, v in sorted(by_month_in.items())},
        "monthly_outflows": {k: round(v, 2) for k, v in sorted(by_month_out.items())},
        "monthly_debt_service": round(monthly_debt_service, 2),
        "total_borrowed_from_lenders": round(total_borrowed, 2),
        "external_lenders_count": len(lenders),
    }
    return {
        "summary": summary,
        "detected_lenders": lenders,
        "integrity_flags": flags,
        "tampering_suspected": tampering,
        "affordability_score": score,
        "comfortable_installment": round(comfortable, 2),
        "monthly_debt_service": round(monthly_debt_service, 2),
        "net_monthly_cash_flow": round(net_monthly, 2),
        "period_start": period_start,
        "period_end": period_end,
        "months_covered": round(months, 1),
        "transactions_count": len(rows),
    }


def _affordability_score(avg_in, net_monthly, regularity, debt_service,
                         avg_balance, lender_count, tampering) -> int:
    """0-100 creditworthiness score. Higher = more affordable."""
    score = 0.0
    # Income level (max 30): KES 80k+/mo avg inflow saturates.
    score += min(avg_in / 80000.0, 1.0) * 30
    # Positive net monthly surplus (max 25).
    score += min(max(net_monthly, 0) / 30000.0, 1.0) * 25
    # Income regularity (max 20).
    score += regularity * 20
    # Healthy average balance (max 10): KES 20k+ saturates.
    score += min(max(avg_balance, 0) / 20000.0, 1.0) * 10
    # Debt-service burden penalty (max -25): relative to income.
    if avg_in > 0:
        dsr = debt_service / avg_in
        score -= min(dsr, 1.0) * 25
    # Multiple external lenders penalty.
    score -= min(lender_count, 5) * 2
    if tampering:
        score -= 25
    return int(max(0, min(100, round(score))))


def analyze_statement(data: bytes, password: str | None, filename: str = "") -> dict:
    """End-to-end: decrypt → extract → analyze. Raises StatementError on failure."""
    decrypted = decrypt_pdf(data, password)
    rows = extract_transactions(decrypted)
    result = analyze(rows)
    result["source_filename"] = filename
    return result
