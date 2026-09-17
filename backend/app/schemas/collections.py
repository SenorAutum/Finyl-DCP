"""Pydantic schemas for collections: PTP, bank statements, Ratiba (Phase 2)."""
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class PtpCreate(BaseModel):
    loan_id: int
    ptp_date: date
    amount: Decimal
    contact_method: str = "call"
    notes: Optional[str] = None


class PtpUpdate(BaseModel):
    status: Optional[str] = None            # honored | broken | partial | rescheduled
    honored_amount: Optional[Decimal] = None
    ptp_date: Optional[date] = None
    notes: Optional[str] = None


class BankStatementIn(BaseModel):
    client_id: int
    bank_name: str
    account_number: Optional[str] = None
    source_filename: Optional[str] = None
    # Either supply raw transactions for parsing, or a pre-computed summary.
    transactions: Optional[list] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None


class RatibaConsentIn(BaseModel):
    loan_id: int
    phone: str
    consent_given: bool = True
    deduction_amount: Optional[Decimal] = None
    deduction_day: Optional[int] = None


class RatibaInitiateIn(BaseModel):
    loan_id: int
