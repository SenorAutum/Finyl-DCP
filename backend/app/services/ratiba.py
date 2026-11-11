"""Safaricom M-Pesa Ratiba (standing orders) — WP12: the real Daraja service.

How it works:
  1. Staff record the client's consent: the M-Pesa number, how much, how often
     and from/to which dates.
  2. "Initiate" asks Safaricom to create the standing order on the DCP's own
     paybill (Daraja createStandingOrderExternal). The client confirms it on
     their phone; Safaricom then calls back (ratiba-callback) and the order is
     marked active, or failed.
  3. Each time the order runs, the money arrives as an ordinary paybill payment
     whose account number is the client's customer number, so it is matched
     and applied like any other paybill payment.

Before WP12 the Safaricom call was a mock that always "succeeded". A
simulation is now only used where WP8 allows one (sandbox / tests without M-Pesa
keys); with production keys or a production platform it is refused.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import Borrower, Loan, MpesaRatibaConsent

# Daraja Ratiba frequency codes.
FREQUENCIES = {
    "1": "Once", "2": "Daily", "3": "Weekly", "4": "Monthly",
    "5": "Every two months", "6": "Quarterly", "7": "Every six months", "8": "Yearly",
}
STATUS_LABELS = {
    "pending": "Consent recorded", "awaiting_customer": "Waiting for the client to confirm",
    "active": "Active", "failed": "Failed", "simulated": "Simulated (no M-Pesa keys)",
}


class RatibaError(ValueError):
    """Shown to the user (HTTP 4xx)."""


def _loan(db, tenant_id, loan_id) -> Loan:
    from app.services.repayments import COLLECTIBLE_STATUSES
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == tenant_id).first()
    if loan is None:
        raise RatibaError("loan_not_found")
    if loan.status not in COLLECTIBLE_STATUSES:
        raise RatibaError(f"The loan is {loan.status}; a standing order only makes sense on an open loan.")
    return loan


def record_consent(db: Session, *, tenant_id: int, loan_id: int, phone: str,
                   consent_given: bool, deduction_amount=None, deduction_day=None,
                   ip: str | None = None, frequency: str | None = None,
                   start_date: date | None = None, end_date: date | None = None,
                   user_id: int | None = None) -> MpesaRatibaConsent:
    from app.services.mpesa import normalise_msisdn
    loan = _loan(db, tenant_id, loan_id)
    msisdn = normalise_msisdn(phone or "")
    if len(msisdn) != 12 or not (msisdn.startswith("2547") or msisdn.startswith("2541")):
        raise RatibaError("Enter the client's Safaricom number, e.g. 0712345678.")
    amount = None
    if deduction_amount is not None:
        try:
            amount = Decimal(str(deduction_amount))
        except InvalidOperation:
            raise RatibaError("The amount is not a number.")
        if amount <= 0 or amount != amount.to_integral_value():
            raise RatibaError("M-Pesa standing orders take whole shillings above zero.")
    freq = str(frequency or ("4" if deduction_day else "")).strip()
    if consent_given:
        if amount is None:
            raise RatibaError("Enter the amount to deduct each time.")
        if freq not in FREQUENCIES:
            raise RatibaError("Choose how often to deduct (daily, weekly, monthly ...).")
        start_date = start_date or (date.today() + timedelta(days=1))
        end_date = end_date or loan.due_date or (start_date + timedelta(days=365))
        if end_date < start_date:
            raise RatibaError("The end date is before the start date.")
    row = MpesaRatibaConsent(
        tenant_id=tenant_id, loan_id=loan_id, client_id=loan.borrower_id, phone=msisdn,
        consent_given=consent_given,
        consent_at=datetime.now(timezone.utc) if consent_given else None,
        consent_ip=ip, deduction_amount=amount, deduction_day=deduction_day,
        frequency=freq or None, start_date=start_date, end_date=end_date,
        created_by=user_id,
        ratiba_status="pending" if consent_given else None,
    )
    db.add(row)
    db.flush()
    return row


def _request(db, row: MpesaRatibaConsent, loan: Loan, creds) -> dict:
    """The body Daraja expects (createStandingOrderExternal)."""
    from app.services import mpesa
    borrower = db.get(Borrower, loan.borrower_id)
    reference = (borrower.customer_number if borrower and borrower.customer_number
                 else loan.account_number)[:12]
    return {
        "StandingOrderName": f"Loan {loan.account_number}"[:60],
        "StartDate": row.start_date.strftime("%Y%m%d"),
        "EndDate": row.end_date.strftime("%Y%m%d"),
        "BusinessShortCode": creds.shortcode,
        "TransactionType": "Standing Order Customer Pay Bill",
        "ReceiverPartyIdentifierType": "4",
        "Amount": str(mpesa.whole_shillings(row.deduction_amount)),
        "PartyA": row.phone,
        "CallBackURL": mpesa.callback_url("ratiba-callback"),
        "AccountReference": reference,
        "TransactionDesc": "Loan repay",
        "Frequency": row.frequency,
    }


def _call_ratiba_api(creds, payload: dict) -> dict:
    import httpx
    from app.services import mpesa
    token = mpesa.get_access_token(creds=creds)
    resp = httpx.post(f"{creds.base_url}/standingorder/v1/createStandingOrderExternal",
                      headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=45)
    resp.raise_for_status()
    return resp.json()


def initiate(db: Session, *, tenant_id: int, loan_id: int) -> MpesaRatibaConsent:
    """Ask Safaricom to create the standing order for the latest consent."""
    from app.services import mpesa
    loan = _loan(db, tenant_id, loan_id)
    row = (db.query(MpesaRatibaConsent)
           .filter(MpesaRatibaConsent.tenant_id == tenant_id,
                   MpesaRatibaConsent.loan_id == loan_id,
                   MpesaRatibaConsent.consent_given == True)  # noqa: E712
           .order_by(MpesaRatibaConsent.created_at.desc(), MpesaRatibaConsent.id.desc()).first())
    if not row:
        raise RatibaError("no_consent")
    if row.ratiba_status in ("awaiting_customer", "active"):
        raise RatibaError("This standing order was already sent to Safaricom.")
    if not row.frequency or row.deduction_amount is None or not row.start_date:
        raise RatibaError("The consent is missing the amount, how often or the dates. "
                          "Record the consent again.")
    creds = mpesa.resolve_creds(db, tenant_id)
    if not creds.configured:
        mpesa.guard_no_simulation(creds, "standing order")      # refuses in production
        ref = f"SIM-{uuid.uuid4().hex[:12].upper()}"
        row.request_ref, row.ratiba_ref, row.ratiba_status = ref, ref, "simulated"
        row.raw_response = {"simulated": True, "request": _request(db, row, loan, creds)}
        db.flush()
        return row
    payload = _request(db, row, loan, creds)
    try:
        resp = _call_ratiba_api(creds, payload)
    except Exception as exc:                       # Safaricom refused or unreachable
        detail = getattr(getattr(exc, "response", None), "text", "") or str(exc)
        row.ratiba_status = "failed"
        row.raw_response = {"request": payload, "error": detail[:500]}
        db.flush()
        return row
    head = resp.get("ResponseHeader") or resp.get("responseHeader") or {}
    code = str(head.get("responseCode") or head.get("ResponseCode") or "")
    row.request_ref = str(head.get("responseRefID") or head.get("responseRefId") or "")[:80] or None
    row.ratiba_status = "awaiting_customer" if code in ("200", "0") else "failed"
    row.raw_response = {"request": payload, "response": resp}
    db.flush()
    return row


def apply_callback(db: Session, body: dict) -> MpesaRatibaConsent | None:
    """Safaricom's answer after the client confirmed (or not)."""
    head = body.get("responseHeader") or body.get("ResponseHeader") or {}
    refs = {str(head.get(k)) for k in ("responseRefID", "requestRefID", "responseRefId",
                                       "requestRefId") if head.get(k)}
    if not refs:
        return None
    row = (db.query(MpesaRatibaConsent)
           .filter(MpesaRatibaConsent.request_ref.in_(refs)).first())
    if row is None:
        return None
    data = {}
    for item in ((body.get("responseBody") or body.get("ResponseBody") or {})
                 .get("responseData") or []):
        if isinstance(item, dict) and "name" in {k.lower() for k in item}:
            name = item.get("name") or item.get("Name")
            data[str(name)] = item.get("value", item.get("Value"))
    code = str(head.get("responseCode", head.get("ResultCode", "")))
    status = str(data.get("Status") or "").upper()
    row.ratiba_status = "active" if code == "0" and status in ("", "OKAY", "OK", "SUCCESS") else "failed"
    tx = data.get("TransactionID")
    if tx and str(tx).strip("0"):
        row.ratiba_ref = str(tx)[:200]
    row.raw_response = {**(row.raw_response or {}), "callback": body}
    db.flush()
    return row


def status_for_loan(db: Session, *, tenant_id: int, loan_id: int) -> dict:
    row = (db.query(MpesaRatibaConsent)
           .filter(MpesaRatibaConsent.tenant_id == tenant_id,
                   MpesaRatibaConsent.loan_id == loan_id)
           .order_by(MpesaRatibaConsent.created_at.desc(), MpesaRatibaConsent.id.desc()).first())
    if not row:
        return {"loan_id": loan_id, "status": "none", "consent_given": False}
    return {"loan_id": loan_id, "status": row.ratiba_status or "none",
            "status_label": STATUS_LABELS.get(row.ratiba_status or "", "—"),
            "consent_given": row.consent_given, "ratiba_ref": row.ratiba_ref,
            "phone": row.phone,
            "deduction_amount": float(row.deduction_amount) if row.deduction_amount else None,
            "deduction_day": row.deduction_day,
            "frequency": row.frequency, "frequency_label": FREQUENCIES.get(row.frequency or ""),
            "start_date": row.start_date.isoformat() if row.start_date else None,
            "end_date": row.end_date.isoformat() if row.end_date else None,
            "error": (row.raw_response or {}).get("error") if row.ratiba_status == "failed" else None}
