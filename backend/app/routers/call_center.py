"""Call Center Tracker: interaction log + per-agent collections scorecard."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import get_current_user, require_module
from app.models import CallLog, CALL_OUTCOMES, Staff, User
from app.schemas import CallLogCreate
from app.services import analytics

router = APIRouter(prefix="/api/v1/call-center", tags=["call_center"])


@router.get("/meta")
def meta(tenant_id: int = Depends(require_module("call_center")), db: Session = Depends(get_db)):
    agents = db.query(Staff).filter(Staff.tenant_id == tenant_id, Staff.active).all()
    return {"outcomes": CALL_OUTCOMES,
            "agents": [{"id": s.id, "name": s.name, "role": s.role} for s in agents]}


@router.get("/calls")
def list_calls(tenant_id: int = Depends(require_module("call_center")), db: Session = Depends(get_db),
               agent_id: int = 0, outcome: str = "", page: int = 1, page_size: int = 20):
    q = (db.query(CallLog).options(joinedload(CallLog.agent), joinedload(CallLog.borrower))
         .filter(CallLog.tenant_id == tenant_id))
    if agent_id:
        q = q.filter(CallLog.agent_id == agent_id)
    if outcome:
        q = q.filter(CallLog.call_outcome == outcome)
    total = q.count()
    rows = q.order_by(CallLog.call_date.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "items": [{
        "id": c.id, "agent_id": c.agent_id, "agent_name": c.agent.name if c.agent else None,
        "borrower_id": c.borrower_id, "borrower_name": c.borrower.full_name if c.borrower else None,
        "loan_id": c.loan_id, "call_date": c.call_date, "duration_seconds": c.duration_seconds,
        "call_outcome": c.call_outcome, "promise_to_pay_date": c.promise_to_pay_date,
        "promise_amount": float(c.promise_amount) if c.promise_amount else None, "notes": c.notes,
    } for c in rows]}


@router.post("/calls")
def log_call(body: CallLogCreate, tenant_id: int = Depends(require_module("call_center")),
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if body.call_outcome not in CALL_OUTCOMES:
        raise HTTPException(400, f"Invalid outcome. Valid: {CALL_OUTCOMES}")
    # Resolve the agent: the logged-in user's linked staff record, else first active agent
    agent_id = user.staff_id
    if not agent_id:
        agent = db.query(Staff).filter(Staff.tenant_id == tenant_id,
                                       Staff.role == "call_agent", Staff.active).first()
        agent_id = agent.id if agent else None
    if not agent_id:
        raise HTTPException(400, "No call agent staff record available")
    c = CallLog(tenant_id=tenant_id, agent_id=agent_id, call_date=datetime.utcnow(),
                **body.model_dump())
    db.add(c)
    db.commit()
    return {"id": c.id}


@router.get("/scorecard")
def scorecard(tenant_id: int = Depends(require_module("call_center")), db: Session = Depends(get_db)):
    """Leaderboard: calls, avg talk time, Collection Efficiency (kept promises → M-Pesa repayments)."""
    return analytics.agent_scorecard(db, tenant_id)
