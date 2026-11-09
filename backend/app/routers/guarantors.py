"""Guarantors: records, documents and M-Pesa name confirmation.

WP10: every endpoint checks that the guarantor's client is in the caller's
data scope (the same rule as the client screens), documents are real uploads
stored by the server, and a guarantor's number is confirmed the same way as a
client's (POST /{id}/confirm-mpesa).
"""
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (UserScope, get_scope, require_module, require_permission,
                           write_audit)
from app.models import GUARANTOR_TYPES, LOAN_CATEGORIES, GuarantorDocument, User
from app.schemas import ConfirmMpesaIn, GuarantorCreate, GuarantorUpdate, GuarantorValidateIn
from app.services import guarantor as svc
from app.services import kyc_decisions, mpesa, mpesa_confirmation, storage
from app.services.authz import user_can
from app.core.ownership import require_client

router = APIRouter(prefix="/api/v1/guarantors", tags=["guarantors"])

# Reading guarantors needs the right to see clients (or to manage guarantors).
_can_view = require_permission("clients.view_all", "clients.view_portfolio",
                               "guarantors.manage", mode="any")
_can_manage = require_permission("guarantors.manage")

GUARANTOR_DOC_TYPES = ["national_id", "passport", "kra_pin", "payslip", "bank_statement",
                       "mpesa_statement", "business_permit", "photo", "consent_form", "other"]
_MIME_OK = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic",
            "text/csv", "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


@router.get("/meta")
def meta(tenant_id: int = Depends(require_module("lending")), _: User = Depends(_can_view),
         db: Session = Depends(get_db)):
    return {"guarantor_types": GUARANTOR_TYPES, "loan_categories": LOAN_CATEGORIES,
            "document_types": GUARANTOR_DOC_TYPES,
            "mpesa_confirmation_mode": kyc_decisions.settings(db, tenant_id).mpesa_confirmation_mode}


@router.get("")
def list_guarantors(client_id: int, tenant_id: int = Depends(require_module("lending")),
                    _: User = Depends(_can_view), scope: UserScope = Depends(get_scope),
                    db: Session = Depends(get_db)):
    rows = svc.list_for_client(db, tenant_id=tenant_id, client_id=client_id, scope=scope)
    return {"items": [svc.to_dict(g) for g in rows]}


@router.post("")
def create_guarantor(body: GuarantorCreate,
                     tenant_id: int = Depends(require_module("lending")),
                     user: User = Depends(_can_manage),
                     scope: UserScope = Depends(get_scope),
                     db: Session = Depends(get_db), request: Request = None):
    if body.guarantor_type not in GUARANTOR_TYPES:
        raise HTTPException(400, "Invalid guarantor_type")
    if not (body.full_name or "").strip():
        raise HTTPException(422, "Enter the guarantor's full name.")
    g = svc.create(db, tenant_id=tenant_id, body=body, scope=scope, created_by=user.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="guarantor.create",
                entity_type="guarantor", entity_id=g.id,
                details={"client_id": g.client_id}, request=request)
    db.commit()
    return svc.to_dict(svc.get(db, tenant_id=tenant_id, guarantor_id=g.id), include_docs=True)


@router.get("/{guarantor_id}")
def get_guarantor(guarantor_id: int, tenant_id: int = Depends(require_module("lending")),
                  _: User = Depends(_can_view), scope: UserScope = Depends(get_scope),
                  db: Session = Depends(get_db)):
    g = svc.get(db, tenant_id=tenant_id, guarantor_id=guarantor_id, scope=scope)
    return svc.to_dict(g, include_docs=True)


@router.put("/{guarantor_id}")
def update_guarantor(guarantor_id: int, body: GuarantorUpdate,
                     tenant_id: int = Depends(require_module("lending")),
                     user: User = Depends(_can_manage),
                     scope: UserScope = Depends(get_scope),
                     db: Session = Depends(get_db), request: Request = None):
    if body.guarantor_type is not None and body.guarantor_type not in GUARANTOR_TYPES:
        raise HTTPException(400, "Invalid guarantor_type")
    g, reset = svc.update(db, tenant_id=tenant_id, guarantor_id=guarantor_id, body=body,
                          scope=scope)
    write_audit(db, tenant_id=tenant_id, user=user, action="guarantor.update",
                entity_type="guarantor", entity_id=guarantor_id,
                details={"confirmation_reset": reset}, request=request)
    db.commit()
    return svc.to_dict(svc.get(db, tenant_id=tenant_id, guarantor_id=g.id), include_docs=True)


@router.delete("/{guarantor_id}")
def delete_guarantor(guarantor_id: int, tenant_id: int = Depends(require_module("lending")),
                     user: User = Depends(_can_manage),
                     scope: UserScope = Depends(get_scope),
                     db: Session = Depends(get_db), request: Request = None):
    paths = svc.delete(db, tenant_id=tenant_id, guarantor_id=guarantor_id, scope=scope)
    write_audit(db, tenant_id=tenant_id, user=user, action="guarantor.delete",
                entity_type="guarantor", entity_id=guarantor_id, request=request)
    db.commit()
    for p in paths:
        storage.delete_file(p)
    return {"status": "deleted"}


# ---- Documents -------------------------------------------------------------
@router.get("/{guarantor_id}/documents")
def list_documents(guarantor_id: int, tenant_id: int = Depends(require_module("lending")),
                   _: User = Depends(_can_view), scope: UserScope = Depends(get_scope),
                   db: Session = Depends(get_db)):
    g = svc.get(db, tenant_id=tenant_id, guarantor_id=guarantor_id, scope=scope)
    return {"items": [svc.doc_dict(d) for d in g.documents]}


@router.post("/{guarantor_id}/documents")
async def upload_documents(guarantor_id: int,
                           files: list[UploadFile] = File(...),
                           doc_types: str = Query("", description="Comma-separated doc_type per file"),
                           tenant_id: int = Depends(require_module("lending")),
                           user: User = Depends(_can_manage),
                           scope: UserScope = Depends(get_scope),
                           db: Session = Depends(get_db), request: Request = None):
    """WP10: real uploads. Before, the API accepted a file path typed by the
    caller and stored it without any file."""
    g = svc.get(db, tenant_id=tenant_id, guarantor_id=guarantor_id, scope=scope)
    types = [t.strip() for t in doc_types.split(",")] if doc_types else []
    saved, written = [], []
    try:
        for idx, up in enumerate(files):
            data = await up.read()
            if len(data) > storage.MAX_BYTES:
                raise HTTPException(413, f"'{up.filename}' exceeds the "
                                         f"{storage.settings.MAX_UPLOAD_MB}MB limit")
            if not data:
                continue
            doc_type = types[idx] if idx < len(types) and types[idx] in GUARANTOR_DOC_TYPES else "other"
            stored, path = storage.save_bytes(tenant_id, g.client_id,
                                              f"guarantor{g.id}_{up.filename or 'file'}", data)
            written.append(path)
            mime = (up.content_type or "").split(";")[0].strip().lower()
            row = GuarantorDocument(
                tenant_id=tenant_id, guarantor_id=g.id, doc_type=doc_type,
                file_name=stored, original_name=(up.filename or "file")[:500],
                mime_type=mime if mime in _MIME_OK else "application/octet-stream",
                size_bytes=len(data), storage_path=path, uploaded_by=user.id)
            db.add(row)
            saved.append(row)
        db.flush()
        write_audit(db, tenant_id=tenant_id, user=user, action="guarantor.documents_upload",
                    entity_type="guarantor", entity_id=g.id,
                    details={"count": len(saved), "types": [d.doc_type for d in saved]},
                    request=request)
        db.commit()
    except Exception:
        db.rollback()
        for p in written:
            storage.delete_file(p)
        raise
    return {"uploaded": len(saved), "documents": [svc.doc_dict(d) for d in saved]}


def _doc(db, tenant_id, g, doc_id) -> GuarantorDocument:
    d = (db.query(GuarantorDocument)
         .filter(GuarantorDocument.id == doc_id, GuarantorDocument.guarantor_id == g.id,
                 GuarantorDocument.tenant_id == tenant_id).first())
    if not d:
        raise HTTPException(404, "Document not found")
    return d


@router.get("/{guarantor_id}/documents/{doc_id}/download")
def download_document(guarantor_id: int, doc_id: int,
                      tenant_id: int = Depends(require_module("lending")),
                      _: User = Depends(_can_view), scope: UserScope = Depends(get_scope),
                      db: Session = Depends(get_db)):
    g = svc.get(db, tenant_id=tenant_id, guarantor_id=guarantor_id, scope=scope)
    d = _doc(db, tenant_id, g, doc_id)
    try:
        data = storage.read_bytes(d.storage_path)
    except OSError:
        raise HTTPException(404, "The file is missing from storage")
    safe = (d.original_name or "document").replace('"', "").replace("\r", "").replace("\n", "")
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"',
                             "X-Content-Type-Options": "nosniff"})


@router.delete("/{guarantor_id}/documents/{doc_id}")
def delete_document(guarantor_id: int, doc_id: int,
                    tenant_id: int = Depends(require_module("lending")),
                    user: User = Depends(_can_manage),
                    scope: UserScope = Depends(get_scope),
                    db: Session = Depends(get_db), request: Request = None):
    g = svc.get(db, tenant_id=tenant_id, guarantor_id=guarantor_id, scope=scope)
    d = _doc(db, tenant_id, g, doc_id)
    path = d.storage_path
    db.delete(d)
    write_audit(db, tenant_id=tenant_id, user=user, action="guarantor.document_delete",
                entity_type="guarantor", entity_id=g.id, details={"document_id": doc_id},
                request=request)
    db.commit()
    if path:
        storage.delete_file(path)
    return {"status": "deleted"}


# ---- M-Pesa ------------------------------------------------------------------
@router.post("/{guarantor_id}/validate")
def validate_guarantor(guarantor_id: int, body: GuarantorValidateIn | None = None,
                       tenant_id: int = Depends(require_module("lending")),
                       user: User = Depends(_can_manage),
                       scope: UserScope = Depends(get_scope),
                       db: Session = Depends(get_db)):
    """Checks the guarantor's number format only and reports whether the
    M-Pesa name has been confirmed. Nothing is marked validated here: Daraja
    gives ordinary partners no name lookup (see POST /{id}/confirm-mpesa)."""
    g = svc.get(db, tenant_id=tenant_id, guarantor_id=guarantor_id, scope=scope)
    if not g.phone:
        raise HTTPException(400, "The guarantor has no phone number.")
    resp = mpesa.validate_mobile_number(g.phone, "", "")["response"]
    return {"guarantor_id": g.id, "format_valid": bool(resp.get("FormatValid")),
            "msisdn": resp.get("MSISDN"), "verified": False,
            "mpesa_validated": svc.evidence_current(g), "kyc_status": g.kyc_status,
            "next_step": ("Confirmed." if svc.evidence_current(g) else
                          "Ask a colleague to confirm the name M-Pesa shows for this number.")}


@router.post("/{guarantor_id}/confirm-mpesa")
def confirm_mpesa(guarantor_id: int, body: ConfirmMpesaIn,
                  tenant_id: int = Depends(require_module("lending")),
                  user: User = Depends(require_permission("guarantors.manage",
                                                          "clients.approve", mode="any")),
                  scope: UserScope = Depends(get_scope),
                  db: Session = Depends(get_db), request: Request = None):
    """WP10: confirm the name M-Pesa holds for the guarantor's number, with
    evidence, under the DCP's M-Pesa confirmation rule (Validation Preferences):
    ``second_person`` (default) - someone with client-approval rights who did
    not capture the guarantor and does not own the client's portfolio;
    ``single_person`` - anyone who manages guarantors."""
    g = svc.get(db, tenant_id=tenant_id, guarantor_id=guarantor_id, scope=scope)
    client = require_client(db, tenant_id, g.client_id, scope)
    if not g.phone:
        raise HTTPException(400, "Add the guarantor's phone number first.")
    mode = kyc_decisions.settings(db, tenant_id).mpesa_confirmation_mode
    if mode != "single_person":
        if not user_can(db, user, "clients.approve"):
            raise HTTPException(403, "Your organisation requires a second staff member "
                                     "with client-approval rights to confirm M-Pesa names.")
        mpesa_confirmation.check_confirmer(user, created_by_user_id=g.created_by,
                                           officer_staff_id=client.officer_staff_id)
    elif not user_can(db, user, "guarantors.manage"):
        raise HTTPException(403, "Missing required permission: guarantors.manage")
    if body.phone and svc.normalise_phone(body.phone) != svc.normalise_phone(g.phone):
        raise HTTPException(409, "The number being confirmed is not the guarantor's current "
                                 "phone. Reload the page and try again.")
    reg, evidence = mpesa_confirmation.check_name_and_evidence(
        expected_name=g.full_name, registered_name=body.registered_name,
        evidence=body.evidence, who="guarantor")
    svc.confirm_mpesa(db, g, user=user, registered_name=reg, evidence=evidence)
    write_audit(db, tenant_id=tenant_id, user=user, action="guarantor.mpesa_confirmed",
                entity_type="guarantor", entity_id=g.id,
                details={"client_id": g.client_id, "registered_name": reg,
                         "phone_last4": (g.phone or "")[-4:], "evidence": evidence[:300],
                         "mode": mode}, request=request)
    db.commit()
    return svc.to_dict(svc.get(db, tenant_id=tenant_id, guarantor_id=g.id), include_docs=True)
