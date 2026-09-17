"""Global multi-entity search (Phase 2)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission, get_scope
from app.services import search as search_service

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.get("")
def global_search(q: str = "", limit: int = 20,
                  user=Depends(require_permission("search.global")),
                  scope=Depends(get_scope), db: Session = Depends(get_db)):
    return search_service.search(db, tenant_id=user.tenant_id, scope=scope,
                                 q=q, limit=min(max(limit, 1), 50))
