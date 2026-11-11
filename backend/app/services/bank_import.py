"""
WP11 — bank statement import.

A DCP downloads its bank statement (CSV or Excel .xlsx) and uploads it. Each
money-in line is matched to a loan by what the customer wrote in the
narrative: a loan account number (e.g. FL/FY2026/33/38) or a customer number
(7 digits with a check digit). Then:

  * matched lines are recorded like a manual payment: applied at once below
    the DCP's manual-payment threshold, otherwise they wait for a second person;
  * lines that cannot be matched go to the Suspense Account (source "bank"),
    with a suggested loan where one fits, for a person to allocate or refund;
  * a line whose bank reference is already recorded is skipped, so importing
    the same statement twice changes nothing.

Every bank lays out its statement differently, so the columns are found by
their headings (Date / Value date, Description / Narrative / Particulars,
Credit / Money in / Deposits, Debit, Reference, Balance) and a DCP can correct
the choice and save it as a named layout for next time.

Only the first sheet of an .xlsx file is read. Old .xls files and PDFs are not
read: save the statement as CSV or .xlsx first.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from sqlalchemy import func

from app.core import customer_number as cn
from app.core.money import money
from app.models import Borrower, Loan, TenantIntegrationConfig

MAX_ROWS = 5000            # lines read from one file
MAX_CREDITS = 2000         # money-in lines imported from one file
MAX_FILE_BYTES = 10 * 1024 * 1024
LAYOUTS_INTEGRATION = "bank_import_layouts"
FIELDS = ("date", "description", "reference", "credit", "debit", "amount", "balance",
          "account", "payer", "status")      # WP12: account, payer, status (M-Pesa statements)
KINDS = ("bank", "mpesa")


class StatementError(ValueError):
    """The file cannot be read as a bank statement (shown to the user)."""


# --------------------------------------------------------------------------- #
# Reading the file into rows of text
# --------------------------------------------------------------------------- #
def read_rows(data: bytes, filename: str = "") -> list[list[str]]:
    if not data:
        raise StatementError("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise StatementError("The file is larger than 10 MB. Split the statement into shorter periods.")
    name = (filename or "").lower()
    if data[:4] == b"PK\x03\x04" or name.endswith(".xlsx"):
        return _read_xlsx(data)
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" or name.endswith(".xls"):
        raise StatementError("Old Excel files (.xls) cannot be read. Open the file in Excel and "
                             "save it as .xlsx or CSV, then upload that.")
    if data[:5] == b"%PDF-" or name.endswith(".pdf"):
        raise StatementError("PDF statements cannot be read. Download the statement from your bank "
                             "as CSV or Excel instead.")
    return _read_csv(data)


def _read_csv(data: bytes) -> list[list[str]]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = "\n".join(text.splitlines()[:50])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if i >= MAX_ROWS:
            break
        rows.append([(c or "").strip() for c in row])
    return rows


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
_DATE_FORMAT_IDS = set(range(14, 23)) | {45, 46, 47}


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _excel_date(serial: float) -> str:
    when = datetime(1899, 12, 30) + timedelta(days=serial)
    return when.strftime("%Y-%m-%d %H:%M:%S" if when.time() != datetime.min.time() else "%Y-%m-%d")


def _read_xlsx(data: bytes) -> list[list[str]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise StatementError("This file is not a readable Excel (.xlsx) file.")
    if sum(i.file_size for i in z.infolist()) > 20 * MAX_FILE_BYTES:
        raise StatementError("The Excel file is too large once unpacked.")

    def load(path):
        try:
            return ET.fromstring(z.read(path))
        except KeyError:
            return None

    # First sheet, as the workbook lists it.
    sheet_path = "xl/worksheets/sheet1.xml"
    wb, rels = load("xl/workbook.xml"), load("xl/_rels/workbook.xml.rels")
    if wb is not None and rels is not None:
        first = wb.find("m:sheets/m:sheet", _NS)
        if first is not None:
            rid = first.get(f"{{{_NS['r']}}}id")
            for rel in rels.findall("rel:Relationship", _NS):
                if rel.get("Id") == rid:
                    target = rel.get("Target", "").lstrip("/")
                    sheet_path = target if target.startswith("xl/") else "xl/" + target
    sheet = load(sheet_path)
    if sheet is None:
        raise StatementError("The Excel file has no readable sheet.")

    shared = []
    sst = load("xl/sharedStrings.xml")
    if sst is not None:
        for si in sst.findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))

    # Which cell styles are dates.
    date_styles = set()
    styles = load("xl/styles.xml")
    if styles is not None:
        custom = {}
        for nf in styles.findall("m:numFmts/m:numFmt", _NS):
            code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", nf.get("formatCode", "").lower())
            custom[int(nf.get("numFmtId", "0"))] = ("d" in code or "y" in code) and "0.0" not in code
        for i, xf in enumerate(styles.findall("m:cellXfs/m:xf", _NS)):
            fid = int(xf.get("numFmtId", "0"))
            if fid in _DATE_FORMAT_IDS or custom.get(fid):
                date_styles.add(i)

    rows = []
    for row in sheet.iter(f"{{{_NS['m']}}}row"):
        if len(rows) >= MAX_ROWS:
            break
        cells = {}
        for c in row.findall("m:c", _NS):
            idx = _col_index(c.get("r", "A"))
            if idx < 0 or idx > 200:
                continue
            kind = c.get("t")
            v = c.find("m:v", _NS)
            raw = v.text if v is not None else None
            if kind == "s" and raw is not None:
                val = shared[int(raw)] if int(raw) < len(shared) else ""
            elif kind == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            elif raw is None:
                val = ""
            elif kind in (None, "n") and int(c.get("s", "0")) in date_styles:
                try:
                    val = _excel_date(float(raw))
                except (ValueError, OverflowError):
                    val = raw
            else:
                val = raw
            cells[idx] = (val or "").strip()
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
        else:
            rows.append([])
    return rows


# --------------------------------------------------------------------------- #
# Finding the columns
# --------------------------------------------------------------------------- #
_SYNONYMS = {
    "date": ["transaction date", "trans date", "txn date", "tran date", "posting date",
             "post date", "booking date", "completion time", "date", "value date",
             "initiation time"],
    "description": ["description", "narrative", "narration", "particulars", "details",
                    "transaction details", "transaction description", "remarks", "memo"],
    "reference": ["reference", "ref", "ref no", "reference no", "reference number",
                  "transaction reference", "transaction ref", "bank reference", "cheque no",
                  "chq no", "cheque number", "document no", "transaction id", "receipt no"],
    "credit": ["credit", "credits", "credit amount", "money in", "paid in", "amount in",
               "deposit", "deposits", "cr", "lodgement", "lodgements"],
    "debit": ["debit", "debits", "debit amount", "money out", "paid out", "amount out",
              "withdrawal", "withdrawals", "dr", "withdrawn"],
    "amount": ["amount", "transaction amount", "amount kes", "kes amount"],
    "balance": ["balance", "running balance", "closing balance", "book balance",
                "ledger balance", "available balance"],
    # WP12: what the payer typed as the account (M-Pesa statements: "A/C No.").
    "account": ["a c no", "ac no", "account no", "account number", "bill reference",
                "bill ref number", "billrefnumber"],
    "payer": ["other party info", "other party", "payer", "sender", "customer"],
    "status": ["transaction status", "status"],
}


def _norm(h) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(h or "").lower()).split())


def _guess(header: list[str]) -> dict:
    """{field: column heading} for the headings this row has."""
    normed = [_norm(h) for h in header]
    out, used = {}, set()
    for fld in ("date", "credit", "debit", "amount", "account", "balance", "reference",
                "status", "payer", "description"):
        # An exact heading wins over one that only starts or ends with the word
        # ("Receipt No." over "Linked Transaction ID" for the reference).
        hit = next((i for syn in _SYNONYMS[fld] for i, h in enumerate(normed)
                    if h == syn and i not in used), None)
        if hit is None:
            hit = next((i for syn in _SYNONYMS[fld] for i, h in enumerate(normed)
                        if i not in used and h and (h.startswith(syn + " ") or h.endswith(" " + syn))),
                       None)
        if hit is not None:
            out[fld] = header[hit]
            used.add(hit)
    return out


def detect_layout(rows: list[list[str]]) -> dict:
    """Find the heading row (statements often start with a few lines about the
    account) and guess the columns. Raises StatementError when nothing fits."""
    for i, row in enumerate(rows[:40]):
        g = _guess(row)
        if "date" in g and ("credit" in g or "amount" in g):
            # WP12: the M-Pesa business portal statement (Receipt No., Paid In ...).
            normed = {_norm(c) for c in row}
            kind = "mpesa" if {"receipt no", "paid in"} <= normed else "bank"
            return {"header_row": i + 1, "kind": kind, **g}
    raise StatementError("Could not find the column headings (a date column and a credit or amount "
                         "column). Choose the columns by hand.")


def _header_index(rows, layout) -> int:
    """0-based index of the heading row for a (possibly saved) layout."""
    hr = layout.get("header_row")
    wanted = [_norm(layout.get(f)) for f in FIELDS if layout.get(f)]
    if hr and 1 <= int(hr) <= len(rows):
        normed = {_norm(c) for c in rows[int(hr) - 1]}
        if all(w in normed for w in wanted):
            return int(hr) - 1
    for i, row in enumerate(rows[:40]):         # a saved layout on a longer preamble
        normed = {_norm(c) for c in row}
        if wanted and all(w in normed for w in wanted):
            return i
    raise StatementError("The chosen column headings were not found in this file.")


def clean_layout(layout: dict | None) -> dict:
    layout = layout or {}
    out = {f: (str(layout.get(f)).strip() or None) if layout.get(f) not in (None, "") else None
           for f in FIELDS}
    try:
        out["header_row"] = int(layout.get("header_row")) if layout.get("header_row") else None
    except (TypeError, ValueError):
        out["header_row"] = None
    order = str(layout.get("date_order") or "dmy").lower()
    out["date_order"] = order if order in ("dmy", "mdy", "ymd") else "dmy"
    kind = str(layout.get("kind") or "bank").lower()
    out["kind"] = kind if kind in KINDS else "bank"
    if not out["date"]:
        raise StatementError("Choose the date column.")
    if not (out["credit"] or out["amount"]):
        raise StatementError("Choose the credit (money in) column, or the amount column.")
    return out


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #
def parse_amount(value) -> Decimal | None:
    """'12,500.00' / 'KES 1,000' / '(250.00)' / '1,000.00 CR' / '-' -> Decimal or None."""
    s = str(value or "").strip().upper()
    if s in ("", "-", "—", "NIL", "N/A"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s.endswith("DR"):
        neg, s = True, s[:-2]
    elif s.endswith("CR"):
        s = s[:-2]
    s = s.replace("KES", "").replace("KSH", "").replace("KSHS", "").replace(",", "").replace(" ", "")
    if s.startswith("-"):
        neg, s = True, s[1:]
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"'{value}' is not an amount")
    return -d if neg else d


_MONTHS = "%b", "%B"


def parse_date(value, order: str = "dmy") -> datetime:
    """Kenyan date (and time) -> naive UTC, like every other timestamp."""
    s = " ".join(str(value or "").replace("T", " ").split())
    if not s:
        raise ValueError("no date")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    day_first = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"]
    month_first = ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"]
    named = [f"%d{sep}{m}{sep}{y}" for m in _MONTHS for y in ("%Y", "%y") for sep in (" ", "-", "/")]
    named += [f"{m} %d %Y" for m in _MONTHS] + [f"{m} %d, %Y" for m in _MONTHS]
    iso = ["%Y-%m-%d", "%Y/%m/%d"]
    dates = (iso + named + (month_first + day_first if order == "mdy" else day_first + month_first)
             if order != "ymd" else iso + day_first + named)
    times = ["", " %H:%M:%S", " %H:%M", " %I:%M:%S %p", " %I:%M %p"]
    for d in dates:
        for t in times:
            try:
                return datetime.strptime(s, d + t) - timedelta(hours=3)
            except ValueError:
                continue
    raise ValueError(f"'{value}' is not a date this import understands")


# --------------------------------------------------------------------------- #
# Matching a narrative to a loan
# --------------------------------------------------------------------------- #
_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9/\-]{3,}[A-Z0-9]")
_SEVEN = re.compile(r"(?<![A-Z0-9])\d{7}(?![A-Z0-9])")


@dataclass
class LineMatch:
    loan: Loan | None = None
    how: str = "none"                       # account_number | customer_number | none
    reason: str | None = None               # suspense reason when not matched
    suggested_loan_id: int | None = None


def _open_loans(db, tenant_id, borrower_id):
    from app.services.repayments import COLLECTIBLE_STATUSES
    return (db.query(Loan)
            .filter(Loan.tenant_id == tenant_id, Loan.borrower_id == borrower_id,
                    Loan.status.in_(COLLECTIBLE_STATUSES))
            .order_by(Loan.due_date.asc().nullslast(), Loan.id.asc()).all())


def match_text(db, tenant_id: int, description: str, reference: str = "") -> LineMatch:
    """Look for this DCP's loan account numbers in the narrative and reference,
    and for its customer numbers in the narrative only (bank reference and
    cheque numbers are often 7 digits too)."""
    from app.services.repayments import COLLECTIBLE_STATUSES
    text = f"{description or ''} {reference or ''}".upper()
    tokens = {t for t in _TOKEN.findall(text)}
    tokens |= {t.replace(" ", "") for t in re.findall(r"FL\s*/\s*FY\s*\d{4}\s*/\s*\d+\s*/\s*\d+", text)}
    loans = []
    if tokens:
        loans = (db.query(Loan)
                 .filter(Loan.tenant_id == tenant_id,
                         func.upper(func.replace(Loan.account_number, " ", "")).in_(tokens))
                 .all())
    customers = set()
    for t in _SEVEN.findall(str(description or "").upper()):
        if cn.is_valid(t):
            b = (db.query(Borrower)
                 .filter(Borrower.tenant_id == tenant_id,
                         Borrower.customer_number == cn.normalise(t)).first())
            if b is not None:
                customers.add(b.id)

    loan_ids = {ln.id for ln in loans}
    if len(loan_ids) > 1:
        return LineMatch(reason="bank_several")
    if len(loan_ids) == 1:
        loan = loans[0]
        if customers - {loan.borrower_id}:
            return LineMatch(reason="bank_several", suggested_loan_id=loan.id)
        if loan.status not in COLLECTIBLE_STATUSES:
            return LineMatch(reason="closed_loan", suggested_loan_id=loan.id)
        return LineMatch(loan=loan, how="account_number")
    if len(customers) > 1:
        return LineMatch(reason="bank_several")
    if len(customers) == 1:
        open_loans = _open_loans(db, tenant_id, next(iter(customers)))
        if len(open_loans) == 1:
            return LineMatch(loan=open_loans[0], how="customer_number")
        return LineMatch(reason="no_open_loan" if not open_loans else "ambiguous_customer",
                         suggested_loan_id=open_loans[0].id if open_loans else None)
    return LineMatch(reason="bank_unmatched")


# --------------------------------------------------------------------------- #
# The whole file
# --------------------------------------------------------------------------- #
@dataclass
class Line:
    line: int
    status: str                          # matched | review | duplicate | skipped | error
    paid_at: datetime | None = None
    description: str = ""
    reference: str | None = None
    amount: Decimal | None = None
    reason: str | None = None
    match: LineMatch = field(default_factory=LineMatch)
    account: str = ""                    # WP12: what the payer typed as the account
    payer_name: str | None = None
    payer_phone: str | None = None

    def as_dict(self) -> dict:
        loan = self.match.loan
        who = None
        if loan is not None and loan.borrower is not None:
            b = loan.borrower
            who = " ".join(filter(None, [b.first_name, b.last_name]))
        kenya = (self.paid_at + timedelta(hours=3)) if self.paid_at else None
        return {"line": self.line, "status": self.status,
                "date": kenya.strftime("%Y-%m-%d") if kenya else None,
                "description": self.description[:200], "reference": self.reference,
                "amount": float(self.amount) if self.amount is not None else None,
                "reason": self.reason,
                "matched_by": self.match.how if loan is not None else None,
                "loan_id": loan.id if loan is not None else None,
                "account_number": loan.account_number if loan is not None else None,
                "client": who, "account_entered": self.account or None,
                "payer": " ".join(filter(None, [self.payer_phone, self.payer_name])) or None,
                "suggested_loan_id": self.match.suggested_loan_id}


def _ref_for(ref_text, paid_at, amount, description, balance) -> str:
    from app.services.payment_approvals import clean_reference
    ref = clean_reference(ref_text)
    if ref:
        return ref
    digest = hashlib.sha1(f"{paid_at:%Y-%m-%d}|{amount}|{_norm(description)}|{balance or ''}"
                          .encode()).hexdigest()[:12].upper()
    return f"BNK{digest}"


def _already_recorded(db, tenant_id, ref) -> str | None:
    from fastapi import HTTPException
    from app.services import payment_approvals as pay_appr
    try:
        pay_appr.ensure_reference_free(db, tenant_id, ref)
    except HTTPException as exc:
        return str(exc.detail)
    return None


def analyse(db, tenant_id: int, rows: list[list[str]], layout: dict) -> list[Line]:
    """Every line after the headings, with what would happen to it."""
    layout = clean_layout(layout)
    h = _header_index(rows, layout)
    header = [_norm(c) for c in rows[h]]

    def col(fld):
        name = layout.get(fld)
        return header.index(_norm(name)) if name and _norm(name) in header else None

    cols = {f: col(f) for f in FIELDS}
    out, seen, credits = [], set(), 0
    now = datetime.utcnow()
    for i, row in enumerate(rows[h + 1:], start=h + 2):
        get = lambda f: (row[cols[f]] if cols[f] is not None and cols[f] < len(row) else "")  # noqa: E731
        if not any(c.strip() for c in row):
            continue
        desc = " ".join(get("description").split())
        state = get("status").strip().lower()
        if state and state not in ("completed", "complete", "success", "successful"):
            out.append(Line(line=i, status="skipped", description=desc,
                            reason=f"transaction {state}"))
            continue
        try:
            credit = parse_amount(get("credit")) if cols["credit"] is not None else None
            if credit is None and cols["amount"] is not None:
                credit = parse_amount(get("amount"))
                if credit is not None and cols["debit"] is not None and parse_amount(get("debit")):
                    credit = None                  # an amount column beside a debit column
        except ValueError as exc:
            out.append(Line(line=i, status="error", description=desc, reason=str(exc)))
            continue
        if credit is None or credit <= 0:
            if get("date").strip():
                out.append(Line(line=i, status="skipped", description=desc,
                                reason="not money in"))
            continue
        try:
            paid_at = parse_date(get("date"), layout["date_order"])
        except ValueError as exc:
            # A totals line ("Total credits") has an amount but no date.
            totals = not get("date").strip()
            out.append(Line(line=i, status="skipped" if totals else "error",
                            description=desc, amount=None if totals else money(credit),
                            reason="no date (a totals line?)" if totals else str(exc)))
            continue
        amount = money(credit)
        ref = _ref_for(get("reference"), paid_at, amount, desc, get("balance"))
        payer_phone, payer_name = _payer(get("payer"))
        ln = Line(line=i, status="review", paid_at=paid_at, description=desc,
                  reference=ref, amount=amount, account=get("account").strip(),
                  payer_name=payer_name, payer_phone=payer_phone)
        if paid_at > now + timedelta(days=1):
            ln.status, ln.reason = "error", "date is in the future"
        elif ref in seen:
            ln.status, ln.reason = "duplicate", "repeated in this file"
        else:
            seen.add(ref)
            used = _already_recorded(db, tenant_id, ref)
            if used:
                ln.status, ln.reason = "duplicate", used
            else:
                credits += 1
                if credits > MAX_CREDITS:
                    ln.status, ln.reason = "error", f"more than {MAX_CREDITS} money-in lines in one file"
                else:
                    ln.match = _match_line(db, tenant_id, ln, get("reference"))
                    if ln.match.loan is not None:
                        ln.status = "matched"
                    else:
                        ln.reason = ln.match.reason
        out.append(ln)
    return out


def _payer(text) -> tuple[str | None, str | None]:
    """'254712***678 - JANE WANJIKU' -> ('254712***678', 'JANE WANJIKU')."""
    s = " ".join(str(text or "").split())
    if not s:
        return None, None
    m = re.match(r"^\s*([0-9*xX#+ ]{6,20}?)\s*[-–:]\s*(.*)$", s)
    if m:
        return m.group(1).replace(" ", "")[:20] or None, (m.group(2).strip() or None)
    return None, s[:120]


def _match_line(db, tenant_id, ln: Line, reference: str) -> LineMatch:
    """WP12: an account column (what the payer typed, as on a paybill) is
    matched exactly like a paybill account number; otherwise the narrative is
    searched. Unmatched lines get a suggestion from the payer's number and name."""
    from app.services.payment_matching import match_reference, suggest_by_phone
    from app.services.repayments import COLLECTIBLE_STATUSES
    if ln.account:
        m = match_reference(db, tenant_id, ln.account)
        if m.loan is not None:
            if m.loan.status not in COLLECTIBLE_STATUSES:
                return LineMatch(reason="closed_loan", suggested_loan_id=m.loan.id)
            return LineMatch(loan=m.loan, how=m.how)
        if m.reason in ("no_open_loan", "ambiguous_customer"):
            return LineMatch(reason=m.reason, suggested_loan_id=m.suggested_loan_id)
    found = match_text(db, tenant_id, ln.description, reference)
    if found.loan is None and not found.suggested_loan_id and ln.payer_phone:
        found.suggested_loan_id = suggest_by_phone(db, tenant_id, ln.payer_phone, ln.payer_name)
    return found


def summary(lines: list[Line]) -> dict:
    def total(st):
        return float(sum((ln.amount or 0) for ln in lines if ln.status == st))
    return {st: {"count": sum(1 for ln in lines if ln.status == st), "amount": total(st)}
            for st in ("matched", "review", "duplicate", "skipped", "error")}


# --------------------------------------------------------------------------- #
# Saved layouts (per DCP)
# --------------------------------------------------------------------------- #
def get_layouts(db, tenant_id: int) -> dict:
    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == LAYOUTS_INTEGRATION).first())
    return dict(((row.config or {}).get("layouts") or {}) if row else {})


def save_layout(db, tenant_id: int, user_id: int, name: str, layout: dict) -> dict:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise StatementError("Give the layout a name, e.g. the bank and account.")
    layout = clean_layout(layout)
    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == LAYOUTS_INTEGRATION).first())
    layouts = get_layouts(db, tenant_id)
    if name not in layouts and len(layouts) >= 30:
        raise StatementError("A DCP can keep at most 30 layouts. Delete one first.")
    layouts[name] = layout
    if row is None:
        row = TenantIntegrationConfig(tenant_id=tenant_id, integration=LAYOUTS_INTEGRATION,
                                      config={"layouts": layouts}, secrets={}, enabled=True,
                                      updated_by_user_id=user_id)
        db.add(row)
    else:
        row.config = {**(row.config or {}), "layouts": layouts}
        row.updated_by_user_id = user_id
    db.flush()
    return layouts


def delete_layout(db, tenant_id: int, user_id: int, name: str) -> dict:
    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == LAYOUTS_INTEGRATION).first())
    layouts = get_layouts(db, tenant_id)
    if row is not None and name in layouts:
        layouts.pop(name)
        row.config = {**(row.config or {}), "layouts": layouts}
        row.updated_by_user_id = user_id
        db.flush()
    return layouts
