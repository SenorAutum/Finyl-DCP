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
from datetime import date, timedelta

from fastapi import HTTPException

from app.core.money import money
from app.core.obs import log_money_event
from app.models import Loan, PaymentTransaction
from app.services import mpesa, sms

logger = logging.getLogger("finyl.payments")


def _payout(phone: str, amount: float, remarks: str, creds=None) -> dict:
    """Wrap the Daraja B2C call, mapping gating/errors to actionable HTTP codes.

    ``creds`` is the per-DCP resolved Daraja credential set (see mpesa.resolve_creds);
    when None the call falls back to the platform .env credentials.
    """
    try:
        return mpesa.b2c_disburse(phone, amount, remarks, creds=creds)
    except mpesa.DarajaNotConfigured:
        # SEC-01: do not leak provider/config internals to the caller.
        raise HTTPException(422, "M-Pesa (Daraja) credentials are not configured")
    except HTTPException:
        raise
    except Exception as exc:
        # SEC-01: log the underlying detail server-side; return a generic message.
        logger.error("b2c_payout_failed phone=%s remarks=%s: %s", phone, remarks, exc)
        raise HTTPException(502, "The M-Pesa payout could not be completed. Please try again later.")


def execute_disbursement(db, tenant_id, loan: Loan, actor_user_id: int) -> dict:
    """Initiate the Daraja B2C payout for an approved loan.

    MPESA-03 (TOCTOU): the approved → processing transition is an atomic,
    conditional UPDATE. Only the request that actually flips the row proceeds to
    move money, so two concurrent disbursements (e.g. the direct path racing the
    checker-approval path) can never both pay out the same loan.

    MPESA-01: the loan is left in `processing`; it becomes `active` only when the
    b2c-result webhook confirms ResultCode==0 (see apply_b2c_result).
    """
    # Atomic guard — flip approved → processing, proceed only if we won the race.
    updated = (
        db.query(Loan)
        .filter(Loan.id == loan.id, Loan.tenant_id == tenant_id, Loan.status == "approved")
        .update({Loan.status: "processing"}, synchronize_session=False)
    )
    db.flush()
    if not updated:
        raise HTTPException(409, "Loan is not in an approvable state (already processing/disbursed).")
    db.refresh(loan)  # sync in-memory ORM object with the committed status change

    creds = mpesa.resolve_creds(db, tenant_id)
    try:
        payload = _payout(loan.borrower.phone, float(loan.principal),
                          f"Disbursement {loan.account_number}", creds=creds)
    except Exception:
        # Payout could not even be accepted — revert the guard so the loan can be retried.
        db.query(Loan).filter(Loan.id == loan.id, Loan.tenant_id == tenant_id,
                              Loan.status == "processing").update(
            {Loan.status: "approved"}, synchronize_session=False)
        db.flush()
        db.refresh(loan)
        raise

    result = payload.get("result", {})
    conversation_id = result.get("ConversationID") or result.get("TransactionReceipt")
    originator_id = result.get("OriginatorConversationID")
    txn = PaymentTransaction(
        tenant_id=tenant_id, type="b2c", loan_id=loan.id, amount=loan.principal,
        phone=loan.borrower.phone,
        # Track the async request by ConversationID until the result carries the receipt.
        mpesa_ref=conversation_id,
        status="processing", raw_payload=payload,
    )
    db.add(txn)
    loan.disbursed_by_user_id = actor_user_id
    log_money_event("disburse", tenant_id=tenant_id, user_id=actor_user_id,
                    loan_id=loan.id, amount=money(loan.principal),
                    phone=loan.borrower.phone, ref=conversation_id)
    return {
        "status": "processing",
        "conversation_id": conversation_id,
        "originator_id": originator_id,
        "amount": float(loan.principal),
    }


def apply_b2c_result(db, tenant_id, txn: PaymentTransaction, result_code, receipt,
                     raw: dict | None = None) -> dict:
    """Apply a definitive B2C result to a processing payout (idempotent).

    Called by the b2c-result webhook and the reconcile sweep. Acts only while the
    transaction is still `processing`; a repeat delivery is a safe no-op.

    ResultCode 0  -> payout settled: txn success, loan activated (dates + balance).
    ResultCode !=0-> payout failed:  txn failed, loan reverted approved for retry.
    """
    if txn.status != "processing":
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
            loan.status = "active"
            loan.disbursement_date = date.today()
            step = 7 if loan.product and loan.product.tenure_unit == "weeks" else 30
            tenure = loan.product.tenure_value if loan.product else 4
            loan.due_date = date.today() + timedelta(days=step * max(1, tenure))
            loan.outstanding_balance = money(loan.total_due)  # MPESA-07
            log_money_event("disburse_settled", tenant_id=tenant_id, loan_id=loan.id,
                            amount=money(txn.amount), ref=txn.mpesa_ref,
                            detail=f"outstanding={loan.outstanding_balance}")
            try:
                sms.sms_loan_disbursed(db, tenant_id, loan.borrower, loan)
            except Exception:
                pass
        return {"applied": True, "status": "success", "loan_status": loan.status if loan else None,
                "mpesa_ref": txn.mpesa_ref, "txn_id": txn.id}

    # Failure — revert the loan so it can be retried through the normal flow.
    txn.status = "failed"
    if loan is not None and loan.status == "processing":
        loan.status = "approved"
    return {"applied": True, "status": "failed", "loan_status": loan.status if loan else None,
            "txn_id": txn.id}


def mark_b2c_timeout(db, txn: PaymentTransaction, raw: dict | None = None) -> dict:
    """Handle a B2C QueueTimeOutURL delivery. The payout's fate is unknown, so we
    DON'T revert the loan (that would risk a double payout if it later settles);
    we flag the transaction as timed_out for the reconcile sweep to resolve."""
    if txn.status != "processing":
        return {"applied": False, "reason": f"txn already {txn.status}", "txn_id": txn.id}
    txn.status = "timed_out"
    if raw is not None:
        txn.raw_payload = {**(txn.raw_payload or {}), "timeout_callback": raw}
    return {"applied": True, "status": "timed_out", "txn_id": txn.id}


def execute_refund(db, tenant_id, amount: float, phone: str, loan_id, actor_user_id: int,
                   reason: str | None = None) -> dict:
    """Run a Daraja B2C refund payout (credential-gated).

    Refund amount and destination phone are validated/derived by the router
    (MPESA-05) before this runs, so here we simply execute the payout.
    """
    creds = mpesa.resolve_creds(db, tenant_id)
    payload = _payout(phone, float(amount), reason or "Refund", creds=creds)
    result = payload.get("result", {})
    txn = PaymentTransaction(
        tenant_id=tenant_id, type="refund", loan_id=loan_id, amount=amount,
        phone=phone, mpesa_ref=result.get("TransactionReceipt") or result.get("ConversationID"),
        status="processing" if result.get("ResultCode") is None else "success",
        raw_payload=payload,
    )
    db.add(txn)
    log_money_event("refund", tenant_id=tenant_id, user_id=actor_user_id,
                    loan_id=loan_id, amount=money(amount), phone=phone,
                    ref=txn.mpesa_ref, detail=reason)
    return {"mpesa_ref": txn.mpesa_ref, "amount": float(amount),
            "status": txn.status}
