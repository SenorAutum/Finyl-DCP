"""Safaricom M-Pesa Ratiba (standing-order) client (Phase 2).

Provider-agnostic: when Daraja Ratiba credentials are not configured the client
runs in a deterministic mock mode so the collections flow is fully exercisable in
dev/staging. The real API hook is isolated in `_call_ratiba_api`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import MpesaRatibaConsent, Loan


def _call_ratiba_api(*, phone: str, amount, day: int, ref: str) -> dict:
    """Real Daraja Ratiba call goes here. Returns raw provider response.

    Currently a mock: Safaricom Ratiba (Standing Order) API access is provisioned
    per-DCP. When live creds are wired via TenantIntegrationConfig, replace this
    body with the httpx call to the ratiba/standingorder endpoint.
    """
    return {"mock": True, "ResponseCode": "0", "ResponseDescription": "Accepted",
            "StandingOrderReference": ref}


def record_consent(db: Session, *, tenant_id: int, loan_id: int, phone: str,
                   consent_given: bool, deduction_amount=None, deduction_day=None,
                   ip: str | None = None) -> MpesaRatibaConsent:
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == tenant_id).first()
    if not loan:
        raise ValueError("loan_not_found")
    row = MpesaRatibaConsent(
        tenant_id=tenant_id, loan_id=loan_id, client_id=loan.borrower_id, phone=phone,
        consent_given=consent_given,
        consent_at=datetime.now(timezone.utc) if consent_given else None,
        consent_ip=ip, deduction_amount=deduction_amount, deduction_day=deduction_day,
        ratiba_status="pending" if consent_given else None,
    )
    db.add(row)
    db.commit()
    return row


def initiate(db: Session, *, tenant_id: int, loan_id: int) -> MpesaRatibaConsent:
    """Create the standing order for the most recent consented row on the loan."""
    row = (db.query(MpesaRatibaConsent)
           .filter(MpesaRatibaConsent.tenant_id == tenant_id,
                   MpesaRatibaConsent.loan_id == loan_id,
                   MpesaRatibaConsent.consent_given == True)  # noqa: E712
           .order_by(MpesaRatibaConsent.created_at.desc()).first())
    if not row:
        raise ValueError("no_consent")
    ref = f"RATIBA-{uuid.uuid4().hex[:12].upper()}"
    resp = _call_ratiba_api(phone=row.phone, amount=row.deduction_amount,
                            day=row.deduction_day, ref=ref)
    ok = str(resp.get("ResponseCode")) == "0"
    row.ratiba_ref = resp.get("StandingOrderReference", ref)
    row.ratiba_status = "active" if ok else "failed"
    row.raw_response = resp
    db.commit()
    return row


def status_for_loan(db: Session, *, tenant_id: int, loan_id: int) -> dict:
    row = (db.query(MpesaRatibaConsent)
           .filter(MpesaRatibaConsent.tenant_id == tenant_id,
                   MpesaRatibaConsent.loan_id == loan_id)
           .order_by(MpesaRatibaConsent.created_at.desc()).first())
    if not row:
        return {"loan_id": loan_id, "status": "none", "consent_given": False}
    return {"loan_id": loan_id, "status": row.ratiba_status or "none",
            "consent_given": row.consent_given, "ratiba_ref": row.ratiba_ref,
            "deduction_amount": float(row.deduction_amount) if row.deduction_amount else None,
            "deduction_day": row.deduction_day}
