"""
Payments hub — mock Daraja M-Pesa endpoints + transaction viewer.

See app/services/mpesa.py for the annotated placeholders where real Daraja
credentials/HTTP calls plug in.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_module
from app.models import Loan, PaymentTransaction, Repayment
from app.schemas import B2CRequest, C2BCallback, StkPushRequest
from app.services import mpesa, sms

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.post("/mpesa-b2c")
def mpesa_b2c(body: B2CRequest, tenant_id: int = Depends(require_module("payments")),
              db: Session = Depends(get_db)):
    """Manual B2C disbursement for an approved loan (mock Daraja)."""
    loan = (db.query(Loan).options(joinedload(Loan.borrower), joinedload(Loan.product))
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    if loan.status != "approved":
        raise HTTPException(400, "Loan must be in 'approved' status to disburse")
    payload = mpesa.b2c_disburse(loan.borrower.phone, float(loan.principal),
                                 f"Disbursement {loan.account_number}")
    db.add(PaymentTransaction(tenant_id=tenant_id, type="b2c", loan_id=loan.id,
                              amount=loan.principal, phone=loan.borrower.phone,
                              mpesa_ref=payload["result"]["TransactionReceipt"],
                              status="success", raw_payload=payload))
    from datetime import date, timedelta
    loan.status = "active"
    loan.disbursement_date = date.today()
    step = 7 if loan.product.tenure_unit == "weeks" else 30
    loan.due_date = date.today() + timedelta(days=step * max(1, loan.product.tenure_value))
    loan.outstanding_balance = round(loan.total_due, 2)
    sms.sms_loan_approval(db, tenant_id, loan.borrower, loan)
    db.commit()
    return payload["response"]


@router.post("/stk-push")
def stk_push(body: StkPushRequest, tenant_id: int = Depends(require_module("payments")),
             db: Session = Depends(get_db)):
    """Send an STK-push collections prompt to a borrower's phone (mock Daraja)."""
    loan = (db.query(Loan).options(joinedload(Loan.borrower))
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    payload = mpesa.stk_push(loan.borrower.phone, body.amount, loan.account_number)
    db.add(PaymentTransaction(tenant_id=tenant_id, type="stk_push", loan_id=loan.id,
                              amount=body.amount, phone=loan.borrower.phone,
                              mpesa_ref=payload["response"]["CheckoutRequestID"],
                              status="pending", raw_payload=payload))
    db.commit()
    return payload["response"]


@router.post("/mpesa-c2b-callback")
def c2b_callback(body: C2BCallback, tenant_id: int = Depends(require_module("payments")),
                 db: Session = Depends(get_db)):
    """
    Daraja C2B confirmation webhook (mock): records the repayment, splits
    principal/interest, updates the loan balance/status and sends a receipt SMS.
    In production this URL is registered with Safaricom's C2B register API.
    """
    loan = (db.query(Loan).options(joinedload(Loan.borrower))
            .filter(Loan.account_number == body.BillRefNumber, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, f"No loan with account {body.BillRefNumber}")
    if loan.status not in ("active", "overdue"):
        raise HTTPException(400, f"Loan status '{loan.status}' does not accept repayments")

    ref = body.TransID or mpesa._mpesa_ref()
    amount = float(body.TransAmount)
    # Flat-interest proportional split
    interest_share = loan.interest_rate / (100.0 + loan.interest_rate)
    repayment = Repayment(
        tenant_id=tenant_id, loan_id=loan.id, amount=amount,
        interest_component=round(amount * interest_share, 2),
        principal_component=round(amount * (1 - interest_share), 2),
        payment_date=datetime.utcnow(), method="mpesa_c2b", mpesa_ref=ref,
    )
    db.add(repayment)
    db.add(PaymentTransaction(tenant_id=tenant_id, type="c2b", loan_id=loan.id, amount=amount,
                              phone=body.MSISDN, mpesa_ref=ref, status="success",
                              raw_payload=body.model_dump()))
    loan.outstanding_balance = max(0, round(float(loan.outstanding_balance) - amount, 2))
    if loan.outstanding_balance <= 0:
        loan.status = "paid"
    sms.sms_payment_receipt(db, tenant_id, loan.borrower, loan, amount, ref)
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Confirmation received successfully",
            "loan_status": loan.status, "outstanding_balance": float(loan.outstanding_balance)}


@router.get("/transactions")
def list_transactions(tenant_id: int = Depends(require_module("payments")),
                      db: Session = Depends(get_db),
                      type: str = "", page: int = 1, page_size: int = 20):
    q = db.query(PaymentTransaction).filter(PaymentTransaction.tenant_id == tenant_id)
    if type:
        q = q.filter(PaymentTransaction.type == type)
    total = q.count()
    rows = (q.order_by(PaymentTransaction.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "page": page, "items": [{
        "id": t.id, "type": t.type, "loan_id": t.loan_id, "amount": float(t.amount),
        "phone": t.phone, "mpesa_ref": t.mpesa_ref, "status": t.status,
        "created_at": t.created_at,
    } for t in rows]}
