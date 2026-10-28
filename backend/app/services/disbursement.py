"""
Money-movement execution helpers (real Daraja B2C, credential-gated). Kept in one
place so the direct path (below threshold) and the maker-checker approval path
both run the exact same side-effects. When Daraja credentials are not configured
the payout returns a *simulated asynchronous acknowledgement* (see mpesa.py) so
the processing → result state machine is exercised end-to-end without faking a
settled payout.

State machine (MPESA-01 / MPESA-03):
  approved --(atomic guard)--> processing --(b2c-result ResultCode==0)--> active
  processing --(result failure / revert)--> approved (retryable)

B2C is asynchronous: the payout request only returns an *acknowledgement*
(ConversationID). The loan is NEVER marked `active` here; it is moved to
`processing` and only the ResultURL webhook (or the reconcile sweep) applies the
definitive result via `apply_b2c_result`.
"""
import logging
import math
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException

from app.core.money import D, money
from app.core.obs import log_money_event
from app.models import Loan, PaymentTransaction
from app.services import mpesa, sms

logger = logging.getLogger("finyl.payments")


# --------------------------------------------------------------------------- #
# WP5 (F06) — durable payout intent
#
# Before WP5 the B2C request was sent INSIDE the caller's database transaction,
# before anything was saved. If the request was accepted by Safaricom and the
# transaction then rolled back (any later error), no record of the payout
# survived: the loan went back to `approved` and could be paid out again.
#
# Now every payout is a three-step, committed sequence:
#   1. save a PaymentTransaction in state `initiated` (+ loan -> processing) and
#      COMMIT, before talking to Safaricom;
#   2. call Safaricom;
#   3. save the outcome and COMMIT:
#        accepted            -> `processing` with Safaricom's ConversationID;
#        definitely refused  -> `failed`, loan back to `approved` (safe to retry);
#        outcome unknown     -> `uncertain`, loan stays `processing`.
#   While any payout for a loan is initiated/processing/timed_out/uncertain, a
#   new payout for that loan is refused until someone resolves it (the result
#   callback, or a person who checked the M-Pesa portal: resolve_payout()).
# --------------------------------------------------------------------------- #
OPEN_PAYOUT_STATES = ("initiated", "processing", "timed_out", "uncertain")


class PayoutRefused(Exception):
    """Safaricom definitely did not take the payout (safe to retry)."""


class PayoutUncertain(Exception):
    """The request may have reached Safaricom; the outcome is unknown."""


def _send_b2c(phone: str, amount, remarks: str, creds=None) -> dict:
    """Call Daraja B2C and classify failures. Returns the payload on acceptance."""
    import httpx
    try:
        payload = mpesa.b2c_disburse(phone, amount, remarks, creds=creds)
    except mpesa.DarajaNotConfigured as exc:
        raise PayoutRefused("not_configured") from exc
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise PayoutRefused("connection_failed") from exc        # never left this server
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and 400 <= exc.response.status_code < 500:
            raise PayoutRefused(f"rejected_{exc.response.status_code}") from exc
        raise PayoutUncertain(f"provider_error_{getattr(exc.response, 'status_code', '?')}") from exc
    except Exception as exc:  # noqa: BLE001 — read timeouts, dropped connections ...
        logger.error("b2c_payout_uncertain phone=%s: %s", phone, exc)
        raise PayoutUncertain(type(exc).__name__) from exc
    ack = payload.get("response") or {}
    if str(ack.get("ResponseCode", "0")) != "0":
        raise PayoutRefused(f"response_code_{ack.get('ResponseCode')}")
    return payload


def open_payout(db, tenant_id, loan_id, types=("b2c",)):
    return (db.query(PaymentTransaction)
            .filter(PaymentTransaction.tenant_id == tenant_id,
                    PaymentTransaction.loan_id == loan_id,
                    PaymentTransaction.type.in_(types),
                    PaymentTransaction.status.in_(OPEN_PAYOUT_STATES))
            .order_by(PaymentTransaction.id.desc()).first())


def _payout(phone: str, amount: float, remarks: str, creds=None) -> dict:
    """Legacy wrapper kept for callers outside this module: maps failures to
    HTTP errors without leaking provider internals (SEC-01)."""
    try:
        return _send_b2c(phone, amount, remarks, creds=creds)
    except PayoutRefused as exc:
        if str(exc) == "not_configured":
            raise HTTPException(422, "M-Pesa (Daraja) credentials are not configured")
        raise HTTPException(502, "The M-Pesa payout could not be completed. Please try again later.")
    except PayoutUncertain:
        raise HTTPException(502, "The M-Pesa payout outcome is unknown. Check M-Pesa before retrying.")


def execute_disbursement(db, tenant_id, loan: Loan, actor_user_id: int) -> dict:
    """Pay out an approved loan (durable intent — see the block comment above).

    MPESA-03: the approved -> processing transition is an atomic conditional
    UPDATE, so two concurrent requests can never both pay out the same loan.
    MPESA-01: the loan becomes `active` only when the b2c-result callback
    confirms ResultCode 0 (apply_b2c_result).
    F23: the amount sent is the loan's agreed payout (principal, less fees and
    excise duty when the product deducts them), from its pricing snapshot.

    COMMITS the session (twice): the payout record must outlive any later error
    in the caller."""
    from app.services import pricing

    earlier = open_payout(db, tenant_id, loan.id)
    if earlier is not None:
        raise HTTPException(409, "An earlier payout for this loan has not been confirmed yet "
                                 f"(transaction #{earlier.id}, {earlier.status}). Resolve it "
                                 "before paying out again.")
    updated = (
        db.query(Loan)
        .filter(Loan.id == loan.id, Loan.tenant_id == tenant_id, Loan.status == "approved")
        .update({Loan.status: "processing"}, synchronize_session=False)
    )
    db.flush()
    if not updated:
        raise HTTPException(409, "Loan is not in an approvable state (already processing/disbursed).")
    db.refresh(loan)

    snapshot = pricing.loan_snapshot(loan, loan.product)
    loan.pricing_snapshot = snapshot
    amount = money(snapshot["payout_amount"])
    if amount <= 0:
        db.rollback()
        raise HTTPException(422, "Fees and excise duty leave nothing to pay out on this loan")
    phone = loan.borrower.phone
    txn = PaymentTransaction(
        tenant_id=tenant_id, type="b2c", loan_id=loan.id, amount=amount, phone=phone,
        mpesa_ref=None, status="initiated",
        raw_payload={"actor_user_id": actor_user_id, "pricing": snapshot})
    db.add(txn)
    loan.disbursed_by_user_id = actor_user_id
    db.commit()                                     # 1. durable intent

    creds = mpesa.resolve_creds(db, tenant_id)
    try:
        payload = _send_b2c(phone, amount, f"Disbursement {loan.account_number}", creds=creds)
    except PayoutRefused as exc:
        txn.status = "failed"
        txn.raw_payload = {**(txn.raw_payload or {}), "refused": str(exc)}
        db.query(Loan).filter(Loan.id == loan.id, Loan.tenant_id == tenant_id,
                              Loan.status == "processing").update(
            {Loan.status: "approved"}, synchronize_session=False)
        db.commit()
        db.refresh(loan)
        if str(exc) == "not_configured":
            raise HTTPException(422, "M-Pesa (Daraja) credentials are not configured")
        raise HTTPException(502, "M-Pesa did not accept the payout. Nothing was sent; "
                                 "you can try again.")
    except PayoutUncertain as exc:
        txn.status = "uncertain"
        txn.raw_payload = {**(txn.raw_payload or {}), "uncertain": str(exc)}
        db.commit()
        log_money_event("disburse_uncertain", tenant_id=tenant_id, user_id=actor_user_id,
                        loan_id=loan.id, amount=amount, phone=phone, ref=f"TXN-{txn.id}",
                        detail=str(exc))
        raise HTTPException(502, "The payout outcome is unknown. Check the M-Pesa portal, then "
                                 f"resolve transaction #{txn.id} before trying again.")

    result = payload.get("result", {})
    conversation_id = result.get("ConversationID") or result.get("TransactionReceipt")
    originator_id = result.get("OriginatorConversationID")
    txn.status = "processing"
    txn.mpesa_ref = conversation_id
    txn.raw_payload = {**(txn.raw_payload or {}), **payload}
    db.commit()                                     # 3. stable correlation
    log_money_event("disburse", tenant_id=tenant_id, user_id=actor_user_id,
                    loan_id=loan.id, amount=amount, phone=phone, ref=conversation_id)
    return {
        "status": "processing",
        "conversation_id": conversation_id,
        "originator_id": originator_id,
        "amount": float(amount),
        "principal": float(loan.principal),
        "transaction_id": txn.id,
    }


SETTLEABLE_STATES = ("processing", "timed_out", "uncertain")


def apply_b2c_result(db, tenant_id, txn: PaymentTransaction, result_code, receipt,
                     raw: dict | None = None) -> dict:
    """Apply Safaricom's definitive B2C result (idempotent).

    F08: settles from `processing` AND from `timed_out` / `uncertain` — a
    timeout notice only means Safaricom's queue was slow, and the real result
    can still arrive afterwards (before WP5 it was silently ignored, leaving the
    loan stuck in `processing`). A repeat delivery is a no-op.

    Refund transactions (type "refund") are settled by apply_refund_result."""
    if txn.type == "refund":
        return apply_refund_result(db, tenant_id, txn, result_code, receipt, raw=raw)
    if txn.status not in SETTLEABLE_STATES:
        return {"applied": False, "reason": f"txn already {txn.status}", "txn_id": txn.id}

    loan = db.query(Loan).filter(Loan.id == txn.loan_id, Loan.tenant_id == tenant_id).first()
    success = str(result_code) == "0"
    if raw is not None:
        txn.raw_payload = {**(txn.raw_payload or {}), "result_callback": raw}

    if success:
        txn.status = "success"
        if receipt:
            txn.mpesa_ref = receipt
        if loan is not None and loan.status == "processing":
            from app.services import pricing
            snapshot = pricing.loan_snapshot(loan, loan.product)
            loan.pricing_snapshot = snapshot
            loan.status = "active"
            loan.disbursement_date = date.today()
            step = 7 if loan.product and loan.product.tenure_unit == "weeks" else 30
            tenure = loan.product.tenure_value if loan.product else 4
            loan.due_date = date.today() + timedelta(days=step * max(1, tenure))
            # F23: the agreed balance — principal + interest, plus fees and
            # excise duty when the product adds them to the balance.
            loan.fees_in_balance = money(snapshot.get("fees_in_balance", 0))
            loan.outstanding_balance = money(loan.total_due + loan.fees_in_balance)
            # Phase 2 — active-loan lock: freeze the borrower's locked identity
            # fields for the life of the obligation and open a lock-log entry.
            loan.active_lock = True
            if loan.borrower is not None:
                loan.borrower.edit_locked = True
                loan.borrower.edit_locked_reason = f"active_loan:{loan.id}"
            try:
                from app.models import LoanActiveLockLog
                db.add(LoanActiveLockLog(tenant_id=tenant_id, loan_id=loan.id,
                                         unlock_reason=None))
            except Exception:
                pass
            log_money_event("disburse_settled", tenant_id=tenant_id, loan_id=loan.id,
                            amount=money(txn.amount), ref=txn.mpesa_ref,
                            detail=f"outstanding={loan.outstanding_balance}")
            try:
                sms.sms_loan_disbursed(db, tenant_id, loan.borrower, loan)
            except Exception:
                pass
        return {"applied": True, "status": "success", "loan_status": loan.status if loan else None,
                "mpesa_ref": txn.mpesa_ref, "txn_id": txn.id}

    # Definite failure — revert the loan so it can be retried through the normal flow.
    txn.status = "failed"
    if loan is not None and loan.status == "processing":
        loan.status = "approved"
    return {"applied": True, "status": "failed", "loan_status": loan.status if loan else None,
            "txn_id": txn.id}


def mark_b2c_timeout(db, txn: PaymentTransaction, raw: dict | None = None) -> dict:
    """Handle a B2C QueueTimeOutURL delivery. The payout's fate is unknown, so we
    DON'T revert the loan (that would risk a double payout if it later settles);
    we flag the transaction as timed_out. A later result still settles it (F08)."""
    if txn.status != "processing":
        return {"applied": False, "reason": f"txn already {txn.status}", "txn_id": txn.id}
    txn.status = "timed_out"
    if raw is not None:
        txn.raw_payload = {**(txn.raw_payload or {}), "timeout_callback": raw}
    return {"applied": True, "status": "timed_out", "txn_id": txn.id}


def resolve_payout(db, tenant_id, txn: PaymentTransaction, outcome: str, receipt: str | None,
                   note: str, actor_user_id: int) -> dict:
    """A person who has checked the M-Pesa portal settles a payout whose result
    never arrived (initiated / uncertain / timed_out / long-processing).

    outcome "paid"     -> settled exactly as a success callback would (receipt required);
    outcome "not_paid" -> failed; the loan becomes retryable (or the refund re-opens)."""
    if txn.status not in OPEN_PAYOUT_STATES:
        raise HTTPException(409, f"Transaction is already {txn.status}")
    if outcome not in ("paid", "not_paid"):
        raise HTTPException(422, "outcome must be 'paid' or 'not_paid'")
    if not (note or "").strip():
        raise HTTPException(422, "A note recording what was checked on the M-Pesa portal is required")
    receipt = (receipt or "").strip().upper() or None
    if outcome == "paid" and not receipt:
        raise HTTPException(422, "The M-Pesa receipt of the payout is required")
    if txn.status == "initiated":
        txn.status = "uncertain"          # make it settleable through the normal path
    txn.raw_payload = {**(txn.raw_payload or {}),
                       "manual_resolution": {"outcome": outcome, "receipt": receipt,
                                             "note": note.strip(), "by": actor_user_id}}
    if txn.status == "processing":
        txn.status = "uncertain"
    out = apply_b2c_result(db, tenant_id, txn, "0" if outcome == "paid" else "MANUAL_NOT_PAID",
                           receipt)
    log_money_event("payout_resolved", tenant_id=tenant_id, user_id=actor_user_id,
                    loan_id=txn.loan_id, amount=money(txn.amount or 0), ref=receipt,
                    detail=f"txn={txn.id} outcome={outcome}")
    return out


# --------------------------------------------------------------------------- #
# Refunds (B2C) — same durable pattern; settled by the result callback
# --------------------------------------------------------------------------- #
def execute_refund(db, tenant_id, amount, phone: str, loan_id, actor_user_id: int,
                   reason: str | None = None, context: dict | None = None) -> dict:
    """Send a refund by B2C. The refund is `processing` until Safaricom's result
    callback settles it (before WP5 a live acknowledgement — ResponseCode 0,
    'request accepted' — was recorded as a completed refund).

    `context` is stored on the transaction (e.g. {"suspense_id": 12}) so the
    result can close the right suspense entry. COMMITS the session."""
    amount = money(amount)
    if amount <= 0:
        raise HTTPException(422, "Refund amount must be positive")
    # M-Pesa pays whole shillings: round UP so the payer is never short-changed
    # (e.g. an overpayment excess of 64.29 is refunded as 65; the DCP absorbs
    # under KES 1). The amount actually sent is what is recorded.
    amount = money(Decimal(math.ceil(amount)))
    txn = PaymentTransaction(
        tenant_id=tenant_id, type="refund", loan_id=loan_id, amount=amount, phone=phone,
        mpesa_ref=None, status="initiated",
        raw_payload={"actor_user_id": actor_user_id, "reason": reason, **(context or {})})
    db.add(txn)
    db.commit()                                     # durable intent
    creds = mpesa.resolve_creds(db, tenant_id)
    try:
        payload = _send_b2c(phone, amount, reason or "Refund", creds=creds)
    except PayoutRefused as exc:
        txn.status = "failed"
        txn.raw_payload = {**(txn.raw_payload or {}), "refused": str(exc)}
        _reopen_refund_source(db, tenant_id, txn, f"refund refused: {exc}")
        db.commit()
        if str(exc) == "not_configured":
            raise HTTPException(422, "M-Pesa (Daraja) credentials are not configured")
        raise HTTPException(502, "M-Pesa did not accept the refund. Nothing was sent.")
    except PayoutUncertain as exc:
        txn.status = "uncertain"
        txn.raw_payload = {**(txn.raw_payload or {}), "uncertain": str(exc)}
        db.commit()
        raise HTTPException(502, "The refund outcome is unknown. Check the M-Pesa portal, then "
                                 f"resolve transaction #{txn.id}.")
    result = payload.get("result", {})
    txn.status = "processing"
    txn.mpesa_ref = result.get("ConversationID") or result.get("TransactionReceipt")
    txn.raw_payload = {**(txn.raw_payload or {}), **payload}
    db.flush()
    log_money_event("refund", tenant_id=tenant_id, user_id=actor_user_id,
                    loan_id=loan_id, amount=amount, phone=phone,
                    ref=txn.mpesa_ref, detail=reason)
    if result.get("_simulated"):
        # Demo / sandbox without credentials: settle through the same path the
        # real result callback uses.
        apply_refund_result(db, tenant_id, txn, "0", mpesa._mpesa_ref())
    return {"mpesa_ref": txn.mpesa_ref, "amount": float(amount), "status": txn.status,
            "transaction_id": txn.id}


def _reopen_refund_source(db, tenant_id, txn, why: str):
    from app.models import SuspenseEntry
    sid = (txn.raw_payload or {}).get("suspense_id")
    if not sid:
        return
    entry = (db.query(SuspenseEntry)
             .filter(SuspenseEntry.id == sid, SuspenseEntry.tenant_id == tenant_id)
             .with_for_update().first())
    if entry is not None and entry.status in ("refund_processing", "pending_approval"):
        entry.status = "open"
        entry.raw_payload = {**(entry.raw_payload or {}), "refund_failed": why}


def apply_refund_result(db, tenant_id, txn: PaymentTransaction, result_code, receipt,
                        raw: dict | None = None) -> dict:
    """Settle a refund from Safaricom's result (idempotent)."""
    from app.models import SuspenseEntry
    if txn.status not in SETTLEABLE_STATES:
        return {"applied": False, "reason": f"txn already {txn.status}", "txn_id": txn.id}
    if raw is not None:
        txn.raw_payload = {**(txn.raw_payload or {}), "result_callback": raw}
    sid = (txn.raw_payload or {}).get("suspense_id")
    if str(result_code) == "0":
        txn.status = "success"
        if receipt:
            txn.mpesa_ref = receipt
        if sid:
            entry = (db.query(SuspenseEntry)
                     .filter(SuspenseEntry.id == sid, SuspenseEntry.tenant_id == tenant_id)
                     .with_for_update().first())
            if entry is not None:
                entry.status = "refunded"
                entry.resolved_at = datetime.utcnow()
        log_money_event("refund_settled", tenant_id=tenant_id, loan_id=txn.loan_id,
                        amount=money(txn.amount or 0), ref=txn.mpesa_ref)
        return {"applied": True, "status": "success", "txn_id": txn.id}
    txn.status = "failed"
    _reopen_refund_source(db, tenant_id, txn, f"refund failed: {result_code}")
    return {"applied": True, "status": "failed", "txn_id": txn.id}


# --------------------------------------------------------------------------- #
# F07 — refundable overpayment on a loan (legacy loan-refund path)
# --------------------------------------------------------------------------- #
def refundable_overpayment(db, tenant_id, loan: Loan, exclude_pending_id=None,
                           count_pending: bool = True) -> Decimal:
    """What can still be refunded on a loan: everything repaid, minus what the
    loan actually cost, minus refunds already sent (in progress or completed)
    and — when raising a new request — refunds awaiting approval. Before WP5
    none of these were subtracted, so two KES 200 requests could both be
    approved against one KES 200 overpayment.

    At APPROVAL time pass count_pending=False: only money already on its way
    counts, so the first request approved wins and the next is refused."""
    from sqlalchemy import func
    from app.models import PendingApproval, Repayment
    paid = money(db.query(func.coalesce(func.sum(Repayment.amount), 0))
                 .filter(Repayment.tenant_id == tenant_id, Repayment.loan_id == loan.id).scalar())
    owed = money(D(loan.total_due) + D(getattr(loan, "fees_in_balance", 0) or 0))
    q = (db.query(PendingApproval)
         .filter(PendingApproval.tenant_id == tenant_id, PendingApproval.loan_id == loan.id,
                 PendingApproval.action_type == "refund",
                 PendingApproval.status == "pending_approval"))
    if exclude_pending_id is not None:
        q = q.filter(PendingApproval.id != exclude_pending_id)
    pending = money(sum((D(p.amount) for p in q.all()), D(0))) if count_pending else D("0.00")
    sent = money(db.query(func.coalesce(func.sum(PaymentTransaction.amount), 0))
                 .filter(PaymentTransaction.tenant_id == tenant_id,
                         PaymentTransaction.loan_id == loan.id,
                         PaymentTransaction.type == "refund",
                         PaymentTransaction.status.in_(OPEN_PAYOUT_STATES + ("success",)))
                 .scalar())
    return money(max(paid - owed - pending - sent, D(0)))
