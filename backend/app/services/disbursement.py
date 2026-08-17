"""
Money-movement execution helpers (real Daraja B2C, credential-gated). Kept in one
place so the direct path (below threshold) and the maker-checker approval path
both run the exact same side-effects. When Daraja credentials are not configured
the payout raises a clear 422 (credentials required) instead of faking a success.
"""
from datetime import date

from fastapi import HTTPException

from app.models import Loan, PaymentTransaction
from app.services import mpesa, sms


def _payout(phone: str, amount: float, remarks: str) -> dict:
    """Wrap the Daraja B2C call, mapping gating/errors to actionable HTTP codes."""
    try:
        return mpesa.b2c_disburse(phone, amount, remarks)
    except mpesa.DarajaNotConfigured as exc:
        raise HTTPException(422, f"M-Pesa (Daraja) credentials required: {exc}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Daraja B2C payout failed: {exc}")


def execute_disbursement(db, tenant_id, loan: Loan, actor_user_id: int) -> dict:
    """Run the Daraja B2C payout for an approved loan and activate it."""
    payload = _payout(loan.borrower.phone, float(loan.principal),
                      f"Disbursement {loan.account_number}")
    txn = PaymentTransaction(
        tenant_id=tenant_id, type="b2c", loan_id=loan.id, amount=loan.principal,
        phone=loan.borrower.phone, mpesa_ref=payload["result"]["TransactionReceipt"],
        status="success", raw_payload=payload,
    )
    db.add(txn)
    loan.status = "active"
    loan.disbursed_by_user_id = actor_user_id
    loan.disbursement_date = date.today()
    step = 7 if loan.product and loan.product.tenure_unit == "weeks" else 30
    tenure = loan.product.tenure_value if loan.product else 4
    from datetime import timedelta
    loan.due_date = date.today() + timedelta(days=step * max(1, tenure))
    loan.outstanding_balance = round(loan.total_due, 2)
    try:
        sms.sms_loan_disbursed(db, tenant_id, loan.borrower, loan)
    except Exception:
        pass
    return {"mpesa_ref": txn.mpesa_ref, "amount": float(loan.principal)}


def execute_refund(db, tenant_id, amount: float, phone: str, loan_id, actor_user_id: int,
                   reason: str | None = None) -> dict:
    """Run a Daraja B2C refund payout (credential-gated)."""
    payload = _payout(phone, float(amount), reason or "Refund")
    txn = PaymentTransaction(
        tenant_id=tenant_id, type="refund", loan_id=loan_id, amount=amount,
        phone=phone, mpesa_ref=payload["result"]["TransactionReceipt"],
        status="success", raw_payload=payload,
    )
    db.add(txn)
    return {"mpesa_ref": txn.mpesa_ref, "amount": float(amount)}
