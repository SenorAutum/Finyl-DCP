"""Guarantor CRUD, document upload and validation (Phase 2)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.core.crypto import pii_hash
from app.models import (Guarantor, GuarantorBusiness, GuarantorDocument, Borrower)


def _business_dict(b: GuarantorBusiness) -> dict:
    return {"id": b.id, "business_name": b.business_name,
            "business_reg_no": b.business_reg_no,
            "operational_age_months": b.operational_age_months, "sector": b.sector,
            "directors": b.directors,
            "monthly_turnover": float(b.monthly_turnover) if b.monthly_turnover else None,
            "business_address": b.business_address}


def to_dict(g: Guarantor, *, include_docs: bool = False) -> dict:
    out = {
        "id": g.id, "client_id": g.client_id, "full_name": g.full_name,
        "phone": g.phone, "relationship": g.relationship_type, "address": g.address,
        "occupation": g.occupation,
        "monthly_income": float(g.monthly_income) if g.monthly_income else None,
        "guarantor_type": g.guarantor_type, "mpesa_validated": g.mpesa_validated,
        "mpesa_validation_name": g.mpesa_validation_name,
        "kyc_status": g.kyc_status, "active": g.active, "created_at": g.created_at,
        "business": [_business_dict(b) for b in g.business] if g.business else [],
    }
    if include_docs:
        out["documents"] = [doc_dict(d) for d in g.documents]
    return out


def doc_dict(d: GuarantorDocument) -> dict:
    return {"id": d.id, "doc_type": d.doc_type, "file_name": d.file_name,
            "original_name": d.original_name, "mime_type": d.mime_type,
            "size_bytes": d.size_bytes, "storage_path": d.storage_path,
            "ocr_applied": d.ocr_applied, "uploaded_at": d.uploaded_at}


def create(db: Session, *, tenant_id: int, body, created_by: int | None = None) -> Guarantor:
    client = db.query(Borrower).filter(Borrower.id == body.client_id,
                                       Borrower.tenant_id == tenant_id).first()
    if not client:
        raise ValueError("client_not_found")
    g = Guarantor(
        tenant_id=tenant_id, client_id=body.client_id, full_name=body.full_name,
        national_id=body.national_id,
        national_id_hash=pii_hash(body.national_id) if body.national_id else None,
        phone=body.phone, relationship_type=body.relationship_type, address=body.address,
        occupation=body.occupation, monthly_income=body.monthly_income,
        guarantor_type=body.guarantor_type or "personal", created_by=created_by,
    )
    db.add(g)
    db.flush()
    if body.business and (body.guarantor_type == "business" or body.business.business_name):
        b = body.business
        db.add(GuarantorBusiness(
            tenant_id=tenant_id, guarantor_id=g.id, business_name=b.business_name,
            business_reg_no=b.business_reg_no, operational_age_months=b.operational_age_months,
            sector=b.sector, directors=b.directors, monthly_turnover=b.monthly_turnover,
            business_address=b.business_address,
        ))
    db.commit()
    db.refresh(g)
    return g


def get(db: Session, *, tenant_id: int, guarantor_id: int) -> Guarantor | None:
    return (db.query(Guarantor)
            .options(joinedload(Guarantor.business), joinedload(Guarantor.documents))
            .filter(Guarantor.id == guarantor_id, Guarantor.tenant_id == tenant_id).first())


def list_for_client(db: Session, *, tenant_id: int, client_id: int) -> list[Guarantor]:
    return (db.query(Guarantor)
            .options(joinedload(Guarantor.business))
            .filter(Guarantor.tenant_id == tenant_id, Guarantor.client_id == client_id)
            .order_by(Guarantor.created_at.desc()).all())


def update(db: Session, *, tenant_id: int, guarantor_id: int, body) -> Guarantor:
    g = get(db, tenant_id=tenant_id, guarantor_id=guarantor_id)
    if not g:
        raise ValueError("guarantor_not_found")
    for field in ("full_name", "phone", "relationship_type", "address", "occupation",
                  "monthly_income", "guarantor_type", "active"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(g, field, val)
    if getattr(body, "national_id", None) is not None:
        g.national_id = body.national_id
        g.national_id_hash = pii_hash(body.national_id)
    db.commit()
    db.refresh(g)
    return g


def delete(db: Session, *, tenant_id: int, guarantor_id: int) -> bool:
    g = (db.query(Guarantor)
         .filter(Guarantor.id == guarantor_id, Guarantor.tenant_id == tenant_id).first())
    if not g:
        return False
    db.delete(g)
    db.commit()
    return True


def add_document(db: Session, *, tenant_id: int, guarantor_id: int, body,
                 uploaded_by: int | None = None) -> GuarantorDocument:
    g = (db.query(Guarantor)
         .filter(Guarantor.id == guarantor_id, Guarantor.tenant_id == tenant_id).first())
    if not g:
        raise ValueError("guarantor_not_found")
    d = GuarantorDocument(
        tenant_id=tenant_id, guarantor_id=guarantor_id, doc_type=body.doc_type,
        file_name=body.file_name, original_name=body.original_name,
        mime_type=body.mime_type, size_bytes=body.size_bytes,
        storage_path=body.storage_path, uploaded_by=uploaded_by,
    )
    db.add(d)
    db.commit()
    return d


def delete_document(db: Session, *, tenant_id: int, guarantor_id: int, doc_id: int) -> bool:
    d = (db.query(GuarantorDocument)
         .filter(GuarantorDocument.id == doc_id,
                 GuarantorDocument.guarantor_id == guarantor_id,
                 GuarantorDocument.tenant_id == tenant_id).first())
    if not d:
        return False
    db.delete(d)
    db.commit()
    return True


def validate(db: Session, *, tenant_id: int, guarantor_id: int, run_mpesa=True,
             run_ocr=False) -> Guarantor:
    """Run guarantor validation (M-Pesa name check). Marks kyc_status accordingly."""
    g = get(db, tenant_id=tenant_id, guarantor_id=guarantor_id)
    if not g:
        raise ValueError("guarantor_not_found")
    if run_mpesa and g.phone:
        try:
            from app.services import mpesa
            name_fn = getattr(mpesa, "validate_name", None) or getattr(mpesa, "name_check", None)
            if callable(name_fn):
                nc = name_fn(db, tenant_id, g.phone) if name_fn.__code__.co_argcount >= 3 else name_fn(g.phone)
                reg = (nc or {}).get("name") if isinstance(nc, dict) else None
                if reg:
                    g.mpesa_validated = True
                    g.mpesa_validation_name = reg
                    g.mpesa_validated_at = datetime.now(timezone.utc)
        except Exception:
            pass
    g.kyc_status = "validated" if g.mpesa_validated else "pending"
    db.commit()
    db.refresh(g)
    return g
