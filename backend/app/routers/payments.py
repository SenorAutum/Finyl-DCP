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
from app.core.money import money
from app.core.obs import log_money_event
from app.models import Loan, PaymentTransaction, PendingApproval, Repayment, User, SuspenseEntry
from app.schemas import (C2BCallback, StkPushRequest, DisburseRequest,
                         RefundRequest, ReconcileRequest, ManualPaymentIn,
                         PaymentSettingsIn, PayoutResolveIn, SuspenseAllocateIn, SuspenseRefundIn)
from app.services import mpesa
from app.services import payment_approvals as pay_appr
from app.services import rbac as rbac_svc
from app.services import repayments as rp
from app.services import webhook_security as ws
from app.services.payment_matching import match_reference
from app.services.disbursement import (execute_disbursement,
                                       apply_b2c_result, mark_b2c_timeout)

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

logger = logging.getLogger("finyl.payments")


def _record_suspense(db: Session, *, tenant_id: int, reason: str, amount, ref,
                     phone=None, loan_id=None, source="c2b", raw_payload=None, **extra):
    """Additively record a suspense entry, idempotent on (tenant_id, mpesa_ref).

    Thin wrapper kept for existing callers/tests; the logic lives in
    services/repayments.record_suspense. Does NOT commit."""
    return rp.record_suspense(db, tenant_id=tenant_id, reason=reason, amount=amount,
                              ref=ref, phone=phone, loan_id=loan_id, source=source,
                              raw_payload=raw_payload, **extra)


def _parse_mpesa_time(value) -> datetime | None:
    """Daraja TransTime / TransactionDate is 'YYYYMMDDHHMMSS' in Kenyan time
    (EAT, UTC+3). Stored as naive UTC like every other timestamp here."""
    if value in (None, ""):
        return None
    try:
        from datetime import timedelta
        return datetime.strptime(str(value).strip()[:14], "%Y%m%d%H%M%S") - timedelta(hours=3)
    except (TypeError, ValueError):
        return None


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
    # F07: lock the loan so two refund requests are worked out one after the
    # other, and subtract refunds already requested or sent.
    loan = (db.query(Loan)
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id)
            .with_for_update().first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    from app.services.disbursement import refundable_overpayment
    overpayment = float(refundable_overpayment(db, tenant_id, loan))
    if overpayment <= 0:
        raise HTTPException(400, "No unrefunded overpayment on this loan "
                                 "(refunds already requested or sent are deducted)")

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
    """Manually reconcile a received payment against a loan (legacy endpoint).

    WP2: now goes through the same rules as POST /payments/manual — a second
    approver is required above the DCP's `manual_payment` threshold (and for
    every amount when no threshold is configured: fail closed, as for
    disbursements). Idempotent on mpesa_ref."""
    amount = money(body.amount)
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    loan = (db.query(Loan)
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, "Loan not found")

    ref = pay_appr.clean_reference(body.mpesa_ref)
    if ref:
        existing = (db.query(Repayment)
                    .filter(Repayment.tenant_id == tenant_id,
                            Repayment.mpesa_ref == ref).first())
        if existing:
            return {"status": "duplicate_ignored", "mpesa_ref": ref,
                    "repayment_id": existing.id,
                    "outstanding_balance": float(loan.outstanding_balance or 0),
                    "loan_status": loan.status}
    pay_appr.ensure_reference_free(db, tenant_id, ref)
    if loan.status not in rp.COLLECTIBLE_STATUSES:
        raise HTTPException(400, f"Loan is not collectible (status={loan.status}); "
                                 "record the money via the suspense account instead")

    details = {"channel": "mpesa_paybill", "reference": ref,
               "paid_at": datetime.utcnow().isoformat(), "source_endpoint": "reconcile"}
    if rbac_svc.requires_maker_checker(db, tenant_id, pay_appr.MANUAL_THRESHOLD, float(amount)):
        pending = pay_appr.park_manual_payment(db, tenant_id=tenant_id, user=user, loan=loan,
                                               amount=amount, details=details, reason=None)
        write_audit(db, tenant_id=tenant_id, user=user, action="reconcile.request",
                    entity_type="loan", entity_id=loan.id,
                    details={"amount": float(amount), "mpesa_ref": ref,
                             "pending_id": pending.id}, request=request)
        db.commit()
        return {"status": "pending_approval", "pending_id": pending.id,
                "message": "Manual payment recorded — awaiting a second approver."}

    loan = db.query(Loan).filter(Loan.id == loan.id).with_for_update().first()
    res = rp.apply_payment(db, tenant_id=tenant_id, loan=loan, amount=amount, ref=ref,
                           method="reconciliation", source="manual", actor_user_id=user.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="reconcile.execute",
                entity_type="loan", entity_id=loan.id,
                details={"amount": float(amount), "mpesa_ref": res.ref,
                         "applied": float(res.applied), "excess": float(res.excess)},
                request=request)
    db.commit()
    return {"status": "reconciled", "outstanding_balance": float(loan.outstanding_balance),
            "loan_status": loan.status, "applied": float(res.applied),
            "excess": float(res.excess), "suspense_id": res.suspense_id}


# --------------------------------------------------------------------------- #
# WP2 — Manual payment entry (paybill / till / bank / cash) with maker-checker
# --------------------------------------------------------------------------- #
@router.post("/manual")
def record_manual_payment(body: ManualPaymentIn, request: Request,
                          tenant_id: int = Depends(require_module("payments")),
                          db: Session = Depends(get_db),
                          user: User = Depends(require_permission("reconcile.execute"))):
    """Record money received outside the automatic paybill flow — a payment the
    callback missed, a till or bank deposit, or cash at a branch.

    The staff member supplies the transaction reference (M-Pesa code / bank
    reference) as evidence. The same reference can never be recorded twice.
    Above the DCP's `manual_payment` threshold (or always, if none is set) the
    entry waits for a second person to approve it before any balance changes."""
    channel = (body.channel or "").strip().lower()
    if channel not in pay_appr.MANUAL_CHANNELS:
        raise HTTPException(422, f"channel must be one of {sorted(pay_appr.MANUAL_CHANNELS)}")
    amount = money(body.amount)
    if amount <= 0:
        raise HTTPException(422, "Amount must be positive")
    ref = pay_appr.clean_reference(body.reference)
    if not ref and channel != "cash":
        raise HTTPException(422, "A transaction reference (M-Pesa code or bank reference) "
                                 "is required for this channel")
    paid_at = pay_appr.check_paid_at(body.paid_at)
    note = (body.note or "").strip()
    if not note:
        raise HTTPException(422, "A note describing the evidence (e.g. statement line, "
                                 "receipt number, customer SMS) is required")

    loan = pay_appr.resolve_manual_loan(db, tenant_id, body.loan_id, body.customer_number)
    if loan.status not in rp.COLLECTIBLE_STATUSES:
        raise HTTPException(400, f"Loan is not collectible (status={loan.status})")
    pay_appr.ensure_reference_free(db, tenant_id, ref)
    if not ref:
        ref = rp.internal_ref("CASH")

    details = {"channel": channel, "reference": ref, "paid_at": paid_at.isoformat(),
               "payer_name": (body.payer_name or "").strip() or None,
               "payer_phone": (body.payer_phone or "").strip() or None,
               "note": note[:500], "source_endpoint": "manual"}
    if rbac_svc.requires_maker_checker(db, tenant_id, pay_appr.MANUAL_THRESHOLD, float(amount)):
        pending = pay_appr.park_manual_payment(db, tenant_id=tenant_id, user=user, loan=loan,
                                               amount=amount, details=details, reason=note[:200])
        write_audit(db, tenant_id=tenant_id, user=user, action="manual_payment.request",
                    entity_type="loan", entity_id=loan.id,
                    details={"amount": float(amount), **details, "pending_id": pending.id},
                    request=request)
        db.commit()
        return {"status": "pending_approval", "pending_id": pending.id, "loan_id": loan.id,
                "reference": ref,
                "message": "Manual payment recorded — awaiting a second approver."}

    result = pay_appr.apply_manual(db, tenant_id=tenant_id, loan_id=loan.id, amount=amount,
                                   details=details, actor_user_id=user.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="manual_payment.execute",
                entity_type="loan", entity_id=loan.id,
                details={"amount": float(amount), **details, "result": result},
                request=request)
    db.commit()
    return result


@router.get("/settings")
def get_payment_settings(tenant_id: int = Depends(require_module("payments")),
                         db: Session = Depends(get_db),
                         user: User = Depends(require_permission(
                             "thresholds.view", "reconcile.execute", mode="any"))):
    """This DCP's payment settings: paybill number and how a payment is split
    between interest, fees/charges and principal."""
    out = rp.get_payment_settings(db, tenant_id)
    out["paybill"] = mpesa.resolve_creds(db, tenant_id).shortcode or None
    out["allocation_order_options"] = rp.ALLOCATION_ORDER_LABELS
    out["early_settlement_options"] = rp.EARLY_SETTLEMENT_METHODS
    return out


@router.put("/settings")
def update_payment_settings(body: PaymentSettingsIn, request: Request,
                            tenant_id: int = Depends(require_module("payments")),
                            db: Session = Depends(get_db),
                            user: User = Depends(require_permission("thresholds.manage"))):
    """Set the allocation order and/or how earned interest is counted on early
    full repayment. `basis` records WHY the allocation order was chosen (e.g.
    'LN 191/2026 reg. 56(2)') so an auditor can see what it was based on."""
    before = rp.get_payment_settings(db, tenant_id)
    try:
        after = rp.set_payment_settings(db, tenant_id, user.id,
                                        allocation_order=body.allocation_order, basis=body.basis,
                                        early_settlement_method=body.early_settlement_method)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    write_audit(db, tenant_id=tenant_id, user=user, action="config.payments.update",
                entity_type="integration_config",
                details={"allocation_order": {"from": before["allocation_order"],
                                              "to": after["allocation_order"]},
                         "early_settlement_method": {"from": before["early_settlement_method"],
                                                     "to": after["early_settlement_method"]},
                         "basis": body.basis}, request=request)
    db.commit()
    return get_payment_settings(tenant_id=tenant_id, db=db, user=user)


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
                 PaymentTransaction.status.in_(("initiated", "processing", "timed_out",
                                                "uncertain")),
                 PaymentTransaction.created_at <= cutoff)
         .order_by(PaymentTransaction.created_at.asc()))
    stuck = q.all()
    resolved = []
    if simulate and not mpesa.is_configured(mpesa.resolve_creds(db, tenant_id)):
        for txn in stuck:
            if txn.status not in ("processing", "timed_out") or not txn.mpesa_ref:
                continue  # never reached Safaricom's queue: resolve by hand
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
                       "needs_manual_check": t.status in ("initiated", "uncertain"),
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
    """Locate a B2C payout OR refund by ConversationID (stored as mpesa_ref),
    falling back to OriginatorConversationID recorded in the raw payload.
    WP5: refunds are B2C payments too — before, their results were never matched."""
    txn = None
    if conversation_id:
        txn = (db.query(PaymentTransaction)
               .filter(PaymentTransaction.type.in_(("b2c", "refund")),
                       PaymentTransaction.mpesa_ref == str(conversation_id)).first())
    if txn is None and originator_id:
        candidates = (db.query(PaymentTransaction)
                      .filter(PaymentTransaction.type.in_(("b2c", "refund")),
                              PaymentTransaction.status.in_(("processing", "timed_out",
                                                             "uncertain"))).all())
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
    amount = money(meta.get("Amount") or txn.amount or 0)
    paid_at = _parse_mpesa_time(meta.get("TransactionDate"))

    txn.status = "success"
    txn.mpesa_ref = str(receipt)
    txn.raw_payload = {**(txn.raw_payload or {}), "result_callback": body}
    # Concurrency: lock the loan row so concurrent repayment callbacks serialize.
    loan = db.query(Loan).filter(
        Loan.id == txn.loan_id, Loan.tenant_id == tenant_id).with_for_update().first()
    if loan is not None and amount > 0:
        # WP2: same rules as every other channel (idempotent on the receipt,
        # allocation order, overpayment -> suspense, defaulted loans collectible).
        rp.apply_payment(db, tenant_id=tenant_id, loan=loan, amount=amount, ref=str(receipt),
                         method="stk_push", paid_at=paid_at,
                         phone=str(meta.get("PhoneNumber") or "") or None, source="stk")
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Result received"}, tenant_id


def _c2b_payer_name(cb: C2BCallback) -> str | None:
    parts = [cb.FirstName, cb.MiddleName, cb.LastName]
    name = " ".join(p.strip() for p in parts if p and str(p).strip())
    return name or None


def _process_c2b_callback(db, body) -> tuple[dict, int | None]:
    """Paybill confirmation (WP2).

      1. The paybill number (BusinessShortCode) decides the DCP. Each DCP has
         its own paybill; money paid into DCP A's paybill can only ever be
         applied to DCP A's loans, whatever account number the payer typed.
      2. Inside that DCP, BillRefNumber is matched to a loan account number or a
         customer number (services/payment_matching).
      3. Matched -> applied via the shared routine. Not matched -> parked in
         that DCP's suspense account for a person to allocate or refund (it is
         no longer dead-lettered: the DCP is known, so it is a review item).
      4. An unknown paybill number is NOT guessed: it is dead-lettered + alerted.
    Payloads without a BusinessShortCode (older tests / non-Daraja senders) keep
    the pre-WP2 behaviour of matching the loan account number directly."""
    try:
        cb = C2BCallback(**body)
    except Exception:
        # Malformed — acknowledge (avoid retries) but keep the event for review.
        raise ws.WebhookUnresolved("unparseable C2B confirmation payload")

    ref = cb.TransID or None
    amount = money(cb.TransAmount)
    paid_at = _parse_mpesa_time(cb.TransTime)
    payer_name = _c2b_payer_name(cb)
    shortcode = (str(cb.BusinessShortCode).strip() if cb.BusinessShortCode not in (None, "")
                 else None)

    if shortcode:
        tenant_id = ws.shortcode_to_tenant(db, shortcode)
        if tenant_id is None:
            raise ws.WebhookUnresolved(f"paybill {shortcode} is not registered to any DCP")
    else:
        legacy = (db.query(Loan).filter(Loan.account_number == cb.BillRefNumber)
                  .order_by(Loan.id.desc()).first()) if cb.BillRefNumber else None
        if legacy is None:
            raise ws.WebhookUnresolved(f"no loan for account {cb.BillRefNumber}")
        tenant_id = legacy.tenant_id

    # Idempotency on TransID (MPESA-03): duplicate delivery is a no-op.
    if rp.reference_already_used(db, tenant_id, ref):
        return {"ResultCode": 0, "ResultDesc": "Duplicate ignored"}, tenant_id

    match = match_reference(db, tenant_id, cb.BillRefNumber, phone=cb.MSISDN)
    if match.loan is None:
        entry = rp.record_suspense(
            db, tenant_id=tenant_id, reason=match.reason or "unmatched", amount=amount,
            ref=ref or rp.internal_ref("C2B"), phone=cb.MSISDN,
            suggested_loan_id=match.suggested_loan_id, source="c2b",
            bill_ref=cb.BillRefNumber, payer_name=payer_name, paid_at=paid_at,
            raw_payload={"shortcode": shortcode, "account_entered": cb.BillRefNumber,
                         "borrower_id": match.borrower_id})
        db.add(PaymentTransaction(tenant_id=tenant_id, type="c2b", loan_id=None,
                                  amount=amount, phone=cb.MSISDN, mpesa_ref=ref,
                                  status="success",
                                  raw_payload={**body, "review": True,
                                               "review_reason": match.reason or "unmatched"}))
        db.flush()
        log_money_event("c2b_unmatched", tenant_id=tenant_id, amount=amount,
                        phone=cb.MSISDN, ref=str(ref),
                        detail=f"bill_ref={cb.BillRefNumber!r} reason={match.reason} "
                               f"suspense_id={entry.id}")
        db.commit()
        return {"ResultCode": 0, "ResultDesc": "Confirmation received successfully",
                "matched": False, "suspense_id": entry.id}, tenant_id

    # Concurrency: lock the loan row so concurrent repayment callbacks serialize.
    loan = (db.query(Loan).filter(Loan.id == match.loan.id, Loan.tenant_id == tenant_id)
            .with_for_update().first())
    res = rp.apply_payment(db, tenant_id=tenant_id, loan=loan, amount=amount, ref=ref,
                           method="mpesa_c2b", paid_at=paid_at, phone=cb.MSISDN,
                           source="c2b", bill_ref=cb.BillRefNumber, payer_name=payer_name)
    if res.status == "duplicate_ignored":
        db.rollback()
        return {"ResultCode": 0, "ResultDesc": "Duplicate ignored",
                "loan_status": loan.status}, tenant_id
    flagged = res.status == "suspense" or res.excess > 0
    txn_payload = {**body, "matched_by": match.how}
    if flagged:
        txn_payload.update({"review": True,
                            "review_reason": ("overpayment" if res.status == "applied"
                                              else f"loan_status_{loan.status}")})
    db.add(PaymentTransaction(tenant_id=tenant_id, type="c2b", loan_id=loan.id, amount=amount,
                              phone=cb.MSISDN, mpesa_ref=res.ref, status="success",
                              raw_payload=txn_payload))
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Confirmation received successfully",
            "matched": True, "matched_by": match.how,
            "loan_status": loan.status, "flagged_for_review": flagged,
            "applied": float(res.applied), "excess": float(res.excess),
            "outstanding_balance": float(loan.outstanding_balance or 0)}, tenant_id


def _find_reversal_txn(db, conversation_id, originator_id=None):
    txn = None
    if conversation_id:
        txn = (db.query(PaymentTransaction)
               .filter(PaymentTransaction.type == "reversal",
                       PaymentTransaction.mpesa_ref == str(conversation_id)).first())
    if txn is None and originator_id:
        for c in (db.query(PaymentTransaction)
                  .filter(PaymentTransaction.type == "reversal",
                          PaymentTransaction.status.in_(("processing", "timed_out"))).all()):
            if (c.raw_payload or {}).get("result", {}).get("OriginatorConversationID") == originator_id:
                txn = c
                break
    return txn


def _process_reversal_result(db, body) -> tuple[dict, int | None]:
    result = body.get("Result", {}) if isinstance(body, dict) else {}
    txn = _find_reversal_txn(db, result.get("ConversationID"),
                             result.get("OriginatorConversationID"))
    if txn is None:
        raise ws.WebhookUnresolved(f"no reversal for conv={result.get('ConversationID')}")
    txn = (db.query(PaymentTransaction).filter(PaymentTransaction.id == txn.id)
           .with_for_update().first())
    pay_appr.apply_reversal_result(db, txn.tenant_id, txn, result)
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Result received"}, txn.tenant_id


def _process_reversal_timeout(db, body) -> tuple[dict, int | None]:
    result = body.get("Result", {}) if isinstance(body, dict) else {}
    txn = _find_reversal_txn(db, result.get("ConversationID"),
                             result.get("OriginatorConversationID"))
    if txn is None:
        raise ws.WebhookUnresolved(f"no reversal for conv={result.get('ConversationID')}")
    pay_appr.mark_reversal_timeout(db, txn, body)
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Timeout received"}, txn.tenant_id


# Retry dispatch: endpoint label -> processor. Used by the scheduler worker to
# reprocess durable `failed` events idempotently.
_PROCESSORS = {
    ws.ENDPOINT_B2C_RESULT: _process_b2c_result,
    ws.ENDPOINT_B2C_TIMEOUT: _process_b2c_timeout,
    ws.ENDPOINT_STK_CALLBACK: _process_stk_callback,
    ws.ENDPOINT_C2B_CALLBACK: _process_c2b_callback,
    ws.ENDPOINT_REVERSAL_RESULT: _process_reversal_result,
    ws.ENDPOINT_REVERSAL_TIMEOUT: _process_reversal_timeout,
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


@router.post("/mpesa/{token}/reversal-result")
async def reversal_result_callback(token: str, request: Request, db: Session = Depends(get_db)):
    """Daraja Reversal ResultURL — outcome of a wrong-payment reversal (WP4)."""
    _check_callback_token(token)
    ws.check_ip_allowlist(request, ws.ENDPOINT_REVERSAL_RESULT)
    body = await _read_payload(request)
    return _ingest_and_process(db, ws.ENDPOINT_REVERSAL_RESULT, body, _process_reversal_result,
                               {"ResultCode": 0, "ResultDesc": "Result received"})


@router.post("/mpesa/{token}/reversal-timeout")
async def reversal_timeout_callback(token: str, request: Request, db: Session = Depends(get_db)):
    """Daraja Reversal QueueTimeOutURL — outcome unknown; entry stays pending."""
    _check_callback_token(token)
    ws.check_ip_allowlist(request, ws.ENDPOINT_REVERSAL_TIMEOUT)
    body = await _read_payload(request)
    return _ingest_and_process(db, ws.ENDPOINT_REVERSAL_TIMEOUT, body, _process_reversal_timeout,
                               {"ResultCode": 0, "ResultDesc": "Timeout received"})


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
        "suggested_loan_id": s.suggested_loan_id,
        "bill_ref": s.bill_ref, "payer_name": s.payer_name, "paid_at": s.paid_at,
        "can_reverse": pay_appr.reversal_eligibility(s)[0],
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
    """Apply an open suspense entry to a loan.

    WP2: same maker-checker rule as manual payments — above the DCP's
    `manual_payment` threshold (or always, when none is set) the allocation
    waits for a second approver and the entry is held as `pending_approval`
    so it cannot be allocated or refunded twice meanwhile. Idempotent: an
    already-resolved entry is a no-op."""
    entry = (db.query(SuspenseEntry)
             .filter(SuspenseEntry.id == entry_id,
                     SuspenseEntry.tenant_id == tenant_id).with_for_update().first())
    if not entry:
        raise HTTPException(404, "Suspense entry not found")
    if entry.status != "open":
        return {"status": "already_resolved", "suspense_status": entry.status,
                "matched_loan_id": entry.matched_loan_id}
    if money(entry.amount or 0) <= 0:
        raise HTTPException(400, "Suspense entry has no positive amount")

    loan = (db.query(Loan)
            .filter(Loan.id == body.loan_id, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    if loan.status not in rp.COLLECTIBLE_STATUSES:
        raise HTTPException(400, f"Loan is not collectible (status={loan.status})")

    amount = money(entry.amount)
    if rbac_svc.requires_maker_checker(db, tenant_id, pay_appr.MANUAL_THRESHOLD, float(amount)):
        pending = pay_appr.park_suspense_allocation(db, tenant_id=tenant_id, user=user,
                                                    entry=entry, loan=loan)
        write_audit(db, tenant_id=tenant_id, user=user, action="suspense.allocate.request",
                    entity_type="suspense_entry", entity_id=entry.id,
                    details={"loan_id": loan.id, "amount": float(amount),
                             "pending_id": pending.id}, request=request)
        db.commit()
        return {"status": "pending_approval", "pending_id": pending.id,
                "suspense_id": entry.id, "loan_id": loan.id,
                "message": "Allocation awaiting a second approver."}

    result = pay_appr.allocate_entry(db, tenant_id=tenant_id, entry=entry, loan_id=loan.id,
                                     resolver_user_id=user.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="suspense.allocate",
                entity_type="suspense_entry", entity_id=entry.id,
                details={"loan_id": loan.id, "amount": float(amount), "result": result},
                request=request)
    db.commit()
    return result


@router.post("/suspense/{entry_id}/refund")
def refund_suspense(entry_id: int, body: SuspenseRefundIn, request: Request,
                    tenant_id: int = Depends(require_module("payments")),
                    db: Session = Depends(get_db),
                    user: User = Depends(require_permission("reconcile.execute"))):
    """Request a refund of an open suspense entry to the payer.

    WP2: refunds ALWAYS need a second approver (as for loan refunds, MPESA-05).
    On approval the money is actually sent back via M-Pesa B2C (previously this
    endpoint only marked the entry refunded and nothing was paid out). The
    destination is the paying number; a different number may be supplied only
    with a note, and the checker sees both before approving."""
    entry = (db.query(SuspenseEntry)
             .filter(SuspenseEntry.id == entry_id,
                     SuspenseEntry.tenant_id == tenant_id).with_for_update().first())
    if not entry:
        raise HTTPException(404, "Suspense entry not found")
    if entry.status != "open":
        return {"status": "already_resolved", "suspense_status": entry.status}
    pending = pay_appr.park_suspense_refund(db, tenant_id=tenant_id, user=user, entry=entry,
                                            refund_phone=body.refund_phone, note=body.note)
    write_audit(db, tenant_id=tenant_id, user=user, action="suspense.refund.request",
                entity_type="suspense_entry", entity_id=entry.id,
                details={"amount": float(entry.amount or 0), "note": body.note,
                         "phone": pending.phone, "pending_id": pending.id},
                request=request)
    db.commit()
    return {"status": "pending_approval", "pending_id": pending.id,
            "suspense_id": entry.id, "amount": float(entry.amount or 0),
            "phone": pending.phone,
            "message": "Refund awaiting a second approver."}


@router.post("/suspense/{entry_id}/reverse")
def reverse_suspense(entry_id: int, body: SuspenseRefundIn, request: Request,
                     tenant_id: int = Depends(require_module("payments")),
                     db: Session = Depends(get_db),
                     user: User = Depends(require_permission("reconcile.execute"))):
    """Request that Safaricom reverse a wrong paybill payment back to the payer.

    WP4: unlike a B2C refund this needs no phone number (it works when
    Safaricom masked the payer's number) and returns the money exactly as it
    came. Only a receipt that is wholly in suspense can be reversed; it ALWAYS
    needs a second approver (refund.approve), and the outcome arrives from
    Safaricom on the reversal-result callback."""
    entry = (db.query(SuspenseEntry)
             .filter(SuspenseEntry.id == entry_id,
                     SuspenseEntry.tenant_id == tenant_id).with_for_update().first())
    if not entry:
        raise HTTPException(404, "Suspense entry not found")
    if entry.status != "open":
        return {"status": "already_resolved", "suspense_status": entry.status}
    pending = pay_appr.park_suspense_reversal(db, tenant_id=tenant_id, user=user, entry=entry,
                                              note=body.note)
    write_audit(db, tenant_id=tenant_id, user=user, action="suspense.reversal.request",
                entity_type="suspense_entry", entity_id=entry.id,
                details={"amount": float(entry.amount or 0), "mpesa_ref": entry.mpesa_ref,
                         "note": body.note, "pending_id": pending.id}, request=request)
    db.commit()
    return {"status": "pending_approval", "pending_id": pending.id, "suspense_id": entry.id,
            "message": "Reversal awaiting a second approver."}


# --------------------------------------------------------------------------- #
# WP5 (F06) — settle a payout whose result never arrived, after checking M-Pesa
# --------------------------------------------------------------------------- #
@router.post("/payouts/{txn_id}/resolve")
def resolve_payout(txn_id: int, body: PayoutResolveIn, request: Request,
                   tenant_id: int = Depends(require_module("payments")),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_permission("disburse.approve", "refund.approve",
                                                           mode="any"))):
    """Record what the M-Pesa portal shows for a payout stuck as initiated /
    uncertain / timed out: `paid` (with the M-Pesa receipt) settles it exactly
    as Safaricom's result would; `not_paid` makes the loan retryable or re-opens
    the refunded receipt. The person who started the payout cannot resolve it."""
    from app.core.permissions import has_permission
    from app.services import disbursement as disb
    txn = (db.query(PaymentTransaction)
           .filter(PaymentTransaction.id == txn_id, PaymentTransaction.tenant_id == tenant_id,
                   PaymentTransaction.type.in_(("b2c", "refund")))
           .with_for_update().first())
    if not txn:
        raise HTTPException(404, "Payout not found")
    need = "disburse.approve" if txn.type == "b2c" else "refund.approve"
    if not has_permission(user.role, need):
        raise HTTPException(403, f"Missing required permission: {need}")
    if (txn.raw_payload or {}).get("actor_user_id") == user.id:
        raise HTTPException(403, "The person who started this payout cannot resolve it")
    out = disb.resolve_payout(db, tenant_id, txn, body.outcome, body.receipt, body.note, user.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="payout.resolve",
                entity_type="payment_transaction", entity_id=txn.id,
                details={"outcome": body.outcome, "receipt": body.receipt, "note": body.note,
                         "result": out}, request=request)
    db.commit()
    return out
