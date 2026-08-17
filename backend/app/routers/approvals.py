"""
Approvals inbox — client-profile approvals, loan approvals (with threshold /
escalation), and maker-checker money-movement sign-off. Each list is scoped to
what the approver is allowed to see; each action is permission-gated and audited.
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import (get_tenant_id, require_permission, get_scope, UserScope,
                           write_audit)
from app.models import Loan, Borrower, User, Staff, PendingApproval
from app.schemas import LoanDecision, ClientProfileDecision, ApprovalDecision
from app.services import rbac, sms
from app.services.disbursement import execute_disbursement, execute_refund

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


# ---------------------------------------------------------------------------
# Loan approvals
# ---------------------------------------------------------------------------
def _loan_row(l: Loan, limit) -> dict:
    return {
        "id": l.id, "account_number": l.account_number,
        "client_name": l.borrower.full_name if l.borrower else None,
        "principal": float(l.principal), "status": l.status,
        "branch_id": l.branch_id, "staff_id": l.staff_id,
        "escalation_level": l.escalation_level,
        "application_date": l.application_date.isoformat() if l.application_date else None,
        "approval_limit": limit,
        "over_limit": (limit is not None and float(l.principal) > limit),
    }


@router.get("/loans")
def pending_loans(tenant_id: int = Depends(get_tenant_id),
                  user: User = Depends(require_permission("loans.approve")),
                  scope: UserScope = Depends(get_scope),
                  db: Session = Depends(get_db)):
    q = (db.query(Loan).options(joinedload(Loan.borrower))
         .filter(Loan.tenant_id == tenant_id,
                 Loan.status.in_(["pending", "underwriting"])))
    q = scope.apply_loan(q, Loan)
    limit = rbac.loan_approval_limit(db, tenant_id, user)
    return [_loan_row(l, limit) for l in q.order_by(Loan.id.desc()).all()]


@router.post("/loans/{loan_id}")
def decide_loan(loan_id: int, body: LoanDecision, request: Request,
                tenant_id: int = Depends(get_tenant_id),
                user: User = Depends(require_permission("loans.approve")),
                scope: UserScope = Depends(get_scope),
                db: Session = Depends(get_db)):
    loan = (db.query(Loan).options(joinedload(Loan.borrower))
            .filter(Loan.id == loan_id, Loan.tenant_id == tenant_id).first())
    if not loan:
        raise HTTPException(404, "Loan not found")
    if not scope.can_see_loan(loan):
        raise HTTPException(403, "Loan is outside your scope")
    if loan.status not in ("pending", "underwriting"):
        raise HTTPException(400, f"Loan is '{loan.status}' and cannot be decided")

    # Per-DCP approver gate: the actor's role must be an enabled loan approver.
    if user.role != "super_admin" and not rbac.approver_enabled(db, tenant_id, "loan", user.role):
        raise HTTPException(
            403, "Your role is not configured as an approver for loan approvals at this DCP")

    action = body.action
    if action == "reject":
        loan.status = "rejected"
        loan.decision_note = body.note
        loan.approved_by_user_id = user.id
        write_audit(db, tenant_id=tenant_id, user=user, action="loan.reject",
                    entity_type="loan", entity_id=loan.id,
                    details={"note": body.note}, request=request)
        db.commit()
        return {"status": loan.status, "message": "Loan rejected"}

    if action == "escalate":
        level = rbac.next_escalation_level_for_tenant(db, tenant_id, user.role) or "hq"
        loan.escalation_level = level
        write_audit(db, tenant_id=tenant_id, user=user, action="loan.escalate",
                    entity_type="loan", entity_id=loan.id,
                    details={"to": level, "note": body.note}, request=request)
        db.commit()
        return {"status": loan.status, "escalation_level": level,
                "message": f"Escalated to {level.upper()}"}

    # --- approve --------------------------------------------------------------
    limit = rbac.loan_approval_limit(db, tenant_id, user)
    if limit is not None and float(loan.principal) > limit:
        level = rbac.next_escalation_level_for_tenant(db, tenant_id, user.role) or "hq"
        loan.escalation_level = level
        write_audit(db, tenant_id=tenant_id, user=user, action="loan.escalate_auto",
                    entity_type="loan", entity_id=loan.id,
                    details={"principal": float(loan.principal), "limit": limit, "to": level},
                    request=request)
        db.commit()
        raise HTTPException(
            409,
            f"Amount KES {float(loan.principal):,.0f} exceeds your approval limit "
            f"of KES {limit:,.0f} — auto-escalated to {level.upper()}.",
        )
    loan.status = "approved"
    loan.approval_date = date.today()
    loan.approved_by_user_id = user.id
    loan.escalation_level = None
    loan.decision_note = body.note
    write_audit(db, tenant_id=tenant_id, user=user, action="loan.approve",
                entity_type="loan", entity_id=loan.id,
                details={"principal": float(loan.principal), "limit": limit}, request=request)
    # Notify the borrower that the loan has qualified (before disbursement).
    try:
        if loan.borrower:
            sms.sms_loan_qualified(db, tenant_id, loan.borrower, loan)
    except Exception:
        pass
    db.commit()
    return {"status": loan.status,
            "message": "Loan approved — awaiting disbursement by a Disbursement Officer."}


# ---------------------------------------------------------------------------
# Client-profile approvals
# ---------------------------------------------------------------------------
@router.get("/clients")
def pending_clients(tenant_id: int = Depends(get_tenant_id),
                    user: User = Depends(require_permission("clients.approve")),
                    scope: UserScope = Depends(get_scope),
                    db: Session = Depends(get_db)):
    q = db.query(Borrower).filter(Borrower.tenant_id == tenant_id,
                                  Borrower.profile_status == "pending_approval")
    q = scope.apply_client(q, Borrower)
    rows = q.order_by(Borrower.id.desc()).all()
    return [{"id": c.id, "name": c.full_name, "national_id": c.national_id,
             "phone": c.phone, "branch_id": c.branch_id,
             "officer_staff_id": c.officer_staff_id,
             "kyc_status": c.kyc_status, "profile_status": c.profile_status}
            for c in rows]


@router.post("/clients/{client_id}")
def decide_client(client_id: int, body: ClientProfileDecision, request: Request,
                  tenant_id: int = Depends(get_tenant_id),
                  user: User = Depends(require_permission("clients.approve")),
                  scope: UserScope = Depends(get_scope),
                  db: Session = Depends(get_db)):
    c = db.query(Borrower).filter(Borrower.id == client_id, Borrower.tenant_id == tenant_id).first()
    if not c:
        raise HTTPException(404, "Client not found")
    if not scope.can_see_client(c):
        raise HTTPException(403, "Client is outside your scope")
    if user.role != "super_admin" and not rbac.approver_enabled(db, tenant_id, "client", user.role):
        raise HTTPException(
            403, "Your role is not configured as an approver for client-profile approvals at this DCP")
    c.profile_status = "approved" if body.action == "approve" else "rejected"
    if body.action == "approve":
        c.approved_by_user_id = user.id
    write_audit(db, tenant_id=tenant_id, user=user, action=f"client.{body.action}",
                entity_type="client", entity_id=c.id, details={"note": body.note}, request=request)
    db.commit()
    return {"id": c.id, "profile_status": c.profile_status}


# ---------------------------------------------------------------------------
# Maker-checker money movement
# ---------------------------------------------------------------------------
@router.get("/pending-actions")
def pending_actions(tenant_id: int = Depends(get_tenant_id),
                    user: User = Depends(require_permission("disburse.approve", "refund.approve", mode="any")),
                    db: Session = Depends(get_db)):
    rows = (db.query(PendingApproval)
            .filter(PendingApproval.tenant_id == tenant_id,
                    PendingApproval.status == "pending_approval")
            .order_by(PendingApproval.id.desc()).all())
    out = []
    for p in rows:
        maker = db.get(User, p.maker_user_id)
        out.append({"id": p.id, "action_type": p.action_type, "loan_id": p.loan_id,
                    "amount": float(p.amount), "phone": p.phone, "reason": p.reason,
                    "maker_email": maker.email if maker else None,
                    "maker_user_id": p.maker_user_id,
                    "maker_at": p.maker_at.isoformat() if p.maker_at else None,
                    "is_own": p.maker_user_id == user.id})
    return out


@router.post("/pending-actions/{pid}")
def decide_pending_action(pid: int, body: ApprovalDecision, request: Request,
                          tenant_id: int = Depends(get_tenant_id),
                          user: User = Depends(require_permission("disburse.approve", "refund.approve", mode="any")),
                          db: Session = Depends(get_db)):
    p = db.query(PendingApproval).filter(PendingApproval.id == pid, PendingApproval.tenant_id == tenant_id).first()
    if not p or p.status != "pending_approval":
        raise HTTPException(404, "Pending action not found")
    # maker-checker: the initiator cannot approve their own action
    if p.maker_user_id == user.id:
        raise HTTPException(403, "The initiating user cannot approve their own action (maker-checker)")
    # permission must match the action type
    from app.core.permissions import has_permission
    need = "disburse.approve" if p.action_type == "disbursement" else "refund.approve"
    if not has_permission(user.role, need):
        raise HTTPException(403, f"Missing required permission: {need}")
    # Per-DCP approver gate for the money-movement action type.
    appr_type = "disbursement" if p.action_type == "disbursement" else "refund"
    if user.role != "super_admin" and not rbac.approver_enabled(db, tenant_id, appr_type, user.role):
        raise HTTPException(
            403, f"Your role is not configured as an approver for {appr_type} approvals at this DCP")

    if body.action == "reject":
        p.status = "rejected"; p.checker_user_id = user.id; p.checker_at = datetime.utcnow()
        write_audit(db, tenant_id=tenant_id, user=user, action=f"{p.action_type}.reject",
                    entity_type="pending_approval", entity_id=p.id, request=request)
        db.commit()
        return {"status": p.status}

    # approve → execute the side-effect
    if p.action_type == "disbursement":
        loan = (db.query(Loan).options(joinedload(Loan.borrower), joinedload(Loan.product))
                .filter(Loan.id == p.loan_id, Loan.tenant_id == tenant_id).first())
        if not loan:
            raise HTTPException(404, "Loan not found")
        result = execute_disbursement(db, tenant_id, loan, user.id)
    else:
        result = execute_refund(db, tenant_id, float(p.amount), p.phone, p.loan_id, user.id, p.reason)
    p.status = "approved"; p.checker_user_id = user.id; p.checker_at = datetime.utcnow()
    p.details = {**(p.details or {}), "result": result}
    write_audit(db, tenant_id=tenant_id, user=user, action=f"{p.action_type}.approve",
                entity_type="pending_approval", entity_id=p.id, details=result, request=request)
    db.commit()
    return {"status": p.status, "result": result}
