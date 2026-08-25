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
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_module, require_permission, write_audit
from app.core.money import split_interest_principal, reduce_balance, money
from app.core.obs import log_money_event
from app.models import Loan, PaymentTransaction, PendingApproval, Repayment, User, SuspenseEntry
from app.schemas import (C2BCallback, StkPushRequest, DisburseRequest,
                         RefundRequest, ReconcileRequest,
                         SuspenseAllocateIn, SuspenseRefundIn)
from app.services import mpesa, sms
from app.services import rbac as rbac_svc
from app.services import webhook_security as ws
from app.services.disbursement import (execute_disbursement, execute_refund,
                                       apply_b2c_result, mark_b2c_timeout)

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

logger = logging.getLogger("finyl.payments")


def _record_suspense(db: Session, *, tenant_id: int, reason: str, amount, ref,
                     phone=None, loan_id=None, source="c2b", raw_payload=None):
    """Additively record a suspense entry, idempotent on (tenant_id, mpesa_ref).

    Returns the entry (existing or new). Does NOT commit — the caller owns the
    transaction so the suspense row is written atomically with the payment record.
    """
    if ref:
        existing = (db.query(SuspenseEntry)
                    .filter(SuspenseEntry.tenant_id == tenant_id,
                            SuspenseEntry.mpesa_ref == str(ref)).first())
        if existing is not None:
            return existing
    entry = SuspenseEntry(tenant_id=tenant_id, source=source,
                          mpesa_ref=(str(ref) if ref else None), phone=phone,
                          amount=money(amount), reason=reason, status="open",
                          matched_loan_id=loan_id, raw_payload=raw_payload or {})
    db.add(entry)
    return entry


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
    # Concurrency: lock the loan row so concurrent repayment callbacks serialize
    # (no double-credit). FOR UPDATE can't be applied with the outer-joined
    # joinedload; borrower is not needed here so we lock the loan row alone.
    loan = (db.query(Loan)
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id)
            .with_for_update().first())
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

    # MPESA-07: Decimal money math for the interest/principal split + balance.
    interest, principal = split_interest_principal(amount, loan.interest_rate)
    rep = Repayment(
        tenant_id=tenant_id, loan_id=loan.id, amount=money(amount),
        interest_component=interest,
        principal_component=principal,
        payment_date=datetime.utcnow(), method="reconciliation",
        mpesa_ref=body.mpesa_ref,
    )
    db.add(rep)
    loan.outstanding_balance = reduce_balance(loan.outstanding_balance or 0, amount)
    if loan.outstanding_balance <= 0 and loan.status in ("active", "overdue"):
        loan.status = "paid"
    write_audit(db, tenant_id=tenant_id, user=user, action="reconcile.execute",
                entity_type="loan", entity_id=loan.id,
                details={"amount": amount, "mpesa_ref": body.mpesa_ref}, request=request)
    log_money_event("reconcile", tenant_id=tenant_id, user_id=user.id, loan_id=loan.id,
                    amount=money(amount), ref=body.mpesa_ref,
                    detail=f"outstanding={loan.outstanding_balance}")
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
    creds = mpesa.resolve_creds(db, tenant_id)
    try:
        payload = mpesa.stk_push(loan.borrower.phone, body.amount, loan.account_number, creds=creds)
    except mpesa.DarajaNotConfigured:
        # SEC-01: do not leak provider/config internals to the caller.
        raise HTTPException(422, "M-Pesa (Daraja) credentials are not configured")
    except Exception as exc:
        # SEC-01: log the detail server-side; return a generic message.
        logger.error("stk_push_failed loan_id=%s tenant_id=%s: %s", loan.id, tenant_id, exc)
        raise HTTPException(502, "STK push could not be completed. Please try again later.")
    db.add(PaymentTransaction(tenant_id=tenant_id, type="stk_push", loan_id=loan.id,
                              amount=body.amount, phone=loan.borrower.phone,
                              mpesa_ref=payload["response"]["CheckoutRequestID"],
                              status="pending", raw_payload=payload))
    write_audit(db, tenant_id=tenant_id, user=user, action="stk_push.execute",
                entity_type="loan", entity_id=loan.id,
                details={"amount": float(body.amount)}, request=request)
    log_money_event("stk_push", tenant_id=tenant_id, user_id=user.id, loan_id=loan.id,
                    amount=money(body.amount), phone=loan.borrower.phone,
                    ref=payload["response"].get("CheckoutRequestID"))
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
    if simulate and not mpesa.is_configured(mpesa.resolve_creds(db, tenant_id)):
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


# --------------------------------------------------------------------------- #
# Durable ingestion wrapper (MPESA durability / dead-letter)
#   Each webhook now: (1) validates the secret path token, (2) applies the
#   Safaricom perimeter IP allowlist (off/log/enforce), (3) persists the raw
#   event as `received` BEFORE processing, (4) runs the SAME idempotent
#   processing as before inside try/except — success -> processed, exception ->
#   failed + scheduled retry (-> dead + alert after max attempts), and (5)
#   ALWAYS returns Safaricom's expected acknowledgement (HTTP 200) even on an
#   internal failure, so Safaricom does not retry while our durable queue owns
#   the retry. Existing money-movement logic + idempotency guards are unchanged;
#   they are merely extracted into reusable processor functions so the retry
#   worker can re-invoke them.
# --------------------------------------------------------------------------- #
def _ingest_and_process(db, endpoint, body, processor, default_ack):
    """Persist the event, run the processor, mark the outcome, ALWAYS return an
    acknowledgement. On success returns the processor's ack; on any failure
    returns ``default_ack`` (a standard Daraja ResultCode-0 body)."""
    tenant_id, shortcode = ws.resolve_tenant_for_webhook(db, endpoint, body)
    event = ws.record_event(db, endpoint, body, tenant_id=tenant_id, shortcode=shortcode)
    try:
        ack, resolved_tenant = processor(db, body)
        ws.mark_processed(db, event, tenant_id=resolved_tenant or tenant_id)
        return ack
    except ws.WebhookUnresolved as exc:
        db.rollback()
        ws.mark_failed(db, event, f"unresolved: {exc}")
        return default_ack
    except Exception as exc:  # noqa: BLE001 — never leak / never 500 to Safaricom
        db.rollback()
        logger.error("webhook_processing_failed endpoint=%s: %s", endpoint, exc)
        ws.mark_failed(db, event, repr(exc))
        return default_ack


# --- Extracted, idempotent processors (called by the webhook + the retry worker) --
def _process_b2c_result(db, body) -> tuple[dict, int | None]:
    result = body.get("Result", {}) if isinstance(body, dict) else {}
    conv = result.get("ConversationID")
    orig = result.get("OriginatorConversationID")
    result_code = result.get("ResultCode")
    receipt = result.get("TransactionID")
    for p in (result.get("ResultParameters", {}) or {}).get("ResultParameter", []) or []:
        if p.get("Key") == "TransactionReceipt" and p.get("Value"):
            receipt = p.get("Value")
    txn = _find_b2c_txn(db, conv, orig)
    if txn is None:
        raise ws.WebhookUnresolved(f"no B2C transaction for conv={conv} orig={orig}")
    apply_b2c_result(db, txn.tenant_id, txn, result_code, receipt, raw=body)
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Result received"}, txn.tenant_id


def _process_b2c_timeout(db, body) -> tuple[dict, int | None]:
    result = body.get("Result", {}) if isinstance(body, dict) else {}
    conv = result.get("ConversationID")
    orig = result.get("OriginatorConversationID")
    txn = _find_b2c_txn(db, conv, orig)
    if txn is None:
        raise ws.WebhookUnresolved(f"no B2C transaction for conv={conv} orig={orig}")
    mark_b2c_timeout(db, txn, raw=body)
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Timeout received"}, txn.tenant_id


def _process_stk_callback(db, body) -> tuple[dict, int | None]:
    cb = ((body.get("Body", {}) or {}).get("stkCallback", {}) or {}) if isinstance(body, dict) else {}
    checkout_id = cb.get("CheckoutRequestID")
    result_code = cb.get("ResultCode")
    if not checkout_id:
        # Malformed / not routable to a checkout — acknowledge, nothing to do.
        return {"ResultCode": 0, "ResultDesc": "Ignored"}, None

    txn = (db.query(PaymentTransaction)
           .filter(PaymentTransaction.type == "stk_push",
                   PaymentTransaction.mpesa_ref == str(checkout_id)).first())
    if txn is None:
        raise ws.WebhookUnresolved(f"unknown STK checkout {checkout_id}")
    if txn.status not in ("pending", "processing"):
        return {"ResultCode": 0, "ResultDesc": "Already processed"}, txn.tenant_id

    tenant_id = txn.tenant_id
    if str(result_code) != "0":
        txn.status = "failed"
        txn.raw_payload = {**(txn.raw_payload or {}), "result_callback": body}
        db.commit()
        return {"ResultCode": 0, "ResultDesc": "Result received"}, tenant_id

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
    # Concurrency: lock the loan row so concurrent repayment callbacks serialize
    # (no double-credit). borrower lazy-loads on access below.
    loan = db.query(Loan).filter(
        Loan.id == txn.loan_id, Loan.tenant_id == tenant_id).with_for_update().first()
    if existing is None and loan is not None:
        # MPESA-07: Decimal money math.
        interest, principal = split_interest_principal(amount, loan.interest_rate)
        db.add(Repayment(tenant_id=tenant_id, loan_id=loan.id, amount=money(amount),
                         interest_component=interest,
                         principal_component=principal,
                         payment_date=datetime.utcnow(), method="stk_push",
                         mpesa_ref=str(receipt)))
        loan.outstanding_balance = reduce_balance(loan.outstanding_balance or 0, amount)
        if loan.outstanding_balance <= 0 and loan.status in ("active", "overdue"):
            loan.status = "paid"
        log_money_event("stk_callback", tenant_id=tenant_id, loan_id=loan.id,
                        amount=money(amount), ref=str(receipt),
                        detail=f"outstanding={loan.outstanding_balance}")
        try:
            sms.sms_payment_receipt(db, tenant_id, loan.borrower, loan, amount, str(receipt))
        except Exception:
            pass
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Result received"}, tenant_id


def _process_c2b_callback(db, body) -> tuple[dict, int | None]:
    try:
        cb = C2BCallback(**body)
    except Exception:
        # Malformed — acknowledge (avoid retries) but record nothing.
        return {"ResultCode": 0, "ResultDesc": "Ignored (unparseable)"}, None

    # Derive tenant + loan from the account number in BillRefNumber.
    # Concurrency: lock the loan row so concurrent repayment callbacks serialize
    # (no double-credit). borrower lazy-loads on access below.
    loan = (db.query(Loan)
            .filter(Loan.account_number == cb.BillRefNumber)
            .with_for_update().first())
    if not loan:
        # Cannot route the money to a loan/tenant — surface it (DLQ + alert)
        # rather than silently dropping funds (MPESA multi-paybill resolution).
        raise ws.WebhookUnresolved(f"no loan for account {cb.BillRefNumber}")
    tenant_id = loan.tenant_id
    ref = cb.TransID or mpesa._mpesa_ref()
    amount = float(cb.TransAmount)

    # Idempotency on TransID (MPESA-03): duplicate delivery is a no-op.
    existing = (db.query(Repayment)
                .filter(Repayment.tenant_id == tenant_id, Repayment.mpesa_ref == str(ref))
                .first())
    if existing is not None:
        return {"ResultCode": 0, "ResultDesc": "Duplicate ignored",
                "loan_status": loan.status}, tenant_id

    # Loan not in a collectible state — record the transaction for review only.
    if loan.status not in ("active", "overdue"):
        db.add(PaymentTransaction(tenant_id=tenant_id, type="c2b", loan_id=loan.id,
                                  amount=amount, phone=cb.MSISDN, mpesa_ref=ref,
                                  status="success",
                                  raw_payload={**body, "review": True,
                                               "review_reason": f"loan_status_{loan.status}"}))
        # Additive: park the funds in suspense (tenant known, loan not collectible).
        _record_suspense(db, tenant_id=tenant_id, reason="closed_loan", amount=amount,
                         ref=ref, phone=cb.MSISDN, loan_id=loan.id, source="c2b",
                         raw_payload={"review_reason": f"loan_status_{loan.status}",
                                      "account_number": cb.BillRefNumber})
        db.commit()
        return {"ResultCode": 0, "ResultDesc": "Recorded for review (loan not collectible)"}, tenant_id

    outstanding = float(loan.outstanding_balance or 0)
    overpayment = amount > outstanding
    # MPESA-07: Decimal money math.
    interest, principal = split_interest_principal(amount, loan.interest_rate)
    db.add(Repayment(tenant_id=tenant_id, loan_id=loan.id, amount=money(amount),
                     interest_component=interest,
                     principal_component=principal,
                     payment_date=datetime.utcnow(), method="mpesa_c2b", mpesa_ref=ref))
    txn_payload = {**body}
    if overpayment:
        # MPESA-04: record but flag for manual review; do NOT auto-mark paid.
        txn_payload.update({"review": True, "review_reason": "overpayment",
                            "outstanding_at_receipt": outstanding})
        loan.outstanding_balance = 0
        # Additive: park the excess (amount over outstanding) in suspense.
        excess = round(amount - outstanding, 2)
        if excess > 0:
            _record_suspense(db, tenant_id=tenant_id, reason="overpayment", amount=excess,
                             ref=ref, phone=cb.MSISDN, loan_id=loan.id, source="c2b",
                             raw_payload={"outstanding_at_receipt": outstanding,
                                          "amount_received": amount,
                                          "account_number": cb.BillRefNumber})
    else:
        loan.outstanding_balance = reduce_balance(outstanding, amount)
        if loan.outstanding_balance <= 0 and loan.status in ("active", "overdue"):
            loan.status = "paid"
    log_money_event("c2b_callback", tenant_id=tenant_id, loan_id=loan.id,
                    amount=money(amount), phone=cb.MSISDN, ref=str(ref),
                    detail=f"overpayment={overpayment} outstanding={loan.outstanding_balance}")
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
            "outstanding_balance": float(loan.outstanding_balance)}, tenant_id


# Retry dispatch: endpoint label -> processor. Used by the scheduler worker to
# reprocess durable `failed` events idempotently.
_PROCESSORS = {
    ws.ENDPOINT_B2C_RESULT: _process_b2c_result,
    ws.ENDPOINT_B2C_TIMEOUT: _process_b2c_timeout,
    ws.ENDPOINT_STK_CALLBACK: _process_stk_callback,
    ws.ENDPOINT_C2B_CALLBACK: _process_c2b_callback,
}


def reprocess_event(db, event) -> bool:
    """Re-run the idempotent processor for a durable webhook event (retry worker).
    Returns True on success (event -> processed), False otherwise (event -> failed
    with the next backoff, or -> dead + alert once max attempts is reached)."""
    processor = _PROCESSORS.get(event.endpoint)
    body = event.raw_payload or {}
    if processor is None:
        ws.mark_failed(db, event, f"no processor for endpoint {event.endpoint}")
        return False
    try:
        _ack, resolved_tenant = processor(db, body)
        ws.mark_processed(db, event, tenant_id=resolved_tenant)
        return True
    except ws.WebhookUnresolved as exc:
        db.rollback()
        ws.mark_failed(db, event, f"unresolved: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        ws.mark_failed(db, event, repr(exc))
        return False


@router.post("/mpesa/{token}/b2c-result")
async def b2c_result_callback(token: str, request: Request, db: Session = Depends(get_db)):
    """Daraja B2C ResultURL — the definitive outcome of a disbursement (MPESA-01).
    Idempotently applies the result: success activates the loan, failure reverts it
    to `approved` for retry. Durably ingested (persist-first) and always acked."""
    _check_callback_token(token)
    ws.check_ip_allowlist(request, ws.ENDPOINT_B2C_RESULT)  # 403 before processing in enforce mode
    body = await _read_payload(request)
    return _ingest_and_process(db, ws.ENDPOINT_B2C_RESULT, body, _process_b2c_result,
                               {"ResultCode": 0, "ResultDesc": "Result received"})


@router.post("/mpesa/{token}/b2c-timeout")
async def b2c_timeout_callback(token: str, request: Request, db: Session = Depends(get_db)):
    """Daraja B2C QueueTimeOutURL — payout outcome unknown. Flags the transaction
    timed_out for the reconcile sweep; does NOT revert the loan (avoids a double
    payout should it later settle)."""
    _check_callback_token(token)
    ws.check_ip_allowlist(request, ws.ENDPOINT_B2C_TIMEOUT)
    body = await _read_payload(request)
    return _ingest_and_process(db, ws.ENDPOINT_B2C_TIMEOUT, body, _process_b2c_timeout,
                               {"ResultCode": 0, "ResultDesc": "Timeout received"})


@router.post("/mpesa/{token}/stk-callback")
async def stk_callback(token: str, request: Request, db: Session = Depends(get_db)):
    """Daraja STK push CallBackURL — result of a collections prompt. On success
    records the repayment (idempotent on the M-Pesa receipt) and reduces the
    balance; on failure marks the transaction failed."""
    _check_callback_token(token)
    ws.check_ip_allowlist(request, ws.ENDPOINT_STK_CALLBACK)
    body = await _read_payload(request)
    return _ingest_and_process(db, ws.ENDPOINT_STK_CALLBACK, body, _process_stk_callback,
                               {"ResultCode": 0, "ResultDesc": "Result received"})


@router.post("/mpesa/{token}/c2b-callback")
async def c2b_callback(token: str, request: Request, db: Session = Depends(get_db)):
    """Daraja C2B confirmation webhook (MPESA-04).

    Unauthenticated (token + IP allowlist). Derives the tenant + loan from
    BillRefNumber (never trusts a client JWT), is idempotent on TransID, and
    cross-checks TransAmount against the outstanding balance: a payment larger than
    the outstanding is recorded but flagged for review and NOT auto-marked paid."""
    _check_callback_token(token)
    ws.check_ip_allowlist(request, ws.ENDPOINT_C2B_CALLBACK)
    body = await _read_payload(request)
    return _ingest_and_process(db, ws.ENDPOINT_C2B_CALLBACK, body, _process_c2b_callback,
                               {"ResultCode": 0, "ResultDesc": "Confirmation received successfully"})


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



# --------------------------------------------------------------------------- #
# Suspense account — list / allocate to a loan / refund  [reconcile.execute]
# --------------------------------------------------------------------------- #
def _suspense_dict(s: SuspenseEntry) -> dict:
    return {
        "id": s.id, "source": s.source, "mpesa_ref": s.mpesa_ref, "phone": s.phone,
        "amount": float(s.amount or 0), "reason": s.reason, "status": s.status,
        "matched_loan_id": s.matched_loan_id,
        "created_at": s.created_at, "resolved_at": s.resolved_at,
        "resolved_by_user_id": s.resolved_by_user_id,
    }


@router.get("/suspense")
def list_suspense(tenant_id: int = Depends(require_module("payments")),
                  db: Session = Depends(get_db),
                  user: User = Depends(require_permission("reconcile.execute")),
                  status: str = "", page: int = 1, page_size: int = 50):
    """List suspense-account entries (unapplied receipts) for the tenant."""
    q = db.query(SuspenseEntry).filter(SuspenseEntry.tenant_id == tenant_id)
    if status:
        q = q.filter(SuspenseEntry.status == status)
    total = q.count()
    rows = (q.order_by(SuspenseEntry.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    open_total = float(sum(float(r.amount or 0) for r in
                           db.query(SuspenseEntry)
                           .filter(SuspenseEntry.tenant_id == tenant_id,
                                   SuspenseEntry.status == "open").all()))
    return {"total": total, "page": page, "open_balance": open_total,
            "items": [_suspense_dict(s) for s in rows]}


@router.post("/suspense/{entry_id}/allocate")
def allocate_suspense(entry_id: int, body: SuspenseAllocateIn, request: Request,
                      tenant_id: int = Depends(require_module("payments")),
                      db: Session = Depends(get_db),
                      user: User = Depends(require_permission("reconcile.execute"))):
    """Apply an open suspense entry to a loan as a repayment.

    Reuses the same Decimal money helpers as /reconcile (interest/principal split,
    balance reduction). Idempotent: an already-resolved entry is a no-op."""
    entry = (db.query(SuspenseEntry)
             .filter(SuspenseEntry.id == entry_id,
                     SuspenseEntry.tenant_id == tenant_id).with_for_update().first())
    if not entry:
        raise HTTPException(404, "Suspense entry not found")
    if entry.status != "open":
        return {"status": "already_resolved", "suspense_status": entry.status,
                "matched_loan_id": entry.matched_loan_id}

    loan = (db.query(Loan)
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id)
            .with_for_update().first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    if loan.status not in ("active", "overdue"):
        raise HTTPException(400, f"Loan is not collectible (status={loan.status})")

    amount = float(entry.amount or 0)
    if amount <= 0:
        raise HTTPException(400, "Suspense entry has no positive amount")

    # Reuse an existing mpesa_ref if free; otherwise derive a suspense-scoped ref
    # so the repayment idempotency guarantee still holds.
    rep_ref = entry.mpesa_ref
    if rep_ref:
        dup = (db.query(Repayment)
               .filter(Repayment.tenant_id == tenant_id,
                       Repayment.mpesa_ref == rep_ref).first())
        if dup is not None:
            rep_ref = f"SUS-{entry.id}-{rep_ref}"
    else:
        rep_ref = f"SUS-{entry.id}"

    interest, principal = split_interest_principal(amount, loan.interest_rate)
    rep = Repayment(tenant_id=tenant_id, loan_id=loan.id, amount=money(amount),
                    interest_component=interest, principal_component=principal,
                    payment_date=datetime.utcnow(), method="suspense_allocation",
                    mpesa_ref=rep_ref)
    db.add(rep)
    loan.outstanding_balance = reduce_balance(loan.outstanding_balance or 0, amount)
    if loan.outstanding_balance <= 0 and loan.status in ("active", "overdue"):
        loan.status = "paid"

    entry.status = "allocated"
    entry.matched_loan_id = loan.id
    entry.resolved_at = datetime.utcnow()
    entry.resolved_by_user_id = user.id

    write_audit(db, tenant_id=tenant_id, user=user, action="suspense.allocate",
                entity_type="suspense_entry", entity_id=entry.id,
                details={"loan_id": loan.id, "amount": amount}, request=request)
    log_money_event("suspense_allocate", tenant_id=tenant_id, user_id=user.id,
                    loan_id=loan.id, amount=money(amount), ref=rep_ref,
                    detail=f"suspense_id={entry.id} outstanding={loan.outstanding_balance}")
    db.commit()
    return {"status": "allocated", "suspense_id": entry.id, "loan_id": loan.id,
            "outstanding_balance": float(loan.outstanding_balance),
            "loan_status": loan.status}


@router.post("/suspense/{entry_id}/refund")
def refund_suspense(entry_id: int, body: SuspenseRefundIn, request: Request,
                    tenant_id: int = Depends(require_module("payments")),
                    db: Session = Depends(get_db),
                    user: User = Depends(require_permission("reconcile.execute"))):
    """Mark an open suspense entry as refunded to the payer. Records the intent
    and audits it; the actual payout follows the standard refund/B2C flow."""
    entry = (db.query(SuspenseEntry)
             .filter(SuspenseEntry.id == entry_id,
                     SuspenseEntry.tenant_id == tenant_id).with_for_update().first())
    if not entry:
        raise HTTPException(404, "Suspense entry not found")
    if entry.status != "open":
        return {"status": "already_resolved", "suspense_status": entry.status}

    entry.status = "refunded"
    entry.resolved_at = datetime.utcnow()
    entry.resolved_by_user_id = user.id
    payload = dict(entry.raw_payload or {})
    if body.note:
        payload["refund_note"] = body.note
    entry.raw_payload = payload

    write_audit(db, tenant_id=tenant_id, user=user, action="suspense.refund",
                entity_type="suspense_entry", entity_id=entry.id,
                details={"amount": float(entry.amount or 0), "note": body.note},
                request=request)
    log_money_event("suspense_refund", tenant_id=tenant_id, user_id=user.id,
                    amount=money(entry.amount or 0), ref=entry.mpesa_ref,
                    detail=f"suspense_id={entry.id}")
    db.commit()
    return {"status": "refunded", "suspense_id": entry.id,
            "amount": float(entry.amount or 0)}
