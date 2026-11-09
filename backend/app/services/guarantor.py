"""Guarantor CRUD, documents and M-Pesa confirmation.

WP10: every function takes the guarantor's CLIENT through the same data-scope
check as the client screens (``app.core.ownership.require_client``), so an
officer only sees and changes guarantors of clients in their own portfolio.

A guarantor is "validated" only when a staff member has confirmed the name
M-Pesa holds for the guarantor's number, with evidence, under the same rules
as a client's number (``app.services.mpesa_confirmation``). Before WP10 the
validation called a name lookup that does not exist, so it never succeeded and
products that need a guarantor could not be used.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.crypto import pii_hash
from app.core.ownership import require_client
from app.models import Guarantor, GuarantorBusiness, GuarantorDocument
from app.services.sms import normalise_phone


def _business_dict(b: GuarantorBusiness) -> dict:
    return {"id": b.id, "business_name": b.business_name,
            "business_reg_no": b.business_reg_no,
            "operational_age_months": b.operational_age_months, "sector": b.sector,
            "directors": b.directors,
            "monthly_turnover": float(b.monthly_turnover) if b.monthly_turnover else None,
            "business_address": b.business_address}


def evidence_current(g: Guarantor) -> bool:
    """Confirmed by a person (or a real lookup) for the number the guarantor
    has NOW. A change of number invalidates it."""
    return bool(g.mpesa_validated
                and g.mpesa_validation_method in ("provider", "manual")
                and g.mpesa_validation_phone
                and normalise_phone(g.mpesa_validation_phone) == normalise_phone(g.phone or ""))


def to_dict(g: Guarantor, *, include_docs: bool = False) -> dict:
    out = {
        "id": g.id, "client_id": g.client_id, "full_name": g.full_name,
        "phone": g.phone, "relationship": g.relationship_type, "address": g.address,
        "occupation": g.occupation,
        "monthly_income": float(g.monthly_income) if g.monthly_income else None,
        "guarantor_type": g.guarantor_type,
        "mpesa_validated": evidence_current(g),
        "mpesa_validation_name": g.mpesa_validation_name,
        "mpesa_validation_method": g.mpesa_validation_method,
        "mpesa_validated_at": g.mpesa_validated_at,
        "kyc_status": g.kyc_status, "active": g.active, "created_at": g.created_at,
        "business": [_business_dict(b) for b in g.business] if g.business else [],
    }
    if include_docs:
        out["documents"] = [doc_dict(d) for d in g.documents]
    return out


def doc_dict(d: GuarantorDocument) -> dict:
    # storage_path is deliberately NOT returned: it is a server path.
    return {"id": d.id, "doc_type": d.doc_type, "file_name": d.file_name,
            "original_name": d.original_name, "mime_type": d.mime_type,
            "size_bytes": d.size_bytes, "ocr_applied": d.ocr_applied,
            "uploaded_at": d.uploaded_at}


def _clean_phone(phone: str | None) -> str | None:
    if phone is None or not str(phone).strip():
        return None
    n = normalise_phone(phone)
    if not (n.startswith("254") and len(n) == 12 and n.isdigit()):
        raise HTTPException(422, f"'{phone}' is not a valid Kenyan mobile number "
                                 "(for example 0712 345 678).")
    return n


def _not_the_client(client, *, national_id: str | None, phone: str | None) -> None:
    """A borrower cannot guarantee their own loan."""
    if national_id and client.national_id_hash and pii_hash(national_id) == client.national_id_hash:
        raise HTTPException(422, "The guarantor's National ID is the client's own. "
                                 "A client cannot guarantee their own loan.")
    if phone and client.phone and normalise_phone(phone) == normalise_phone(client.phone):
        raise HTTPException(422, "The guarantor's phone number is the client's own. "
                                 "Enter the guarantor's number.")


def _reset_confirmation(g: Guarantor) -> None:
    g.mpesa_validated = False
    g.mpesa_validation_method = None
    g.mpesa_validation_name = None
    g.mpesa_validation_phone = None
    g.mpesa_validated_by = None
    g.mpesa_validation_evidence = None
    g.mpesa_validated_at = None
    g.kyc_status = "pending"


def create(db: Session, *, tenant_id: int, body, scope=None,
           created_by: int | None = None) -> Guarantor:
    client = require_client(db, tenant_id, body.client_id, scope)
    phone = _clean_phone(body.phone)
    _not_the_client(client, national_id=body.national_id, phone=phone)
    g = Guarantor(
        tenant_id=tenant_id, client_id=client.id, full_name=body.full_name.strip(),
        national_id=body.national_id,
        national_id_hash=pii_hash(body.national_id) if body.national_id else None,
        phone=phone, relationship_type=body.relationship_type, address=body.address,
        occupation=body.occupation, monthly_income=body.monthly_income,
        guarantor_type=body.guarantor_type or "personal", created_by=created_by,
        kyc_status="pending", mpesa_validated=False,
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
    db.flush()
    return g


def get(db: Session, *, tenant_id: int, guarantor_id: int, scope=None) -> Guarantor:
    """The guarantor, if it is in the tenant and its client is in the caller's
    data scope. 404 (other tenant / missing) or 403 (outside scope)."""
    g = (db.query(Guarantor)
         .options(joinedload(Guarantor.business), joinedload(Guarantor.documents))
         .filter(Guarantor.id == guarantor_id, Guarantor.tenant_id == tenant_id).first())
    if not g:
        raise HTTPException(404, "Guarantor not found")
    require_client(db, tenant_id, g.client_id, scope)
    return g


def list_for_client(db: Session, *, tenant_id: int, client_id: int, scope=None) -> list[Guarantor]:
    require_client(db, tenant_id, client_id, scope)
    return (db.query(Guarantor)
            .options(joinedload(Guarantor.business))
            .filter(Guarantor.tenant_id == tenant_id, Guarantor.client_id == client_id)
            .order_by(Guarantor.created_at.desc()).all())


def update(db: Session, *, tenant_id: int, guarantor_id: int, body, scope=None) -> tuple[Guarantor, bool]:
    """Returns (guarantor, confirmation_reset). Changing the phone number or
    National ID removes the M-Pesa confirmation: it was for the old details."""
    g = get(db, tenant_id=tenant_id, guarantor_id=guarantor_id, scope=scope)
    client = require_client(db, tenant_id, g.client_id, scope)
    reset = False
    if body.phone is not None:
        phone = _clean_phone(body.phone)
        _not_the_client(client, national_id=None, phone=phone)
        if normalise_phone(phone or "") != normalise_phone(g.phone or ""):
            g.phone = phone
            reset = True
    if body.national_id is not None:
        _not_the_client(client, national_id=body.national_id, phone=None)
        new_hash = pii_hash(body.national_id) if body.national_id else None
        if new_hash != g.national_id_hash:
            g.national_id = body.national_id or None
            g.national_id_hash = new_hash
            reset = True
    if body.full_name is not None and body.full_name.strip() != (g.full_name or ""):
        g.full_name = body.full_name.strip()
        reset = True
    for field in ("relationship_type", "address", "occupation", "monthly_income",
                  "guarantor_type", "active"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(g, field, val)
    if reset and (g.mpesa_validated or g.kyc_status == "validated"):
        _reset_confirmation(g)
    else:
        reset = False
    db.flush()
    return g, reset


def delete(db: Session, *, tenant_id: int, guarantor_id: int, scope=None) -> list[str]:
    """Deletes the guarantor; returns the stored file paths to remove."""
    g = get(db, tenant_id=tenant_id, guarantor_id=guarantor_id, scope=scope)
    paths = [d.storage_path for d in g.documents if d.storage_path]
    db.delete(g)
    db.flush()
    return paths


def confirm_mpesa(db: Session, g: Guarantor, *, user, registered_name: str,
                  evidence: str) -> None:
    g.mpesa_validated = True
    g.mpesa_validation_method = "manual"
    g.mpesa_validation_name = registered_name
    g.mpesa_validation_phone = normalise_phone(g.phone or "")
    g.mpesa_validated_by = user.id
    g.mpesa_validation_evidence = evidence[:1000]
    g.mpesa_validated_at = datetime.now(timezone.utc)
    g.kyc_status = "validated"
    db.flush()
