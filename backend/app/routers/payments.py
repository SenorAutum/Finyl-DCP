"""
Payments hub — Daraja M-Pesa endpoints + async callback webhooks + transaction viewer.

Two classes of endpoint live here:

  * Authenticated operator actions (/disburse, /refund, /reconcile, /stk-push,
    /reconcile-disbursements, /transactions) — JWT + RBAC permission + module gate.
    Every payout flows through services/disbursement.py so the RBAC / maker-checker
    ladder cannot be bypassed (MPESA-02).

  * Unauthenticated Daraja callback webhooks (/mpesa/{token}/...) — Safaricom sends
    no JWT, so these are protected by (a) a hard-to-guess secret path token
    (MPESA_CALLBACK_TOKEN) validated in constant time, and (b) a documented nginx
    Safaricom source-IP allowlist (defence-in-depth). They are idempotent and never
    trust client-supplied amounts blindly (MPESA-01 / MPESA-03 / MPESA-04).

See app/services/mpesa.py for the annotated Daraja client (credential-gated).
"""
import hmac
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_module, require_permission, write_audit
from app.models import Loan, PaymentTransaction, PendingApproval, Repayment, User
from app.schemas import (C2BCallback, StkPushRequest, DisburseRequest,
                         RefundRequest, ReconcileRequest)
from app.services import mpesa, sms
from app.services import rbac as rbac_svc
from app.services.disbursement import (execute_disbursement, execute_refund,
                                       apply_b2c_result, mark_b2c_timeout)

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
    the request is parked as a PendingApproval for a second authorized user.

    The payout is asynchronous: the loan moves approved → processing here and is
    only activated when Daraja's b2c-result callback confirms success (MPESA-01)."""
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
    return result


# --------------------------------------------------------------------------- #
# Refund (mandatory maker-checker) — reconciliation_officer  [MPESA-05]
# --------------------------------------------------------------------------- #
@router.post("/refund")
def refund_payment(body: RefundRequest,
                   tenant_id: int = Depends(require_module("payments")),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_permission("refund.execute")),
                   request: Request = None):
    """Refund a recorded overpayment on a loan.

    MPESA-05 hardening:
      * The refund is tied to a loan; the destination phone is the borrower's
        registered number (a client-supplied phone is only accepted if it matches).
      * The maximum refundable amount is derived server-side from the loan's
        recorded overpayment (total repaid − total due); a larger amount is rejected.
      * ALL refunds require maker-checker regardless of threshold — the request is
        always parked for a second approver, carrying the validated phone/amount.
    """
    loan = (db.query(Loan).options(joinedload(Loan.borrower))
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, "Loan not found")

    repayments = (db.query(Repayment)
                  .filter(Repayment.loan_id == loan.id, Repayment.tenant_id == tenant_id).all())
    total_paid = sum(float(r.amount) for r in repayments)
    overpayment = round(total_paid - float(loan.total_due), 2)
    if overpayment <= 0:
        raise HTTPException(400, "No recorded overpayment on this loan to refund")

    # Destination phone: borrower's registered number. A supplied phone must match it.
    borrower_msisdn = mpesa.normalise_msisdn(loan.borrower.phone)
    if body.phone and mpesa.normalise_msisdn(body.phone) != borrower_msisdn:
        raise HTTPException(400, "Refund phone does not match the borrower's registered number")
    phone = loan.borrower.phone

    amount = float(body.amount) if body.amount is not None else overpayment
    if amount <= 0:
        raise HTTPException(400, "Refund amount must be positive")
    if amount > overpayment:
        raise HTTPException(400,
                            f"Refund amount {amount} exceeds recorded overpayment {overpayment}")

    pending = PendingApproval(
        tenant_id=tenant_id, action_type="refund", loan_id=loan.id,
        amount=amount, phone=phone, reason=body.reason,
        status="pending_approval", maker_user_id=user.id, maker_at=datetime.utcnow(),
        details={"overpayment": overpayment, "derived_phone": phone},
    )
    db.add(pending)
    write_audit(db, tenant_id=tenant_id, user=user, action="refund.request",
                entity_type="loan", entity_id=loan.id,
                details={"amount": amount, "overpayment": overpayment,
                         "maker_checker": True}, request=request)
    db.commit()
    return {"status": "pending_approval", "pending_id": pending.id,
            "amount": amount, "phone": phone,
            "message": "Refund requires a second approver (mandatory maker-checker)."}


# --------------------------------------------------------------------------- #
# Reconciliation — reconciliation_officer records a matched repayment
# --------------------------------------------------------------------------- #
@router.post("/reconcile")
def reconcile_payment(body: ReconcileRequest,
                      tenant_id: int = Depends(require_module("payments")),
                      db: Session = Depends(get_db),
                      user: User = Depends(require_permission("reconcile.execute")),
                      request: Request = None):
    """Manually reconcile a received payment against a loan.

    MPESA-03 idempotency: if a repayment with the same mpesa_ref already exists for
    this tenant, this is a no-op (prevents double-posting a manual reconciliation)."""
    loan = (db.query(Loan).options(joinedload(Loan.borrower))
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    amount = float(body.amount)
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    if body.mpesa_ref:
        existing = (db.query(Repayment)
                    .filter(Repayment.tenant_id == tenant_id,
                            Repayment.mpesa_ref == body.mpesa_ref).first())
        if existing:
            return {"status": "duplicate_ignored", "mpesa_ref": body.mpesa_ref,
                    "repayment_id": existing.id,
                    "outstanding_balance": float(loan.outstanding_balance or 0),
                    "loan_status": loan.status}

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


# --------------------------------------------------------------------------- #
# STK push (collections prompt) — gated by collections.stk_push  [MPESA-08]
# --------------------------------------------------------------------------- #
@router.post("/stk-push")
def stk_push(body: StkPushRequest,
             tenant_id: int = Depends(require_module("payments")),
             db: Session = Depends(get_db),
             user: User = Depends(require_permission("collections.stk_push")),
             request: Request = None):
    """Send an STK-push collections prompt to a borrower's phone."""
    loan = (db.query(Loan).options(joinedload(Loan.borrower))
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    try:
        payload = mpesa.stk_push(loan.borrower.phone, body.amount, loan.account_number)
    except mpesa.DarajaNotConfigured as exc:
        raise HTTPException(422, f"M-Pesa (Daraja) credentials required: {exc}")
    except Exception as exc:
        raise HTTPException(502, f"Daraja STK push failed: {exc}")
    db.add(PaymentTransaction(tenant_id=tenant_id, type="stk_push", loan_id=loan.id,
                              amount=body.amount, phone=loan.borrower.phone,
                              mpesa_ref=payload["response"]["CheckoutRequestID"],
                              status="pending", raw_payload=payload))
    write_audit(db, tenant_id=tenant_id, user=user, action="stk_push.execute",
                entity_type="loan", entity_id=loan.id,
                details={"amount": float(body.amount)}, request=request)
    db.commit()
    return payload["response"]


# --------------------------------------------------------------------------- #
# Reconcile stuck disbursements — sweep processing/timed_out B2C payouts
# --------------------------------------------------------------------------- #
@router.post("/reconcile-disbursements")
def reconcile_disbursements(tenant_id: int = Depends(require_module("payments")),
                            db: Session = Depends(get_db),
                            user: User = Depends(require_permission("reconcile.execute")),
                            older_than_minutes: int = 0, simulate: bool = False,
                            request: Request = None):
    """Find B2C payouts still in `processing`/`timed_out` (the async result never
    arrived) and report them. In the credential-gated MOCK, `simulate=true` drives
    each pending payout to a successful result via the same callback code path so
    the E2E state machine completes without live Daraja. With real credentials this
    is a read-only report (operators resolve via Daraja Transaction Status API)."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(minutes=max(0, older_than_minutes))
    q = (db.query(PaymentTransaction)
         .filter(PaymentTransaction.tenant_id == tenant_id,
                 PaymentTransaction.type == "b2c",
                 PaymentTransaction.status.in_(("processing", "timed_out")),
                 PaymentTransaction.created_at <= cutoff)
         .order_by(PaymentTransaction.created_at.asc()))
    stuck = q.all()
    resolved = []
    if simulate and not mpesa.is_configured():
        for txn in stuck:
            conv = txn.mpesa_ref
            orig = (txn.raw_payload or {}).get("result", {}).get("OriginatorConversationID")
            result = mpesa.simulate_b2c_result(conv, orig, success=True,
                                               amount=float(txn.amount))
            r = result["Result"]
            receipt = r.get("TransactionID")
            outcome = apply_b2c_result(db, tenant_id, txn, r.get("ResultCode"), receipt, raw=result)
            resolved.append({"txn_id": txn.id, **outcome})
        write_audit(db, tenant_id=tenant_id, user=user, action="reconcile.disbursements",
                    details={"resolved": len(resolved), "simulated": True}, request=request)
        db.commit()
    return {"stuck_count": len(stuck),
            "stuck": [{"txn_id": t.id, "loan_id": t.loan_id, "status": t.status,
                       "conversation_id": t.mpesa_ref, "amount": float(t.amount),
                       "created_at": t.created_at} for t in stuck],
            "resolved": resolved}


# --------------------------------------------------------------------------- #
# UNAUTHENTICATED Daraja callback webhooks  [MPESA-01 / MPESA-03 / MPESA-04]
#   Protected by a secret path token (constant-time compare) + nginx source-IP
#   allowlist. Safaricom sends no JWT, so these carry NO require_module/permission.
#   They never raise on unmatched data (that would trigger Safaricom retries) and
#   always return a Daraja-style acknowledgement.
# --------------------------------------------------------------------------- #
def _check_callback_token(token: str):
    """Constant-time validation of the secret callback path token. A mismatch
    returns 404 (not 401/403) so the endpoint is indistinguishable from a
    non-existent path to anyone probing without the token."""
    if not hmac.compare_digest(str(token), str(settings.MPESA_CALLBACK_TOKEN)):
        raise HTTPException(404, "Not Found")


async def _read_payload(request: Request) -> dict:
    """Parse a Daraja callback body defensively (JSON or form-encoded)."""
    try:
        return await request.json()
    except Exception:
        try:
            return dict(await request.form())
        except Exception:
            return {}


def _find_b2c_txn(db: Session, conversation_id, originator_id=None):
    """Locate a processing B2C transaction by ConversationID (stored as mpesa_ref),
    falling back to OriginatorConversationID recorded in the raw payload."""
    txn = None
    if conversation_id:
        txn = (db.query(PaymentTransaction)
               .filter(PaymentTransaction.type == "b2c",
                       PaymentTransaction.mpesa_ref == str(conversation_id)).first())
    if txn is None and originator_id:
        candidates = (db.query(PaymentTransaction)
                      .filter(PaymentTransaction.type == "b2c",
                              PaymentTransaction.status.in_(("processing", "timed_out"))).all())
        for c in candidates:
            res = (c.raw_payload or {}).get("result", {})
            if res.get("OriginatorConversationID") == originator_id:
                txn = c
                break
    return txn


@router.post("/mpesa/{token}/b2c-result")
async def b2c_result_callback(token: str, request: Request, db: Session = Depends(get_db)):
    """Daraja B2C ResultURL — the definitive outcome of a disbursement (MPESA-01).
    Idempotently applies the result: success activates the loan, failure reverts it
    to `approved` for retry."""
    _check_callback_token(token)
    body = await _read_payload(request)
    result = body.get("Result", {}) if isinstance(body, dict) else {}
    conv = result.get("ConversationID")
    orig = result.get("OriginatorConversationID")
    result_code = result.get("ResultCode")
    receipt = result.get("TransactionID")
    for p in (result.get("ResultParameters", {}) or {}).get("ResultParameter", []) or []:
        if p.get("Key") == "TransactionReceipt" and p.get("Value"):
            receipt = p.get("Value")
    txn = _find_b2c_txn(db, conv, orig)
    if txn is not None:
        apply_b2c_result(db, txn.tenant_id, txn, result_code, receipt, raw=body)
        db.commit()
    return {"ResultCode": 0, "ResultDesc": "Result received"}


@router.post("/mpesa/{token}/b2c-timeout")
async def b2c_timeout_callback(token: str, request: Request, db: Session = Depends(get_db)):
    """Daraja B2C QueueTimeOutURL — payout outcome unknown. Flags the transaction
    timed_out for the reconcile sweep; does NOT revert the loan (avoids a double
    payout should it later settle)."""
    _check_callback_token(token)
    body = await _read_payload(request)
    result = body.get("Result", {}) if isinstance(body, dict) else {}
    conv = result.get("ConversationID")
    orig = result.get("OriginatorConversationID")
    txn = _find_b2c_txn(db, conv, orig)
    if txn is not None:
        mark_b2c_timeout(db, txn, raw=body)
        db.commit()
    return {"ResultCode": 0, "ResultDesc": "Timeout received"}


@router.post("/mpesa/{token}/stk-callback")
async def stk_callback(token: str, request: Request, db: Session = Depends(get_db)):
    """Daraja STK push CallBackURL — result of a collections prompt. On success
    records the repayment (idempotent on the M-Pesa receipt) and reduces the
    balance; on failure marks the transaction failed."""
    _check_callback_token(token)
    body = await _read_payload(request)
    cb = ((body.get("Body", {}) or {}).get("stkCallback", {}) or {}) if isinstance(body, dict) else {}
    checkout_id = cb.get("CheckoutRequestID")
    result_code = cb.get("ResultCode")
    if not checkout_id:
        return {"ResultCode": 0, "ResultDesc": "Ignored"}

    txn = (db.query(PaymentTransaction)
           .filter(PaymentTransaction.type == "stk_push",
                   PaymentTransaction.mpesa_ref == str(checkout_id)).first())
    if txn is None:
        return {"ResultCode": 0, "ResultDesc": "Unknown checkout"}
    if txn.status not in ("pending", "processing"):
        return {"ResultCode": 0, "ResultDesc": "Already processed"}

    tenant_id = txn.tenant_id
    if str(result_code) != "0":
        txn.status = "failed"
        txn.raw_payload = {**(txn.raw_payload or {}), "result_callback": body}
        db.commit()
        return {"ResultCode": 0, "ResultDesc": "Result received"}

    # Success — pull amount/receipt/phone from the callback metadata.
    meta = {i.get("Name"): i.get("Value")
            for i in (cb.get("CallbackMetadata", {}) or {}).get("Item", []) or []}
    receipt = meta.get("MpesaReceiptNumber") or mpesa._mpesa_ref()
    amount = float(meta.get("Amount") or txn.amount or 0)

    # Idempotency: if this receipt already produced a repayment, no-op.
    existing = (db.query(Repayment)
                .filter(Repayment.tenant_id == tenant_id, Repayment.mpesa_ref == str(receipt))
                .first())
    txn.status = "success"
    txn.mpesa_ref = str(receipt)
    txn.raw_payload = {**(txn.raw_payload or {}), "result_callback": body}
    loan = db.query(Loan).options(joinedload(Loan.borrower)).filter(
        Loan.id == txn.loan_id, Loan.tenant_id == tenant_id).first()
    if existing is None and loan is not None:
        interest_share = loan.interest_rate / (100.0 + loan.interest_rate)
        db.add(Repayment(tenant_id=tenant_id, loan_id=loan.id, amount=amount,
                         interest_component=round(amount * interest_share, 2),
                         principal_component=round(amount * (1 - interest_share), 2),
                         payment_date=datetime.utcnow(), method="stk_push",
                         mpesa_ref=str(receipt)))
        loan.outstanding_balance = max(0, round(float(loan.outstanding_balance or 0) - amount, 2))
        if loan.outstanding_balance <= 0 and loan.status in ("active", "overdue"):
            loan.status = "paid"
        try:
            sms.sms_payment_receipt(db, tenant_id, loan.borrower, loan, amount, str(receipt))
        except Exception:
            pass
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Result received"}


@router.post("/mpesa/{token}/c2b-callback")
async def c2b_callback(token: str, request: Request, db: Session = Depends(get_db)):
    """Daraja C2B confirmation webhook (MPESA-04).

    Unauthenticated (token + IP allowlist). Derives the tenant + loan from
    BillRefNumber (never trusts a client JWT), is idempotent on TransID, and
    cross-checks TransAmount against the outstanding balance: a payment larger than
    the outstanding is recorded but flagged for review and NOT auto-marked paid."""
    _check_callback_token(token)
    body = await _read_payload(request)
    try:
        cb = C2BCallback(**body)
    except Exception:
        # Malformed — acknowledge (avoid retries) but record nothing.
        return {"ResultCode": 0, "ResultDesc": "Ignored (unparseable)"}

    # Derive tenant + loan from the account number in BillRefNumber.
    loan = (db.query(Loan).options(joinedload(Loan.borrower))
            .filter(Loan.account_number == cb.BillRefNumber).first())
    if not loan:
        return {"ResultCode": 0, "ResultDesc": f"No loan for account {cb.BillRefNumber}"}
    tenant_id = loan.tenant_id
    ref = cb.TransID or mpesa._mpesa_ref()
    amount = float(cb.TransAmount)

    # Idempotency on TransID (MPESA-03): duplicate delivery is a no-op.
    existing = (db.query(Repayment)
                .filter(Repayment.tenant_id == tenant_id, Repayment.mpesa_ref == str(ref))
                .first())
    if existing is not None:
        return {"ResultCode": 0, "ResultDesc": "Duplicate ignored",
                "loan_status": loan.status}

    # Loan not in a collectible state — record the transaction for review only.
    if loan.status not in ("active", "overdue"):
        db.add(PaymentTransaction(tenant_id=tenant_id, type="c2b", loan_id=loan.id,
                                  amount=amount, phone=cb.MSISDN, mpesa_ref=ref,
                                  status="success",
                                  raw_payload={**body, "review": True,
                                               "review_reason": f"loan_status_{loan.status}"}))
        db.commit()
        return {"ResultCode": 0, "ResultDesc": "Recorded for review (loan not collectible)"}

    outstanding = float(loan.outstanding_balance or 0)
    overpayment = amount > outstanding
    interest_share = loan.interest_rate / (100.0 + loan.interest_rate)
    db.add(Repayment(tenant_id=tenant_id, loan_id=loan.id, amount=amount,
                     interest_component=round(amount * interest_share, 2),
                     principal_component=round(amount * (1 - interest_share), 2),
                     payment_date=datetime.utcnow(), method="mpesa_c2b", mpesa_ref=ref))
    txn_payload = {**body}
    if overpayment:
        # MPESA-04: record but flag for manual review; do NOT auto-mark paid.
        txn_payload.update({"review": True, "review_reason": "overpayment",
                            "outstanding_at_receipt": outstanding})
        loan.outstanding_balance = 0
    else:
        loan.outstanding_balance = max(0, round(outstanding - amount, 2))
        if loan.outstanding_balance <= 0 and loan.status in ("active", "overdue"):
            loan.status = "paid"
    db.add(PaymentTransaction(tenant_id=tenant_id, type="c2b", loan_id=loan.id, amount=amount,
                              phone=cb.MSISDN, mpesa_ref=ref, status="success",
                              raw_payload=txn_payload))
    try:
        sms.sms_payment_receipt(db, tenant_id, loan.borrower, loan, amount, ref)
    except Exception:
        pass
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Confirmation received successfully",
            "loan_status": loan.status, "flagged_for_review": overpayment,
            "outstanding_balance": float(loan.outstanding_balance)}


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
