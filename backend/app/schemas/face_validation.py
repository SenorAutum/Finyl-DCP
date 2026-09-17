"""Pydantic schemas for face-validation (Phase 2)."""
from typing import Optional

from pydantic import BaseModel


class FaceValidateIn(BaseModel):
    id_image_path: Optional[str] = None
    selfie_image_path: Optional[str] = None
    provider: Optional[str] = None      # override tenant default provider
