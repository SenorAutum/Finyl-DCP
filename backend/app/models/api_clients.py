"""Third-party API client model (migration 027).

API keys are shown once at creation; only a bcrypt hash + a 12-char prefix are
stored. The prefix is a unique lookup handle for authenticating inbound requests.
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, String)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base

API_SCOPES = ["statements.ingest", "kyc.read", "loans.read", "clients.read"]


class ThirdPartyApiClient(Base):
    __tablename__ = "third_party_api_clients"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    client_name = Column(String(200), nullable=False)
    api_key_hash = Column(String(500), nullable=False)          # bcrypt hash
    api_key_prefix = Column(String(12), nullable=False, index=True, unique=True)
    scopes = Column(JSONB, nullable=False, default=list)
    rate_limit_per_min = Column(Integer, nullable=False, default=60)
    active = Column(Boolean, nullable=False, default=True)
    last_used_at = Column(DateTime(timezone=True))
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
