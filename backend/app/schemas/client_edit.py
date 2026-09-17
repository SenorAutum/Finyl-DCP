"""Pydantic schemas for the client-edit maker-checker workflow (Phase 2)."""
from typing import Optional

from pydantic import BaseModel


class ClientEditRequestIn(BaseModel):
    edit_tier: str = "secondary"        # primary | secondary
    # {field: {"old_value": ..., "new_value": ...}}
    field_changes: dict
    supporting_docs: Optional[list] = None


class ClientEditRejectIn(BaseModel):
    rejection_reason: str
