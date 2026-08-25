"""
Maker-checker approvals state machine (money-movement sign-off).

Exercises app.routers.approvals.decide_pending_action directly (no HTTP/JWT):
  * Separation of Duties — the maker cannot approve their own action (403).
  * A valid checker approval executes the disbursement side-effect exactly once
    and moves the pending action to a terminal `approved` state.
  * Reject moves the action to terminal `rejected` and leaves the loan untouched.
  * Terminal actions cannot be re-actioned (404 — no longer pending_approval).
  * services.rbac.requires_maker_checker fails CLOSED when no threshold is set,
    and honours a configured threshold when one exists.

All external I/O (Daraja B2C) is stubbed by the `mock_b2c` fixture, so no
network is ever touched and the payout count is asserted deterministically.
"""
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.models import PendingApproval, PaymentTransaction, ApprovalThreshold
from app.routers.approvals import decide_pending_action
from app.schemas import ApprovalDecision
from app.services.rbac import requires_maker_checker


def _pending_disbursement(seed, loan):
    """A disbursement pending-action initiated by the maker."""
    p = PendingApproval(
        tenant_id=seed.tenant.id,
        action_type="disbursement",
        loan_id=loan.id,
        amount=loan.principal,
        phone=seed.borrower.phone,
        reason="disburse approved loan",
        status="pending_approval",
        maker_user_id=seed.maker.id,
        maker_at=datetime.utcnow(),
    )
    seed.db.add(p)
    seed.db.commit()
    return p


def test_sod_maker_cannot_approve_own_action(seed, mock_b2c):
    """Separation of Duties: the initiating user (maker) approving their own
    action must be rejected with 403, and no payout may occur."""
    loan = seed.make_loan(status="approved", principal="10000.00")
    p = _pending_disbursement(seed, loan)

    with pytest.raises(HTTPException) as exc:
        decide_pending_action(
            p.id, ApprovalDecision(action="approve"), None,
            tenant_id=seed.tenant.id, db=seed.db, user=seed.maker,
        )
    assert exc.value.status_code == 403
    assert "maker-checker" in str(exc.value.detail).lower()
    # No side-effect: no payout, loan still approved, action still pending.
    assert mock_b2c.calls == []
    seed.db.refresh(p)
    seed.db.refresh(loan)
    assert p.status == "pending_approval"
    assert loan.status == "approved"


def test_valid_checker_approval_executes_disbursement_once(seed, mock_b2c):
    """A different user with the approver role approves → the disbursement
    side-effect runs exactly once and the action becomes terminal `approved`."""
    loan = seed.make_loan(status="approved", principal="10000.00")
    p = _pending_disbursement(seed, loan)

    result = decide_pending_action(
        p.id, ApprovalDecision(action="approve"), None,
        tenant_id=seed.tenant.id, db=seed.db, user=seed.checker,
    )
    assert result["status"] == "approved"

    seed.db.refresh(p)
    seed.db.refresh(loan)
    assert p.status == "approved"
    assert p.checker_user_id == seed.checker.id
    # Disbursement executed exactly once: loan moved to processing, one payout,
    # exactly one b2c PaymentTransaction created.
    assert loan.status == "processing"
    assert len(mock_b2c.calls) == 1
    txns = (seed.db.query(PaymentTransaction)
            .filter(PaymentTransaction.loan_id == loan.id,
                    PaymentTransaction.type == "b2c").all())
    assert len(txns) == 1
    assert txns[0].status == "processing"


def test_reject_is_terminal_and_leaves_loan_untouched(seed, mock_b2c):
    loan = seed.make_loan(status="approved", principal="10000.00")
    p = _pending_disbursement(seed, loan)

    result = decide_pending_action(
        p.id, ApprovalDecision(action="reject"), None,
        tenant_id=seed.tenant.id, db=seed.db, user=seed.checker,
    )
    assert result["status"] == "rejected"

    seed.db.refresh(p)
    seed.db.refresh(loan)
    assert p.status == "rejected"
    assert p.checker_user_id == seed.checker.id
    assert loan.status == "approved"          # unchanged
    assert mock_b2c.calls == []               # no payout on reject


def test_terminal_action_cannot_be_reactioned(seed, mock_b2c):
    """Once approved (or rejected) the action is terminal — a second decide on
    the same id must 404 (it is no longer pending_approval)."""
    loan = seed.make_loan(status="approved", principal="10000.00")
    p = _pending_disbursement(seed, loan)

    decide_pending_action(
        p.id, ApprovalDecision(action="approve"), None,
        tenant_id=seed.tenant.id, db=seed.db, user=seed.checker,
    )
    payouts_after_first = len(mock_b2c.calls)

    with pytest.raises(HTTPException) as exc:
        decide_pending_action(
            p.id, ApprovalDecision(action="approve"), None,
            tenant_id=seed.tenant.id, db=seed.db, user=seed.checker,
        )
    assert exc.value.status_code == 404
    # No extra payout from the rejected re-action.
    assert len(mock_b2c.calls) == payouts_after_first


def test_requires_maker_checker_fails_closed_without_threshold(seed):
    """MPESA-06: with NO threshold configured, ALL money movement requires a
    second approver (fail closed)."""
    assert requires_maker_checker(seed.db, seed.tenant.id, "disbursement", 100.0) is True
    assert requires_maker_checker(seed.db, seed.tenant.id, "disbursement", 999999.0) is True


def test_requires_maker_checker_honours_configured_threshold(seed):
    """With a threshold row, only amounts strictly above the limit are parked."""
    seed.db.add(ApprovalThreshold(
        tenant_id=seed.tenant.id, scope_type="tenant", scope_key="all",
        threshold_type="disbursement", amount=50000,
    ))
    seed.db.commit()

    assert requires_maker_checker(seed.db, seed.tenant.id, "disbursement", 100.0) is False
    assert requires_maker_checker(seed.db, seed.tenant.id, "disbursement", 50000.0) is False   # not strictly above
    assert requires_maker_checker(seed.db, seed.tenant.id, "disbursement", 60000.0) is True
