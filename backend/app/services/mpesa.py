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



def normalise_msisdn(phone: str) -> str:
    """Normalise a Kenyan mobile number to Daraja's 2547XXXXXXXX format."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    elif digits.startswith("7") or digits.startswith("1"):
        digits = "254" + digits
    elif digits.startswith("254254"):
        digits = digits[3:]
    return digits


def validate_mobile_number(phone: str, national_id: str, expected_name: str) -> dict:
    """
    Safaricom subscriber name-lookup check (MOCK).

    >>> PLACEHOLDER — REAL SAFARICOM/DARAJA VALIDATION GOES HERE <<<
    Confirms the mobile number is registered on M-Pesa under the client's
    National ID. In production this is either:
      * Daraja "Account Balance"/"Transaction Status" style registered-name
        lookup, or
      * the operator's KYC name-verification API (bank-grade partners only),
    authenticated with DARAJA_CONSUMER_KEY/SECRET. Keep this return shape and
    the router/UI keep working unchanged.
    """
    _ = (settings.DARAJA_CONSUMER_KEY, settings.DARAJA_PASSKEY)  # read in real impl
    msisdn = normalise_msisdn(phone)
    valid_prefix = msisdn.startswith("2547") or msisdn.startswith("2541")
    ok = bool(msisdn) and len(msisdn) == 12 and valid_prefix and bool(national_id)
    registered_name = (expected_name or "").upper() if ok else None
    return {
        "request": {
            "CommandID": "CheckIdentity",
            "PartyA": settings.DARAJA_SHORTCODE,
            "PartyB": msisdn,
            "IdentityNumber": national_id,
            "Initiator": "finyl-api",
        },
        "response": {
            "ResultCode": 0 if ok else 1,
            "ResultDesc": ("The service request is processed successfully."
                           if ok else "Subscriber not found or number not registered."),
            "MSISDN": msisdn,
            "RegisteredName": registered_name,
            "IdentityNumber": national_id,
            "Matched": ok,
            "ConversationID": f"AG_{datetime.utcnow():%Y%m%d}_{uuid.uuid4().hex[:16]}",
            "CheckedAt": datetime.utcnow().isoformat() + "Z",
        },
    }
