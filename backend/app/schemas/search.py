"""Pydantic schemas for global search (Phase 2)."""
from typing import Optional

from pydantic import BaseModel


class SearchHit(BaseModel):
    entity_type: str          # client | loan | lead | staff | product
    id: int
    label: str
    sublabel: Optional[str] = None
    route: str                # frontend navigation route
    rank: Optional[float] = None


class SearchResponse(BaseModel):
    query: str
    total: int
    hits: list[SearchHit]
