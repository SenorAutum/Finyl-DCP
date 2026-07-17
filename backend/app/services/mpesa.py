"""
Safaricom Daraja M-Pesa integration (MOCK).

>>> PLACEHOLDER — REAL DARAJA INTEGRATION GOES HERE <<<
Each function simulates the real Daraja request/response JSON shape. To go
live: obtain an OAuth token with DARAJA_CONSUMER_KEY/SECRET, then call the
real endpoints:
  B2C        POST https://api.safaricom.co.ke/mpesa/b2c/v3/paymentrequest
  STK Push   POST https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest
  C2B        register confirmation/validation URLs pointing at our callback.
Keep the return shapes identical and the rest of the app works unchanged.
"""
import random
import string
import uuid
from datetime import datetime

from app.core.config import settings


def _mpesa_ref() -> str:
    """Generate a plausible M-Pesa receipt e.g. 'SGH7K2M9QX'."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


def b2c_disburse(phone: str, amount: float, remarks: str) -> dict:
    """Simulate a Daraja B2C payment request (disbursement to borrower)."""
    _ = settings.DARAJA_CONSUMER_KEY  # placeholder creds read here in real impl
    conversation_id = f"AG_{datetime.utcnow():%Y%m%d}_{uuid.uuid4().hex[:16]}"
    return {
        "request": {
            "InitiatorName": "finyl-api",
            "CommandID": "BusinessPayment",
            "Amount": amount,
            "PartyA": settings.DARAJA_SHORTCODE,
            "PartyB": phone,
            "Remarks": remarks,
        },
        "response": {
            "ConversationID": conversation_id,
            "OriginatorConversationID": str(uuid.uuid4()),
            "ResponseCode": "0",
            "ResponseDescription": "Accept the service request successfully.",
        },
        "result": {
            "ResultCode": 0,
            "ResultDesc": "The service request is processed successfully.",
            "TransactionReceipt": _mpesa_ref(),
            "TransactionAmount": amount,
            "ReceiverPartyPublicName": phone,
            "TransactionCompletedDateTime": datetime.utcnow().strftime("%d.%m.%Y %H:%M:%S"),
        },
    }


def stk_push(phone: str, amount: float, account_ref: str) -> dict:
    """Simulate a Daraja Lipa-na-M-Pesa STK push (collections prompt)."""
    return {
        "request": {
            "BusinessShortCode": settings.DARAJA_SHORTCODE,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": phone,
            "PhoneNumber": phone,
            "AccountReference": account_ref,
            "TransactionDesc": "Loan repayment",
        },
        "response": {
            "MerchantRequestID": f"{random.randint(10000,99999)}-{random.randint(10000000,99999999)}-1",
            "CheckoutRequestID": f"ws_CO_{datetime.utcnow():%d%m%Y%H%M%S}{random.randint(100000,999999)}",
            "ResponseCode": "0",
            "ResponseDescription": "Success. Request accepted for processing",
            "CustomerMessage": "Success. Request accepted for processing",
        },
    }
