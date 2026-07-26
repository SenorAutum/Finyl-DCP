"""
Client registry & KYC onboarding.

Canonical prefix: /api/v1/clients
Alias prefix:     /api/v1/borrowers  (kept so previously built integrations keep
                  working — the DB table is still `borrowers` for join stability)

Covers:
  * CRUD with the nested Mobile Wallet / Next of Kin collections
  * document upload / list / download / delete (any file type)
  * "Process ID"  → local Tesseract OCR of ID front+back, merged into form fields
  * "eKYC"        → external identity-verification provider (annotated mock)
  * "Validate M-Pesa" → Safaricom name lookup against the National ID (annotated mock)

Every endpoint inherits the platform's JWT auth, tenant scoping and the
`lending` feature-flag gate via `require_module("lending")`.
"""
import os
from datetime import date, datetime

from fastapi import (APIRouter, Depends, File, HTTPException, Query, Response,
                     UploadFile)
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import get_current_user, require_module
from app.models import (Borrower, ClientDocument, ClientMobileWallet,
                        ClientNextOfKin, DOC_TYPES, Loan, ImpactSurvey,
                        NEXT_OF_KIN_RELATIONSHIPS, PaymentTransaction, User,
                        WALLET_OPERATORS)
from app.schemas import ClientCreate, EkycVerifyRequest, ValidateMpesaRequest
from app.services import ekyc, mpesa, storage
from app.services.ocr import OcrUnavailable, process_id_files

MAX_BYTES = storage.MAX_BYTES
OCR_MIMES = ("image/jpeg", "image/jpg", "image/png", "image/webp", "application/pdf")


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
def _wallet_dict(w: ClientMobileWallet) -> dict:
    return {"id": w.id, "mobile_number": w.mobile_number, "wallet_number": w.wallet_number,
            "operator": w.operator, "active": bool(w.active)}


def _nok_dict(n: ClientNextOfKin) -> dict:
    return {"id": n.id, "full_name": n.full_name, "relationship": n.relationship_type,
            "mobile_number": n.mobile_number, "national_id": n.national_id,
            "address": n.address, "active": bool(n.active)}


def _doc_dict(d: ClientDocument) -> dict:
    return {"id": d.id, "file_name": d.file_name, "original_name": d.original_name,
            "mime_type": d.mime_type, "size_bytes": d.size_bytes, "doc_type": d.doc_type,
            "ocr_applied": bool(d.ocr_applied), "uploaded_at": d.uploaded_at,
            "uploaded_by": d.uploaded_by,
            "is_image": (d.mime_type or "").startswith("image/")}


def _client_dict(b: Borrower, loan_count: int | None = None, nested: bool = False) -> dict:
    out = {
        "id": b.id, "full_name": b.full_name, "first_name": b.first_name,
        "middle_name": b.middle_name, "last_name": b.last_name,
        "serial_number": b.serial_number,
        "national_id": b.national_id, "phone": b.phone, "gender": b.gender,
        "date_of_birth": b.date_of_birth,
        "district_of_birth": b.district_of_birth, "place_of_issue": b.place_of_issue,
        "date_of_issue": b.date_of_issue, "district": b.district, "division": b.division,
        "location": b.location, "sub_location": b.sub_location,
        "region_id": b.region_id, "branch_id": b.branch_id,
        "business_sector": b.business_sector,
        "baseline_monthly_sales": float(b.baseline_monthly_sales or 0),
        "baseline_employees": b.baseline_employees,
        "kyc_status": b.kyc_status, "credit_score": b.credit_score,
        "current_credit_rating": b.current_credit_rating,
        "is_active": True if b.is_active is None else bool(b.is_active),
        "onboarded_by": b.onboarded_by, "approved_by_user_id": b.approved_by_user_id,
        "mpesa_validated": bool(b.mpesa_validated),
        "mpesa_validation_name": b.mpesa_validation_name,
        "mpesa_validated_at": b.mpesa_validated_at,
        "ekyc_status": b.ekyc_status, "ekyc_reference": b.ekyc_reference,
        "ekyc_checked_at": b.ekyc_checked_at,
    }
    if loan_count is not None:
        out["loan_count"] = loan_count
    if nested:
        out["wallets"] = [_wallet_dict(w) for w in b.wallets]
        out["next_of_kin"] = [_nok_dict(n) for n in b.next_of_kin]
        out["documents"] = [_doc_dict(d) for d in sorted(b.documents, key=lambda d: d.id, reverse=True)]
    return out


# Backwards-compatible alias used by the lending router.
_borrower_dict = _client_dict


def _get_client(db: Session, tenant_id: int, client_id: int) -> Borrower:
    c = (db.query(Borrower)
         .options(joinedload(Borrower.wallets), joinedload(Borrower.next_of_kin),
                  joinedload(Borrower.documents))
         .filter(Borrower.id == client_id, Borrower.tenant_id == tenant_id).first())
    if not c:
        raise HTTPException(404, "Client not found")
    return c


def _sync_nested(db: Session, client: Borrower, tenant_id: int, body: ClientCreate) -> None:
    """Replace-in-place sync of the wallet / next-of-kin sub-grids: rows with an
    id are updated, new rows inserted, missing ids deleted."""
    # Mobile wallets
    keep_w = {w.id for w in body.wallets if w.id}
    for existing in list(client.wallets):
        if existing.id not in keep_w:
            db.delete(existing)
    by_id = {w.id: w for w in client.wallets if w.id}
    for row in body.wallets:
        if not any([row.mobile_number, row.wallet_number, row.operator]):
            continue  # ignore blank "+ Add row" placeholders
        target = by_id.get(row.id) if row.id else None
        if target is None:
            target = ClientMobileWallet(tenant_id=tenant_id, client_id=client.id)
            db.add(target)
        target.mobile_number = row.mobile_number
        target.wallet_number = row.wallet_number
        target.operator = row.operator
        target.active = row.active

    # Next of kin
    keep_n = {n.id for n in body.next_of_kin if n.id}
    for existing in list(client.next_of_kin):
        if existing.id not in keep_n:
            db.delete(existing)
    by_id_n = {n.id: n for n in client.next_of_kin if n.id}
    for row in body.next_of_kin:
        if not any([row.full_name, row.mobile_number, row.national_id]):
            continue
        target = by_id_n.get(row.id) if row.id else None
        if target is None:
            target = ClientNextOfKin(tenant_id=tenant_id, client_id=client.id)
            db.add(target)
        target.full_name = row.full_name
        target.relationship_type = row.relationship
        target.mobile_number = row.mobile_number
        target.national_id = row.national_id
        target.address = row.address
        target.active = row.active


def _apply_scalars(client: Borrower, body: ClientCreate) -> None:
    data = body.model_dump(exclude={"wallets", "next_of_kin"})
    for key, value in data.items():
        setattr(client, key, value)


# --------------------------------------------------------------------------- #
# Router factory — mounted twice (canonical + legacy alias)
# --------------------------------------------------------------------------- #
def build_router(prefix: str, tag: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    # ---- reference data for the form selects -------------------------------
    @router.get("/reference")
    def reference(tenant_id: int = Depends(require_module("lending")),
                  db: Session = Depends(get_db)):
        users = db.query(User).filter(or_(User.tenant_id == tenant_id,
                                          User.role == "super_admin"),
                                      User.active).order_by(User.full_name).all()
        return {
            "wallet_operators": WALLET_OPERATORS,
            "relationships": NEXT_OF_KIN_RELATIONSHIPS,
            "doc_types": DOC_TYPES,
            # "failed" is kept in the list because the demo seeder (and historical
            # records) use it alongside "rejected".
            "kyc_statuses": ["draft", "pending", "validated", "failed", "rejected"],
            "approvers": [{"id": u.id, "name": u.full_name, "role": u.role} for u in users],
        }

    # ---- list / read -------------------------------------------------------
    @router.get("")
    def list_clients(tenant_id: int = Depends(require_module("lending")),
                     db: Session = Depends(get_db),
                     search: str = "", kyc_status: str = "",
                     page: int = 1, page_size: int = 20):
        q = db.query(Borrower).filter(Borrower.tenant_id == tenant_id)
        if search:
            like = f"%{search}%"
            q = q.filter(or_(Borrower.first_name.ilike(like), Borrower.last_name.ilike(like),
                             Borrower.national_id.ilike(like), Borrower.phone.ilike(like)))
        if kyc_status:
            q = q.filter(Borrower.kyc_status == kyc_status)
        total = q.count()
        rows = (q.order_by(Borrower.id.desc())
                .offset((page - 1) * page_size).limit(page_size).all())
        return {"total": total, "page": page, "items": [_client_dict(b) for b in rows]}

    @router.get("/{client_id}")
    def client_detail(client_id: int, tenant_id: int = Depends(require_module("lending")),
                      db: Session = Depends(get_db)):
        c = _get_client(db, tenant_id, client_id)
        out = _client_dict(c, nested=True)
        loans = (db.query(Loan).filter(Loan.borrower_id == c.id)
                 .order_by(Loan.id.desc()).all())
        out["loans"] = [{"id": l.id, "account_number": l.account_number,
                         "principal": float(l.principal), "status": l.status,
                         "disbursement_date": l.disbursement_date, "due_date": l.due_date,
                         "outstanding_balance": float(l.outstanding_balance or 0)}
                        for l in loans]
        surveys = (db.query(ImpactSurvey).filter(ImpactSurvey.borrower_id == c.id)
                   .order_by(ImpactSurvey.id.desc()).all())
        out["impact_surveys"] = [{"id": s.id, "loan_cycle_number": s.loan_cycle_number,
                                  "monthly_sales_pre": float(s.monthly_sales_pre or 0),
                                  "monthly_sales_post": float(s.monthly_sales_post or 0),
                                  "jobs_created": s.jobs_created,
                                  "survey_date": s.survey_date} for s in surveys]
        return out

    # ---- create / update ---------------------------------------------------
    @router.post("")
    def create_client(body: ClientCreate,
                      tenant_id: int = Depends(require_module("lending")),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
        client = Borrower(tenant_id=tenant_id)
        _apply_scalars(client, body)
        if not client.onboarded_by:
            client.onboarded_by = user.full_name
        db.add(client)
        db.flush()                     # need the id for the nested rows
        _sync_nested(db, client, tenant_id, body)
        db.commit()
        db.refresh(client)
        return _client_dict(client, nested=True)

    @router.put("/{client_id}")
    def update_client(client_id: int, body: ClientCreate,
                      tenant_id: int = Depends(require_module("lending")),
                      db: Session = Depends(get_db)):
        client = _get_client(db, tenant_id, client_id)
        _apply_scalars(client, body)
        _sync_nested(db, client, tenant_id, body)
        db.commit()
        db.refresh(client)
        return _client_dict(client, nested=True)

    # ---- documents ---------------------------------------------------------
    @router.get("/{client_id}/documents")
    def list_documents(client_id: int, tenant_id: int = Depends(require_module("lending")),
                       db: Session = Depends(get_db)):
        _get_client(db, tenant_id, client_id)
        rows = (db.query(ClientDocument)
                .filter(ClientDocument.client_id == client_id,
                        ClientDocument.tenant_id == tenant_id)
                .order_by(ClientDocument.id.desc()).all())
        return [_doc_dict(d) for d in rows]

    @router.post("/{client_id}/documents")
    async def upload_documents(client_id: int,
                               files: list[UploadFile] = File(...),
                               doc_types: str = Query("", description="Comma-separated doc_type per file"),
                               tenant_id: int = Depends(require_module("lending")),
                               db: Session = Depends(get_db),
                               user: User = Depends(get_current_user)):
        """Accepts ANY file type, several at a time. `doc_types` is a parallel,
        comma-separated list (missing entries default to 'other')."""
        _get_client(db, tenant_id, client_id)
        types = [t.strip() for t in doc_types.split(",")] if doc_types else []
        saved = []
        for idx, upload in enumerate(files):
            data = await upload.read()
            if len(data) > MAX_BYTES:
                raise HTTPException(413, f"'{upload.filename}' exceeds the "
                                         f"{storage.settings.MAX_UPLOAD_MB}MB limit")
            if not data:
                continue
            doc_type = types[idx] if idx < len(types) and types[idx] in DOC_TYPES else "other"
            stored, path = storage.save_bytes(tenant_id, client_id, upload.filename or "file", data)
            row = ClientDocument(
                tenant_id=tenant_id, client_id=client_id, file_name=stored,
                original_name=upload.filename, mime_type=upload.content_type,
                size_bytes=len(data), doc_type=doc_type, storage_path=path,
                uploaded_by=user.full_name,
            )
            db.add(row)
            saved.append(row)
        db.commit()
        return {"uploaded": len(saved), "documents": [_doc_dict(d) for d in saved]}

    @router.get("/{client_id}/documents/{doc_id}/download")
    def download_document(client_id: int, doc_id: int,
                          tenant_id: int = Depends(require_module("lending")),
                          db: Session = Depends(get_db)):
        doc = (db.query(ClientDocument)
               .filter(ClientDocument.id == doc_id, ClientDocument.client_id == client_id,
                       ClientDocument.tenant_id == tenant_id).first())
        if not doc or not doc.storage_path or not os.path.exists(doc.storage_path):
            raise HTTPException(404, "Document not found")
        return Response(
            content=storage.read_bytes(doc.storage_path),
            media_type=doc.mime_type or "application/octet-stream",
            headers={"Content-Disposition": f'inline; filename="{doc.original_name}"'},
        )

    @router.delete("/{client_id}/documents/{doc_id}")
    def delete_document(client_id: int, doc_id: int,
                        tenant_id: int = Depends(require_module("lending")),
                        db: Session = Depends(get_db)):
        doc = (db.query(ClientDocument)
               .filter(ClientDocument.id == doc_id, ClientDocument.client_id == client_id,
                       ClientDocument.tenant_id == tenant_id).first())
        if not doc:
            raise HTTPException(404, "Document not found")
        if doc.storage_path:
            storage.delete_file(doc.storage_path)
        db.delete(doc)
        db.commit()
        return {"ok": True}

    # ---- Process ID (OCR) --------------------------------------------------
    @router.post("/ocr/process-id")
    async def process_id(files: list[UploadFile] = File(...),
                         client_id: int | None = Query(None, description="Persist OCR text against a saved client"),
                         tenant_id: int = Depends(require_module("lending")),
                         db: Session = Depends(get_db)):
        """Runs OCR over every queued image/PDF (e.g. ID front + back) and returns
        merged National-ID fields with per-field confidence + the raw text."""
        payload = []
        for upload in files:
            data = await upload.read()
            if not data:
                continue
            if len(data) > MAX_BYTES:
                raise HTTPException(413, f"'{upload.filename}' exceeds the "
                                         f"{storage.settings.MAX_UPLOAD_MB}MB limit")
            mime = (upload.content_type or "").lower()
            name = (upload.filename or "").lower()
            if not (mime in OCR_MIMES or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".pdf"))):
                continue  # non-OCR-able attachments are simply skipped
            payload.append((upload.filename or "file", mime, data))
        if not payload:
            raise HTTPException(400, "Queue no OCR-able file — add a JPEG, PNG or PDF of the ID.")
        try:
            result = process_id_files(payload)
        except OcrUnavailable as exc:
            # 503 (not 500) so the UI can show an actionable message.
            raise HTTPException(503, f"OCR engine unavailable: {exc}")

        if client_id:
            client = _get_client(db, tenant_id, client_id)
            doc = (db.query(ClientDocument)
                   .filter(ClientDocument.client_id == client.id,
                           ClientDocument.tenant_id == tenant_id)
                   .order_by(ClientDocument.id.desc()).first())
            if doc:
                doc.ocr_applied = True
                doc.ocr_text = result["raw_text"][:20000]
                db.commit()
        return result

    # ---- eKYC --------------------------------------------------------------
    @router.post("/ekyc/verify")
    def ekyc_verify(body: EkycVerifyRequest,
                    tenant_id: int = Depends(require_module("lending")),
                    db: Session = Depends(get_db)):
        client = _get_client(db, tenant_id, body.client_id) if body.client_id else None
        national_id = body.national_id or (client.national_id if client else None)
        first = body.first_name or (client.first_name if client else None)
        last = body.last_name or (client.last_name if client else None)
        if not (national_id and first and last):
            raise HTTPException(400, "National ID, first name and last name are required for eKYC")

        result = ekyc.verify_identity(
            national_id=national_id, first_name=first, last_name=last,
            middle_name=body.middle_name or (client.middle_name if client else None),
            date_of_birth=body.date_of_birth or (client.date_of_birth if client else None),
            phone=body.phone or (client.phone if client else None),
        )
        status_map = {"VERIFIED": "verified", "NOT_VERIFIED": "not_verified"}
        if client:
            client.ekyc_status = status_map.get(result.get("status"), "error")
            client.ekyc_reference = result.get("reference")
            client.ekyc_checked_at = datetime.utcnow()
            if client.ekyc_status == "verified" and client.kyc_status in (None, "draft", "pending"):
                client.kyc_status = "validated"
            db.commit()
        return {
            "status": status_map.get(result.get("status"), "error"),
            "reference": result.get("reference"),
            "match_score": result.get("matchScore"),
            "verified_name": result.get("verifiedName"),
            "checks": result.get("checks", {}),
            "provider": result.get("provider", settings_provider_name()),
            "raw": result,
        }

    # ---- M-Pesa number validation -----------------------------------------
    @router.post("/validate-mpesa")
    def validate_mpesa(body: ValidateMpesaRequest,
                       tenant_id: int = Depends(require_module("lending")),
                       db: Session = Depends(get_db)):
        client = _get_client(db, tenant_id, body.client_id) if body.client_id else None
        phone = body.phone or (client.phone if client else None)
        national_id = body.national_id or (client.national_id if client else None)
        expected = body.expected_name or (client.full_name if client else None)
        if not phone or not national_id:
            raise HTTPException(400, "Both a mobile number and a National ID are required")

        payload = mpesa.validate_mobile_number(phone, national_id, expected or "")
        resp = payload["response"]
        matched = bool(resp["Matched"])

        # Audit trail — same log the Daraja B2C/STK/C2B calls write to.
        db.add(PaymentTransaction(
            tenant_id=tenant_id, type="validation", amount=0, phone=resp["MSISDN"],
            mpesa_ref=resp.get("ConversationID"),
            status="success" if matched else "failed", raw_payload=payload,
        ))
        if client:
            client.mpesa_validated = matched
            client.mpesa_validation_name = resp.get("RegisteredName")
            client.mpesa_validated_at = datetime.utcnow()
        db.commit()
        return {
            "matched": matched,
            "msisdn": resp["MSISDN"],
            "registered_name": resp.get("RegisteredName"),
            "national_id": national_id,
            "result_desc": resp["ResultDesc"],
            "checked_at": resp["CheckedAt"],
            "raw": payload,
        }

    # ---- nested collection helpers (optional direct access) ----------------
    @router.delete("/{client_id}/wallets/{wallet_id}")
    def delete_wallet(client_id: int, wallet_id: int,
                      tenant_id: int = Depends(require_module("lending")),
                      db: Session = Depends(get_db)):
        row = (db.query(ClientMobileWallet)
               .filter(ClientMobileWallet.id == wallet_id,
                       ClientMobileWallet.client_id == client_id,
                       ClientMobileWallet.tenant_id == tenant_id).first())
        if not row:
            raise HTTPException(404, "Wallet not found")
        db.delete(row)
        db.commit()
        return {"ok": True}

    @router.delete("/{client_id}/next-of-kin/{nok_id}")
    def delete_nok(client_id: int, nok_id: int,
                   tenant_id: int = Depends(require_module("lending")),
                   db: Session = Depends(get_db)):
        row = (db.query(ClientNextOfKin)
               .filter(ClientNextOfKin.id == nok_id,
                       ClientNextOfKin.client_id == client_id,
                       ClientNextOfKin.tenant_id == tenant_id).first())
        if not row:
            raise HTTPException(404, "Next of kin not found")
        db.delete(row)
        db.commit()
        return {"ok": True}

    return router


def settings_provider_name() -> str:
    from app.core.config import settings
    return "live" if not settings.EKYC_MOCK else "creditinfo-idm (mock)"


# Canonical + legacy alias mounts.
router = build_router("/api/v1/clients", "clients")
alias_router = build_router("/api/v1/borrowers", "clients (alias)")
