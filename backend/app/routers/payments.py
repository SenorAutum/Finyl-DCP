"""
Payments hub — mock Daraja M-Pesa endpoints + transaction viewer.

See app/services/mpesa.py for the annotated placeholders where real Daraja
credentials/HTTP calls plug in.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_module, require_permission, write_audit
from app.models import Loan, PaymentTransaction, PendingApproval, Repayment, User
from app.schemas import (B2CRequest, C2BCallback, StkPushRequest, DisburseRequest,
                         RefundRequest, ReconcileRequest)
from app.services import mpesa, sms
from app.services import rbac as rbac_svc
from app.services.disbursement import execute_disbursement, execute_refund

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


# --------------------------------------------------------------------------- #
# Disbursement (maker-checker) — disbursement_officer
# --------------------------------------------------------------------------- #
@router.post("/disburse")
def disburse_loan(body: DisburseRequest,
                  tenant_id: int = Depends(require_module("payments")),
                  db: Session = Depends(get_db),
                  user: User = Depends(require_permission("disburse.execute")),
                  request: Request = None):
    """Disburse an approved loan. Above the configured maker-checker threshold
    the request is parked as a PendingApproval for a second authorized user."""
    loan = (db.query(Loan).options(joinedload(Loan.borrower), joinedload(Loan.product))
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    if loan.status != "approved":
        raise HTTPException(400, "Loan must be in 'approved' status to disburse")
    amount = float(loan.principal)
    if rbac_svc.requires_maker_checker(db, tenant_id, "disbursement", amount):
        pending = PendingApproval(
            tenant_id=tenant_id, action_type="disbursement", loan_id=loan.id,
            amount=amount, phone=loan.borrower.phone, reason=body.reason,
            status="pending_approval", maker_user_id=user.id, maker_at=datetime.utcnow(),
            details={"account_number": loan.account_number},
        )
        db.add(pending)
        write_audit(db, tenant_id=tenant_id, user=user, action="disburse.request",
                    entity_type="loan", entity_id=loan.id,
                    details={"amount": amount, "maker_checker": True}, request=request)
        db.commit()
        return {"status": "pending_approval", "pending_id": pending.id,
                "message": "Disbursement exceeds threshold — awaiting a second approver."}
    result = execute_disbursement(db, tenant_id, loan, user.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="disburse.execute",
                entity_type="loan", entity_id=loan.id,
                details={"amount": amount, **result}, request=request)
    db.commit()
    return {"status": "disbursed", **result}


# --------------------------------------------------------------------------- #
# Refund (maker-checker) — reconciliation_officer
# --------------------------------------------------------------------------- #
@router.post("/refund")
def refund_payment(body: RefundRequest,
                   tenant_id: int = Depends(require_module("payments")),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_permission("refund.execute")),
                   request: Request = None):
    """Refund an over/mis-payment. Above the maker-checker threshold it is
    parked for a second approver."""
    amount = float(body.amount)
    if amount <= 0:
        raise HTTPException(400, "Refund amount must be positive")
    if rbac_svc.requires_maker_checker(db, tenant_id, "refund", amount):
        pending = PendingApproval(
            tenant_id=tenant_id, action_type="refund", loan_id=body.loan_id,
            amount=amount, phone=body.phone, reason=body.reason,
            status="pending_approval", maker_user_id=user.id, maker_at=datetime.utcnow(),
            details={},
        )
        db.add(pending)
        write_audit(db, tenant_id=tenant_id, user=user, action="refund.request",
                    entity_type="loan", entity_id=body.loan_id,
                    details={"amount": amount, "maker_checker": True}, request=request)
        db.commit()
        return {"status": "pending_approval", "pending_id": pending.id,
                "message": "Refund exceeds threshold — awaiting a second approver."}
    result = execute_refund(db, tenant_id, amount, body.phone, body.loan_id, user.id, body.reason)
    write_audit(db, tenant_id=tenant_id, user=user, action="refund.execute",
                entity_type="loan", entity_id=body.loan_id,
                details={"amount": amount, **result}, request=request)
    db.commit()
    return {"status": "refunded", **result}


# --------------------------------------------------------------------------- #
# Reconciliation — reconciliation_officer records a matched repayment
# --------------------------------------------------------------------------- #
@router.post("/reconcile")
def reconcile_payment(body: ReconcileRequest,
                      tenant_id: int = Depends(require_module("payments")),
                      db: Session = Depends(get_db),
                      user: User = Depends(require_permission("reconcile.execute")),
                      request: Request = None):
    """Manually reconcile a received payment against a loan."""
    loan = (db.query(Loan).options(joinedload(Loan.borrower))
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    amount = float(body.amount)
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    interest_share = loan.interest_rate / (100.0 + loan.interest_rate)
    rep = Repayment(
        tenant_id=tenant_id, loan_id=loan.id, amount=amount,
        interest_component=round(amount * interest_share, 2),
        principal_component=round(amount * (1 - interest_share), 2),
        payment_date=datetime.utcnow(), method="reconciliation",
        mpesa_ref=body.mpesa_ref,
    )
    db.add(rep)
    loan.outstanding_balance = max(0, round(float(loan.outstanding_balance or 0) - amount, 2))
    if loan.outstanding_balance <= 0 and loan.status in ("active", "overdue"):
        loan.status = "paid"
    write_audit(db, tenant_id=tenant_id, user=user, action="reconcile.execute",
                entity_type="loan", entity_id=loan.id,
                details={"amount": amount, "mpesa_ref": body.mpesa_ref}, request=request)
    db.commit()
    return {"status": "reconciled", "outstanding_balance": float(loan.outstanding_balance),
            "loan_status": loan.status}


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
