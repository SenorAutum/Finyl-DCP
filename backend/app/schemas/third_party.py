"""Pydantic schemas for third-party API clients + ingestion (Phase 2)."""
from typing import Optional

from pydantic import BaseModel


class ApiClientCreate(BaseModel):
    client_name: str
    scopes: list[str]
    rate_limit_per_min: int = 60


class MpesaStatementIngestIn(BaseModel):
    client_id: int
    phone: Optional[str] = None
    statement: dict                     # raw statement payload / parsed transactions


class BankStatementIngestIn(BaseModel):
    client_id: int
    bank_name: str
    account_number: Optional[str] = None
    transactions: list
