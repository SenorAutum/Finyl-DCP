"""
Money-movement execution helpers (mock Daraja B2C). Kept in one place so the
direct path (below threshold) and the maker-checker approval path both run the
exact same side-effects.
"""
from datetime import date

from app.models import Loan, PaymentTransaction
from app.services import mpesa, sms


def execute_disbursement(db, tenant_id, loan: Loan, actor_user_id: int) -> dict:
    """Run the mock Daraja B2C payout for an approved loan and activate it."""
    payload = mpesa.b2c_disburse(loan.borrower.phone, float(loan.principal),
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
        sms.sms_loan_approval(db, tenant_id, loan.borrower, loan)
    except Exception:
        pass
    return {"mpesa_ref": txn.mpesa_ref, "amount": float(loan.principal)}


def execute_refund(db, tenant_id, amount: float, phone: str, loan_id, actor_user_id: int,
                   reason: str | None = None) -> dict:
    """Run a mock Daraja B2C refund payout."""
    payload = mpesa.b2c_disburse(phone, float(amount), reason or "Refund")
    txn = PaymentTransaction(
        tenant_id=tenant_id, type="refund", loan_id=loan_id, amount=amount,
        phone=phone, mpesa_ref=payload["result"]["TransactionReceipt"],
        status="success", raw_payload=payload,
    )
    db.add(txn)
    return {"mpesa_ref": txn.mpesa_ref, "amount": float(amount)}
