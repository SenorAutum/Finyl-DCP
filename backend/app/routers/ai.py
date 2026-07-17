"""AI Financial Health Agent endpoint (tenant_admin / super_admin only)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_module, require_role
from app.schemas import AiChatRequest
from app.services import ai_agent

router = APIRouter(prefix="/api/v1/ai", tags=["ai_agent"],
                   dependencies=[Depends(require_role("tenant_admin"))])


@router.post("/chat")
def chat(body: AiChatRequest, tenant_id: int = Depends(require_module("ai_agent")),
         db: Session = Depends(get_db)):
    if not body.message.strip():
        raise HTTPException(400, "Empty message")
    try:
        answer = ai_agent.chat(db, tenant_id, body.message, body.history)
    except Exception as exc:  # LLM/network failure — degrade gracefully
        raise HTTPException(502, f"AI agent unavailable: {exc}")
    return {"answer": answer}
