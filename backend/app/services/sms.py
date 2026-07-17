"""
Centralized SMS helper.

>>> PLACEHOLDER — REAL BULK-SMS PROVIDER INTEGRATION GOES HERE <<<
To go live, replace `_dispatch_to_provider` with an HTTP call to your bulk-SMS
gateway (e.g. Africa's Talking / Twilio) using SMS_API_URL / SMS_API_KEY from
the environment. Everything else (logging, triggers) stays unchanged.
"""
import random
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import SmsLog


def _dispatch_to_provider(phone: str, message: str) -> str:
    """MOCK provider call. Swap with e.g.:
        httpx.post(settings.SMS_API_URL, headers={"apiKey": settings.SMS_API_KEY},
                   json={"to": phone, "message": message})
    Returns provider status."""
    _ = settings.SMS_API_URL  # placeholder credentials are read but unused in mock mode
    return "sent"


def send_sms(db: Session, tenant_id: int, phone: str, message: str, trigger_type: str = "manual") -> SmsLog:
    """Send (mock) an SMS and record it in sms_logs."""
    status = _dispatch_to_provider(phone, message)
    log = SmsLog(
        tenant_id=tenant_id,
        recipient_phone=phone,
        message=message,
        trigger_type=trigger_type,
        status=status,
        sent_at=datetime.utcnow(),
    )
    db.add(log)
    db.flush()
    return log


# --- Trigger message templates ------------------------------------------------

def sms_loan_approval(db, tenant_id, borrower, loan):
    return send_sms(
        db, tenant_id, borrower.phone,
        f"Dear {borrower.first_name}, your loan {loan.account_number} of KES "
        f"{float(loan.principal):,.0f} has been APPROVED and is being disbursed to "
        f"your M-Pesa. Repay by {loan.due_date}. Finyl-DCP.",
        "loan_approval",
    )


def sms_repayment_reminder(db, tenant_id, borrower, loan, days_left: int):
    return send_sms(
        db, tenant_id, borrower.phone,
        f"Hi {borrower.first_name}, a friendly reminder: loan {loan.account_number} "
        f"balance KES {float(loan.outstanding_balance):,.0f} is due in {days_left} day(s) "
        f"on {loan.due_date}. Pay via M-Pesa Paybill. Finyl-DCP.",
        "repayment_reminder",
    )


def sms_overdue_alert(db, tenant_id, borrower, loan):
    return send_sms(
        db, tenant_id, borrower.phone,
        f"Dear {borrower.first_name}, loan {loan.account_number} is OVERDUE. "
        f"Outstanding KES {float(loan.outstanding_balance):,.0f}. Penalties may apply. "
        f"Kindly settle to protect your credit score. Finyl-DCP.",
        "overdue_alert",
    )


def sms_payment_receipt(db, tenant_id, borrower, loan, amount, ref):
    return send_sms(
        db, tenant_id, borrower.phone,
        f"Payment received: KES {amount:,.0f} for loan {loan.account_number} "
        f"(ref {ref}). New balance KES {float(loan.outstanding_balance):,.0f}. Thank you! Finyl-DCP.",
        "manual",
    )


def sms_ticket_resolution(db, tenant_id, phone, ticket_id):
    return send_sms(
        db, tenant_id, phone,
        f"Your complaint {ticket_id} has been RESOLVED. Thank you for your patience. "
        f"Reply HELP for further assistance. Finyl-DCP.",
        "ticket_resolution",
    )
