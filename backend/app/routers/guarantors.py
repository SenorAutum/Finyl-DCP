"""Guarantors: CRUD, documents and validation (Phase 2)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_module, require_permission, get_tenant_id, write_audit
from app.models import GUARANTOR_TYPES, LOAN_CATEGORIES
from app.schemas import (GuarantorCreate, GuarantorUpdate, GuarantorDocumentIn,
                         GuarantorValidateIn)
from app.services import guarantor as svc

router = APIRouter(prefix="/api/v1/guarantors", tags=["guarantors"])


@router.get("/meta")
def meta(tenant_id: int = Depends(require_module("lending"))):
    return {"guarantor_types": GUARANTOR_TYPES, "loan_categories": LOAN_CATEGORIES}


@router.get("")
def list_guarantors(client_id: int, tenant_id: int = Depends(require_module("lending")),
                    db: Session = Depends(get_db)):
    rows = svc.list_for_client(db, tenant_id=tenant_id, client_id=client_id)
    return {"items": [svc.to_dict(g) for g in rows]}


@router.post("")
def create_guarantor(body: GuarantorCreate,
                     tenant_id: int = Depends(require_module("lending")),
                     user=Depends(require_permission("guarantors.manage")),
                     db: Session = Depends(get_db), request: Request = None):
    if body.guarantor_type not in GUARANTOR_TYPES:
        raise HTTPException(400, "Invalid guarantor_type")
    try:
        g = svc.create(db, tenant_id=tenant_id, body=body, created_by=user.id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="guarantor.create",
                entity_type="guarantor", entity_id=g.id, request=request)
    db.commit()
    return svc.to_dict(g, include_docs=True)


@router.get("/{guarantor_id}")
def get_guarantor(guarantor_id: int, tenant_id: int = Depends(require_module("lending")),
                  db: Session = Depends(get_db)):
    g = svc.get(db, tenant_id=tenant_id, guarantor_id=guarantor_id)
    if not g:
        raise HTTPException(404, "Guarantor not found")
    return svc.to_dict(g, include_docs=True)


@router.put("/{guarantor_id}")
def update_guarantor(guarantor_id: int, body: GuarantorUpdate,
                     tenant_id: int = Depends(require_module("lending")),
                     user=Depends(require_permission("guarantors.manage")),
                     db: Session = Depends(get_db), request: Request = None):
    try:
        g = svc.update(db, tenant_id=tenant_id, guarantor_id=guarantor_id, body=body)
    except ValueError as e:
        raise HTTPException(404, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="guarantor.update",
                entity_type="guarantor", entity_id=guarantor_id, request=request)
    db.commit()
    return svc.to_dict(g, include_docs=True)


@router.delete("/{guarantor_id}")
def delete_guarantor(guarantor_id: int, tenant_id: int = Depends(require_module("lending")),
                     user=Depends(require_permission("guarantors.manage")),
                     db: Session = Depends(get_db), request: Request = None):
    if not svc.delete(db, tenant_id=tenant_id, guarantor_id=guarantor_id):
        raise HTTPException(404, "Guarantor not found")
    write_audit(db, tenant_id=tenant_id, user=user, action="guarantor.delete",
                entity_type="guarantor", entity_id=guarantor_id, request=request)
    db.commit()
    return {"status": "deleted"}


@router.get("/{guarantor_id}/documents")
def list_documents(guarantor_id: int, tenant_id: int = Depends(require_module("lending")),
                   db: Session = Depends(get_db)):
    g = svc.get(db, tenant_id=tenant_id, guarantor_id=guarantor_id)
    if not g:
        raise HTTPException(404, "Guarantor not found")
    return {"items": [svc.doc_dict(d) for d in g.documents]}


@router.post("/{guarantor_id}/documents")
def add_document(guarantor_id: int, body: GuarantorDocumentIn,
                 tenant_id: int = Depends(require_module("lending")),
                 user=Depends(require_permission("guarantors.manage")),
                 db: Session = Depends(get_db)):
    try:
        d = svc.add_document(db, tenant_id=tenant_id, guarantor_id=guarantor_id,
                             body=body, uploaded_by=user.id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return svc.doc_dict(d)


@router.delete("/{guarantor_id}/documents/{doc_id}")
def delete_document(guarantor_id: int, doc_id: int,
                    tenant_id: int = Depends(require_module("lending")),
                    user=Depends(require_permission("guarantors.manage")),
                    db: Session = Depends(get_db)):
    if not svc.delete_document(db, tenant_id=tenant_id, guarantor_id=guarantor_id, doc_id=doc_id):
        raise HTTPException(404, "Document not found")
    return {"status": "deleted"}


@router.post("/{guarantor_id}/validate")
def validate_guarantor(guarantor_id: int, body: GuarantorValidateIn | None = None,
                       tenant_id: int = Depends(require_module("lending")),
                       user=Depends(require_permission("guarantors.manage")),
                       db: Session = Depends(get_db), request: Request = None):
    body = body or GuarantorValidateIn()
    try:
        g = svc.validate(db, tenant_id=tenant_id, guarantor_id=guarantor_id,
                         run_mpesa=body.run_mpesa, run_ocr=body.run_ocr)
    except ValueError as e:
        raise HTTPException(404, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="guarantor.validate",
                entity_type="guarantor", entity_id=guarantor_id,
                details={"kyc_status": g.kyc_status}, request=request)
    db.commit()
    return svc.to_dict(g, include_docs=True)
