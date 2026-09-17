"""Third-party API clients + external ingestion endpoints (Phase 2).

Management endpoints (`/api/v1/api-clients`) are authenticated as a logged-in
tenant admin. Ingestion endpoints (`/api/v1/ingestion/*`) are authenticated with
an X-Api-Key issued to an external system (no user session).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (require_module, require_permission, get_api_client,
                           write_audit)
from app.models import ThirdPartyApiClient
from app.schemas import (ApiClientCreate, MpesaStatementIngestIn, BankStatementIngestIn)
from app.services import third_party_auth
from app.services import bank_statement as bank_service

router = APIRouter(prefix="/api/v1/api-clients", tags=["api-clients"])


def _client_dict(c: ThirdPartyApiClient) -> dict:
    return {"id": c.id, "client_name": c.client_name, "api_key_prefix": c.api_key_prefix,
            "scopes": c.scopes, "rate_limit_per_min": c.rate_limit_per_min,
            "active": c.active, "last_used_at": c.last_used_at, "created_at": c.created_at}


@router.post("")
def create_api_client(body: ApiClientCreate,
                      tenant_id: int = Depends(require_module("lending")),
                      user=Depends(require_permission("api_clients.manage")),
                      db: Session = Depends(get_db), request: Request = None):
    row, full_key = third_party_auth.create_client(
        db, tenant_id=tenant_id, client_name=body.client_name, scopes=body.scopes,
        rate_limit_per_min=body.rate_limit_per_min, created_by=user.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="api_clients.create",
                entity_type="api_client", entity_id=row.id,
                details={"client_name": body.client_name, "scopes": body.scopes},
                request=request)
    db.commit()
    out = _client_dict(row)
    out["api_key"] = full_key  # shown exactly once
    return out


@router.get("")
def list_api_clients(tenant_id: int = Depends(require_module("lending")),
                     user=Depends(require_permission("api_clients.manage")),
                     db: Session = Depends(get_db)):
    rows = (db.query(ThirdPartyApiClient)
            .filter(ThirdPartyApiClient.tenant_id == tenant_id)
            .order_by(ThirdPartyApiClient.created_at.desc()).all())
    return {"items": [_client_dict(c) for c in rows]}


@router.delete("/{client_id}")
def revoke_api_client(client_id: int,
                      tenant_id: int = Depends(require_module("lending")),
                      user=Depends(require_permission("api_clients.manage")),
                      db: Session = Depends(get_db), request: Request = None):
    row = (db.query(ThirdPartyApiClient)
           .filter(ThirdPartyApiClient.id == client_id,
                   ThirdPartyApiClient.tenant_id == tenant_id).first())
    if not row:
        raise HTTPException(404, "API client not found")
    row.active = False
    write_audit(db, tenant_id=tenant_id, user=user, action="api_clients.revoke",
                entity_type="api_client", entity_id=client_id, request=request)
    db.commit()
    return {"id": client_id, "active": False}


# --- External ingestion (X-Api-Key authenticated) --------------------------
ingestion_router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])


@ingestion_router.post("/mpesa-statement")
def ingest_mpesa_statement(body: MpesaStatementIngestIn,
                           client=Depends(get_api_client),
                           db: Session = Depends(get_db)):
    if not third_party_auth.has_scope(client, "mpesa_statement"):
        raise HTTPException(403, "API key missing 'mpesa_statement' scope")
    txns = body.statement.get("transactions") if isinstance(body.statement, dict) else None
    row = bank_service.ingest(
        db, tenant_id=client.tenant_id, client_id=body.client_id,
        bank_name="M-Pesa", transactions=txns or [],
        account_number=body.phone, source_filename="mpesa_ingestion")
    return {"ingested": True, "statement_id": row.id,
            "affordability_score": float(row.affordability_score or 0)}


@ingestion_router.post("/bank-statement")
def ingest_bank_statement_ext(body: BankStatementIngestIn,
                              client=Depends(get_api_client),
                              db: Session = Depends(get_db)):
    if not third_party_auth.has_scope(client, "bank_statement"):
        raise HTTPException(403, "API key missing 'bank_statement' scope")
    row = bank_service.ingest(
        db, tenant_id=client.tenant_id, client_id=body.client_id,
        bank_name=body.bank_name, transactions=body.transactions or [],
        account_number=body.account_number, source_filename="api_ingestion")
    return {"ingested": True, "statement_id": row.id,
            "affordability_score": float(row.affordability_score or 0)}
