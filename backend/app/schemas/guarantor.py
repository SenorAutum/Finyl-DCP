"""Pydantic schemas for the guarantors domain (Phase 2)."""
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class GuarantorBusinessIn(BaseModel):
    business_name: str
    business_reg_no: Optional[str] = None
    operational_age_months: Optional[int] = None
    sector: Optional[str] = None
    directors: Optional[list] = None
    monthly_turnover: Optional[Decimal] = None
    business_address: Optional[str] = None


class GuarantorCreate(BaseModel):
    client_id: int
    full_name: str
    national_id: Optional[str] = None
    phone: Optional[str] = None
    relationship_type: Optional[str] = Field(default=None, alias="relationship")
    address: Optional[str] = None
    occupation: Optional[str] = None
    monthly_income: Optional[Decimal] = None
    guarantor_type: str = "personal"
    business: Optional[GuarantorBusinessIn] = None

    model_config = {"populate_by_name": True}


class GuarantorUpdate(BaseModel):
    full_name: Optional[str] = None
    national_id: Optional[str] = None
    phone: Optional[str] = None
    relationship_type: Optional[str] = Field(default=None, alias="relationship")
    address: Optional[str] = None
    occupation: Optional[str] = None
    monthly_income: Optional[Decimal] = None
    guarantor_type: Optional[str] = None
    active: Optional[bool] = None
    business: Optional[GuarantorBusinessIn] = None

    model_config = {"populate_by_name": True}


class GuarantorDocumentIn(BaseModel):
    doc_type: str
    file_name: str
    original_name: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    storage_path: str


class GuarantorValidateIn(BaseModel):
    """Trigger an M-Pesa name + (optional) OCR validation for a guarantor."""
    run_mpesa: bool = True
    run_ocr: bool = False
