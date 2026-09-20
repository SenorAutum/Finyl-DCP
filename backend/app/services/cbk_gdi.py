"""CBK GDI (Granular Data Interface) monthly submission service.

Central Bank of Kenya Digital Credit Provider reporting. Extracts the five
mandatory datasets from the tenant's operational data, formats them per the CBK
data dictionary, obtains an OAuth2 client-credentials token and submits each
dataset (in the mandatory sequence) to the GDI REST flows.

Credential-gated: while CBK_GDI_CLIENT_ID / CBK_GDI_TOKEN_URL are empty the
service raises ConfigurationError and never attempts a live call, mirroring the
Daraja / eKYC placeholder pattern used elsewhere in the codebase.
"""
from __future__ import annotations

import calendar
import logging
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_pii
from app.models import (Borrower, CbkSubmissionLog, Complaint, Loan, Product,
                        Repayment)

logger = logging.getLogger("cbk_gdi")

# --- Endpoint paths (verbatim from the CBK SOAP UI project) ------------------
ENDPOINTS = {
    "customer":   "/test/api/v1/flows/rest/API_CUSTOMERDATASYNC/1.0/submitCustomerData",
    "loan":       "/test/api/v1/flows/rest/API_DIGITALLOANDATAS/1.0/submitDigitalLoanAccountData/",
    "repayment":  "/test/api/v1/flows/rest/API_DIGITALLOANREPAYSYNC/1.0/submitDigitalLoanRepaymentData/",
    "overdue":    "/test/api/v1/flows/rest/API_OVERDUENPLOANSSYNC/1.0/submitOverdueNPDigitalLoans",
    "complaint":  "/test/api/v1/flows/rest/API_CUSTOMERCOMPLAINSYNC/1.0/submitCustomerComplaintsData/",
    "status":     "/test/api/v1/flows/rest/COMMON_GET_REQUESTS_STATUS/1.0/getRequestsStatus",
    "nil":        "/test/api/v1/flows/rest/NilSubmission",
}

# Envelope array key per dataset.
ENVELOPE_KEYS = {
    "customer":  "CUSTOMER_DATA",
    "loan":      "DIGITALLENDER_DATA",
    "repayment": "DIGITALLOANREPAYMENTS_DATA",
    "overdue":   "OVERDUE_NP_LOANS",
    "complaint": "CUSTOMERCOMPLAINTS_DATA",
}

# Human-friendly dataset labels for the submission log / UI.
DATASET_LABELS = {
    "customer":  "Customer Data",
    "loan":      "Digital Loan Accounts",
    "repayment": "Loan Repayments",
    "overdue":   "Overdue / Non-Performing Loans",
    "complaint": "Customer Complaints",
}

# Mandatory submission sequence.
DATASET_SEQUENCE = ["customer", "loan", "repayment", "overdue", "complaint"]

_MONTHS = ["", "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Module-level token cache: {"token": str, "expires_at": epoch_seconds}.
_token_cache: dict = {}


class ConfigurationError(RuntimeError):
    """Raised when CBK GDI credentials/URLs are not configured."""


class SubmissionError(RuntimeError):
    """Raised when a CBK GDI submission fails after retry."""


# --- Formatting helpers ------------------------------------------------------
def _fmt_date(d) -> str:
    """Format a date/datetime as DD-MON-YYYY (uppercase). None -> ''."""
    if d is None:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    return f"{d.day:02d}-{_MONTHS[d.month]}-{d.year}"


def _last_day(reporting_month: date) -> date:
    last = calendar.monthrange(reporting_month.year, reporting_month.month)[1]
    return date(reporting_month.year, reporting_month.month, last)


def _reporting_date_str(reporting_month: date) -> str:
    """Last day of the reporting month as DD-MON-YYYY."""
    return _fmt_date(_last_day(reporting_month))


def _reporting_iso(reporting_month: date) -> str:
    """Last day of the reporting month as YYYY-MM-DD (envelope REPORTING_DATE)."""
    return _last_day(reporting_month).isoformat()


def _num(v) -> float:
    """Coerce a Decimal/None money value to a JSON-safe float."""
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _tenure_days(loan: Loan) -> int:
    """Loan tenure in days.

    Prefers disbursement -> due span; falls back to the product's tenure config.
    """
    if loan.disbursement_date and loan.due_date:
        days = (loan.due_date - loan.disbursement_date).days
        if days > 0:
            return days
    product = getattr(loan, "product", None)
    if product is not None:
        value = product.tenure_value or 0
        unit = (product.tenure_unit or "weeks").lower()
        if unit.startswith("week"):
            return value * 7
        if unit.startswith("month"):
            return value * 30
        if unit.startswith("day"):
            return value
    return 0


# --- OAuth2 ------------------------------------------------------------------
def _require_config() -> None:
    if not settings.CBK_GDI_TOKEN_URL or not settings.CBK_GDI_CLIENT_ID or not settings.CBK_GDI_CLIENT_SECRET:
        raise ConfigurationError(
            "CBK GDI is not configured. Set CBK_GDI_TOKEN_URL, CBK_GDI_CLIENT_ID "
            "and CBK_GDI_CLIENT_SECRET to enable live submissions."
        )


def get_token(force_refresh: bool = False) -> str:
    """Obtain an OAuth2 client-credentials access token (cached until expiry)."""
    _require_config()
    now = time.time()
    if (not force_refresh and _token_cache.get("token")
            and _token_cache.get("expires_at", 0) > now + 30):
        return _token_cache["token"]

    data = {"grant_type": "client_credentials"}
    if settings.CBK_GDI_SCOPE:
        data["scope"] = settings.CBK_GDI_SCOPE
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                settings.CBK_GDI_TOKEN_URL,
                data=data,
                auth=(settings.CBK_GDI_CLIENT_ID, settings.CBK_GDI_CLIENT_SECRET),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise SubmissionError(f"CBK GDI token request failed: {exc}") from exc

    if resp.status_code != 200:
        raise SubmissionError(
            f"CBK GDI token request returned {resp.status_code}: {resp.text[:300]}"
        )
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise SubmissionError("CBK GDI token response missing access_token")
    expires_in = int(body.get("expires_in", 3600))
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in
    return token


# --- Dataset extractors ------------------------------------------------------
def extract_customer_data(db: Session, tenant_id: int, reporting_month: date) -> list:
    """CUSTOMER_DATA — one row per borrower with a loan as of month end."""
    last = _last_day(reporting_month)
    borrowers = (
        db.query(Borrower)
        .filter(Borrower.tenant_id == tenant_id)
        .filter(Borrower.created_at <= datetime(last.year, last.month, last.day, 23, 59, 59))
        .all()
    )
    rows = []
    for b in borrowers:
        try:
            national_id = decrypt_pii(b.national_id) if b.national_id else ""
        except Exception:  # pragma: no cover - defensive
            national_id = ""
        gender = (b.gender or "").strip().upper()
        gender_code = gender[0] if gender[:1] in ("M", "F") else "C"
        rows.append({
            "CUSTOMER_ID": str(b.id),
            "PRIM_ID_DOC_TYPE": "CUSTID01",
            "PRIM_ID_DOC_NUMBER": national_id,
            "CUSTOMER_FIRST_NAME": b.first_name or "",
            "CUSTOMER_MIDDLE_NAME": b.middle_name or "",
            "CUSTOMER_LAST_NAME": b.last_name or "",
            "GENDER": gender_code,
            "DATE_OF_BIRTH": _fmt_date(b.date_of_birth),
            "NATIONALITY": "KE",
            "MOBILE_NUMBER": b.phone or "",
            "CUSTOMER_TYPE": "CUSTTYP01",
            "PHYSICAL_ADDRESS": b.location or "",
            "EMAIL_ADDRESS": "",
            "REGISTRATION_DATE": _fmt_date(b.created_at),
            "CUSTOMER_STATUS": "ACTIVE",
        })
    return rows


def extract_loan_accounts(db: Session, tenant_id: int, reporting_month: date) -> list:
    """DIGITALLENDER_DATA — one row per loan disbursed on/before month end."""
    last = _last_day(reporting_month)
    loans = (
        db.query(Loan)
        .filter(Loan.tenant_id == tenant_id)
        .filter(Loan.disbursement_date.isnot(None))
        .filter(Loan.disbursement_date <= last)
        .all()
    )
    rows = []
    for ln in loans:
        status = (ln.status or "").lower()
        account_status = "ACTIVE" if status in ("active", "overdue") else "CLOSED"
        total_due = _num(ln.total_due)
        principal = _num(ln.principal)
        rows.append({
            "CUSTOMER_ID": str(ln.borrower_id),
            "ACCOUNT_NUMBER": ln.account_number or "",
            "PRODUCT_TYPE": "LOANTYP01",
            "LOAN_AMOUNT": principal,
            "LOAN_TENURE": _tenure_days(ln),
            "INTEREST_RATE": _num(ln.interest_rate),
            "INTEREST_CALCULATION_METHOD": "INTCALC01",
            "DISBURSEMENT_DATE": _fmt_date(ln.disbursement_date),
            "MATURITY_DATE": _fmt_date(ln.due_date),
            "TOTAL_AMOUNT_PAYABLE": total_due,
            "OUTSTANDING_BALANCE": _num(ln.outstanding_balance),
            "ACCOUNT_STATUS": account_status,
            "CHANNEL_TYPE_USED": "CHN01",
            "FACILITY_FEE_CHARGE": 0,
            "INSURANCE_FEE_CHARGE": 0,
            "OTHER_CHARGES": 0,
            "APPLICATION_DATE": _fmt_date(ln.application_date),
            "APPROVAL_DATE": _fmt_date(ln.approval_date),
            "CURRENCY": "KES",
        })
    return rows


def extract_repayments(db: Session, tenant_id: int, reporting_month: date) -> list:
    """DIGITALLOANREPAYMENTS_DATA — repayments within the reporting month."""
    first = date(reporting_month.year, reporting_month.month, 1)
    last = _last_day(reporting_month)
    start = datetime(first.year, first.month, first.day, 0, 0, 0)
    end = datetime(last.year, last.month, last.day, 23, 59, 59)
    reps = (
        db.query(Repayment)
        .filter(Repayment.tenant_id == tenant_id)
        .filter(Repayment.payment_date >= start)
        .filter(Repayment.payment_date <= end)
        .all()
    )
    rows = []
    for r in reps:
        loan = getattr(r, "loan", None)
        account_number = loan.account_number if loan is not None else ""
        borrower_id = loan.borrower_id if loan is not None else None
        rows.append({
            "CUSTOMER_ID": str(borrower_id) if borrower_id is not None else "",
            "ACCOUNT_NUMBER": account_number or "",
            "REPAYMENT_DATE": _fmt_date(r.payment_date),
            "REPAYMENT_AMOUNT": _num(r.amount),
            "PRINCIPAL_REPAYMENT": _num(r.principal_component),
            "INTEREST_CHARGED_REPAYMENT": _num(r.interest_component),
            "ACCRUED_INTEREST_BALANCE": 0,
            "LATE_PAYMENT_FEE_CHARGE": 0,
            "PAYMENT_CHANNEL": "CHN01",
            "PAYMENT_REFERENCE": r.mpesa_ref or "",
            "CURRENCY": "KES",
        })
    return rows


def extract_overdue(db: Session, tenant_id: int, reporting_month: date) -> list:
    """OVERDUE_NP_LOANS — loans overdue/defaulted with due date before month end."""
    last = _last_day(reporting_month)
    loans = (
        db.query(Loan)
        .filter(Loan.tenant_id == tenant_id)
        .filter(Loan.status.in_(("overdue", "defaulted")))
        .filter(Loan.due_date.isnot(None))
        .filter(Loan.due_date < last)
        .all()
    )
    rows = []
    for ln in loans:
        outstanding = _num(ln.outstanding_balance)
        days_overdue = (last - ln.due_date).days if ln.due_date else 0
        rows.append({
            "CUSTOMER_ID": str(ln.borrower_id),
            "ACCOUNT_NUMBER": ln.account_number or "",
            "DISBURSEMENT_DATE": _fmt_date(ln.disbursement_date),
            "MATURITY_DATE": _fmt_date(ln.due_date),
            "DAYS_IN_ARREARS": days_overdue if days_overdue > 0 else 0,
            "ARREARS_AMOUNT": outstanding,
            "TOTAL_OUTSTANDING": outstanding,
            "CLASSIFICATION": "CLASS05" if days_overdue > 90 else "CLASS02",
        })
    return rows


def extract_complaints(db: Session, tenant_id: int, reporting_month: date) -> list:
    """CUSTOMERCOMPLAINTS_DATA — complaints raised within the reporting month."""
    first = date(reporting_month.year, reporting_month.month, 1)
    last = _last_day(reporting_month)
    start = datetime(first.year, first.month, first.day, 0, 0, 0)
    end = datetime(last.year, last.month, last.day, 23, 59, 59)
    complaints = (
        db.query(Complaint)
        .filter(Complaint.tenant_id == tenant_id)
        .filter(Complaint.created_at >= start)
        .filter(Complaint.created_at <= end)
        .all()
    )
    rows = []
    for c in complaints:
        borrower = getattr(c, "borrower", None)
        name = borrower.full_name if borrower is not None and hasattr(borrower, "full_name") else ""
        phone = borrower.phone if borrower is not None else ""
        status = (c.status or "").lower()
        if status in ("resolved", "closed"):
            current_status = "CUSTCST04"
        elif status == "in_progress":
            current_status = "CUSTCST02"
        else:
            current_status = "CUSTCST01"
        rows.append({
            "COMPLAINT_REFERENCE": c.ticket_id or str(c.id),
            "CUSTOMER_ID": str(c.borrower_id) if c.borrower_id is not None else "",
            "COMPLAINANT_NAME": name or "",
            "COMPLAINANT_CLASSIFICATION": "COMPCLS01",
            "COMPLAINANT_CONTACT": phone or "",
            "ACCOUNT_NUMBER": "",
            "COMPLAINT_NATURE": "NOC01",
            "COMPLAINT_DETAILS": c.description or "",
            "COMPLAINT_DATE": _fmt_date(c.created_at),
            "CURRENT_STATUS": current_status,
            "RESOLUTION_DATE": _fmt_date(c.resolved_at),
            "REMEDIAL_ACTION": c.remedial_action or "",
            "CHANNEL_OF_COMPLAINT": "CHN01",
            "CURRENCY": "KES",
        })
    return rows


EXTRACTORS = {
    "customer":  extract_customer_data,
    "loan":      extract_loan_accounts,
    "repayment": extract_repayments,
    "overdue":   extract_overdue,
    "complaint": extract_complaints,
}


# --- Submission --------------------------------------------------------------
def _post(path: str, payload: dict) -> httpx.Response:
    """POST to a GDI endpoint with a Bearer token, refreshing once on 401."""
    _require_config()
    url = settings.CBK_GDI_BASE_URL.rstrip("/") + path
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code == 401:
                token = get_token(force_refresh=True)
                headers["Authorization"] = f"Bearer {token}"
                resp = client.post(url, json=payload, headers=headers)
            return resp
    except httpx.HTTPError as exc:
        raise SubmissionError(f"CBK GDI request to {path} failed: {exc}") from exc


def submit_dataset(dataset_name: str, rows: list, reporting_month: date,
                   request_id: Optional[str] = None) -> dict:
    """Submit one dataset envelope to CBK GDI. Empty rows -> nil submission."""
    if dataset_name not in ENVELOPE_KEYS:
        raise ValueError(f"Unknown dataset: {dataset_name}")
    request_id = request_id or str(uuid.uuid4())
    envelope_key = ENVELOPE_KEYS[dataset_name]

    if not rows:
        payload = {
            "INSTITUTION_CODE": settings.CBK_INSTITUTION_CODE,
            "REQUEST_ID": request_id,
            "IS_ATTACHED": "N",
            "REPORTING_DATE": _reporting_iso(reporting_month),
            "DATASET": envelope_key,
        }
        resp = _post(ENDPOINTS["nil"], payload)
    else:
        payload = {
            "INSTITUTION_CODE": settings.CBK_INSTITUTION_CODE,
            "REQUEST_ID": request_id,
            "IS_ATTACHED": "N",
            "REPORTING_DATE": _reporting_iso(reporting_month),
            envelope_key: rows,
        }
        resp = _post(ENDPOINTS[dataset_name], payload)

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:1000]}
    return {
        "ok": 200 <= resp.status_code < 300,
        "status_code": resp.status_code,
        "response": body,
        "request_id": request_id,
    }


def run_monthly_submission(db: Session, tenant_id: int, reporting_month: date,
                           submitted_by_id: Optional[int] = None) -> dict:
    """Extract and submit all five datasets in the mandatory CBK sequence.

    Each dataset gets its own CbkSubmissionLog row (pending -> submitted/error).
    A configuration/credential problem is recorded on the row and the run stops.
    """
    results = []
    for key in DATASET_SEQUENCE:
        label = DATASET_LABELS[key]
        request_id = str(uuid.uuid4())
        try:
            rows = EXTRACTORS[key](db, tenant_id, reporting_month)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("CBK GDI extract failed for %s tenant=%s", key, tenant_id)
            rows = None
            log = CbkSubmissionLog(
                tenant_id=tenant_id, reporting_month=reporting_month,
                dataset_name=label, request_id=request_id, status="error",
                rows_submitted=0, submitted_by=submitted_by_id,
                error_detail=f"extract failed: {exc}",
            )
            db.add(log)
            db.commit()
            results.append({"name": label, "rows": 0, "status": "error",
                            "request_id": request_id, "error": str(exc)})
            continue

        log = CbkSubmissionLog(
            tenant_id=tenant_id, reporting_month=reporting_month,
            dataset_name=label, request_id=request_id, status="pending",
            rows_submitted=len(rows), submitted_by=submitted_by_id,
        )
        db.add(log)
        db.commit()

        try:
            result = submit_dataset(key, rows, reporting_month, request_id)
            log.status = "submitted" if result["ok"] else "error"
            log.rows_submitted = len(rows)
            log.response_body = result["response"]
            if not result["ok"]:
                log.error_detail = f"HTTP {result['status_code']}"
            cbk_req = None
            if isinstance(result["response"], dict):
                cbk_req = (result["response"].get("REQUEST_ID")
                           or result["response"].get("requestId"))
            if cbk_req:
                log.cbk_request_id = str(cbk_req)
            db.commit()
            results.append({"name": label, "rows": len(rows),
                            "status": log.status, "request_id": request_id})
        except (ConfigurationError, SubmissionError) as exc:
            log.status = "error"
            log.error_detail = str(exc)
            db.commit()
            results.append({"name": label, "rows": len(rows), "status": "error",
                            "request_id": request_id, "error": str(exc)})
            # Stop the sequence on a hard configuration/transport failure.
            break

    return {"reporting_month": reporting_month.isoformat(), "datasets": results}


def check_submission_status(reporting_month: date) -> dict:
    """Query CBK GDI for the status of this institution's submissions."""
    payload = {
        "INSTITUTION_CODE": settings.CBK_INSTITUTION_CODE,
        "REPORTING_DATE": _reporting_iso(reporting_month),
    }
    resp = _post(ENDPOINTS["status"], payload)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:1000]}
    return {
        "ok": 200 <= resp.status_code < 300,
        "status_code": resp.status_code,
        "response": body,
    }
