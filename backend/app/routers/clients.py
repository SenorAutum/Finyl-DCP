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

from fastapi import Request
from app.core.crypto import pii_hash
from app.core.database import get_db
from app.core.deps import (get_current_user, require_module, require_permission,
                           get_scope, UserScope, write_audit)
from app.core.permissions import has_permission
from app.models import (Borrower, ClientDocument, ClientMobileWallet,
                        ClientNextOfKin, CrbCheck, DOC_TYPES, Loan, ImpactSurvey,
                        MpesaStatementAnalysis, NEXT_OF_KIN_RELATIONSHIPS,
                        PaymentTransaction, User, WALLET_OPERATORS)
from app.models import KycConsent
from app.schemas import ClientCreate, EkycVerifyRequest, ValidateMpesaRequest, ConsentIn
from app.services import crb, ekyc, mpesa, storage
from app.services import alt_phone_validation, client_edit
from app.services.mpesa_statement import StatementError, analyze_statement
from app.services.ocr import OcrUnavailable, process_id_files, extract_fields_structured
from app.core import obs

# Minimum onboarding age (CBK Digital Credit Providers — adult borrowers only).
MIN_ONBOARDING_AGE = 18


def _age_years(dob) -> int | None:
    """Whole years old as of today, or None when the DOB is missing/unparseable."""
    if not dob:
        return None
    if isinstance(dob, str):
        try:
            dob = datetime.fromisoformat(dob).date()
        except ValueError:
            return None
    if isinstance(dob, datetime):
        dob = dob.date()
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

# Primary identity fields locked from relationship officers (field-level lock).
LOCKED_FIELDS = ("phone", "national_id", "date_of_birth")

MAX_BYTES = storage.MAX_BYTES
OCR_MIMES = ("image/jpeg", "image/jpg", "image/png", "image/webp", "application/pdf")

# INPUT-01: MIME types that browsers may render/execute inline. We never trust the
# client-supplied Content-Type for these — they are neutralised to a non-active
# type on ingest so a stored document can never be served as active content.
DANGEROUS_MIME = {
    "text/html", "application/xhtml+xml", "image/svg+xml",
    "application/xml", "text/xml", "application/javascript",
    "text/javascript", "application/ecmascript", "text/ecmascript",
}


def _safe_mime(content_type: str | None) -> str:
    """Return a storage-safe MIME, downgrading active/renderable types."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if not ct or ct in DANGEROUS_MIME:
        return "application/octet-stream"
    return ct


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
def _wallet_dict(w: ClientMobileWallet) -> dict:
    return {"id": w.id, "mobile_number": w.mobile_number, "wallet_number": w.wallet_number,
            "operator": w.operator, "active": bool(w.active),
            "wallet_locked": bool(getattr(w, "wallet_locked", False)),
            "locked_at": getattr(w, "locked_at", None),
            "locked_by": getattr(w, "locked_by", None)}


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
        "officer_staff_id": getattr(b, "officer_staff_id", None),
        "profile_status": getattr(b, "profile_status", "approved"),
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


# INPUT-02 — trust/decision fields must never be mass-assigned from the client
# payload. They are set only by their own server-side flows (eKYC verify sets
# kyc_status, the CRB check sets credit_score, approval workflow sets approver /
# is_active). Accepting them from ClientCreate would let a caller self-approve or
# forge a credit score.
_TRUST_FIELDS = {"kyc_status", "credit_score", "current_credit_rating",
                 "approved_by_user_id", "is_active"}


def _apply_scalars(client: Borrower, body: ClientCreate) -> None:
    data = body.model_dump(exclude={"wallets", "next_of_kin"} | _TRUST_FIELDS)
    for key, value in data.items():
        setattr(client, key, value)
    # PII-02: the national_id blind index (national_id_hash) is kept in step
    # automatically by the before_insert/before_update mapper event on Borrower
    # (see models/lending.py), so every write path stays consistent.


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
        # Only surface real client-profile approvers: users whose role holds
        # clients.approve and is not the front-line originator. This keeps
        # relationship_officer (and other non-approver roles) out of the picker.
        from app.core.permissions import has_permission
        approvers = [u for u in users
                     if u.role != "relationship_officer" and has_permission(u.role, "clients.approve")]
        return {
            "wallet_operators": WALLET_OPERATORS,
            "relationships": NEXT_OF_KIN_RELATIONSHIPS,
            "doc_types": DOC_TYPES,
            # "failed" is kept in the list because the demo seeder (and historical
            # records) use it alongside "rejected".
            "kyc_statuses": ["draft", "pending", "validated", "failed", "rejected"],
            "approvers": [{"id": u.id, "name": u.full_name, "role": u.role} for u in approvers],
        }

    # ---- list / read -------------------------------------------------------
    @router.get("")
    def list_clients(tenant_id: int = Depends(require_module("lending")),
                     db: Session = Depends(get_db), scope: UserScope = Depends(get_scope),
                     search: str = "", kyc_status: str = "", profile_status: str = "",
                     page: int = 1, page_size: int = 20):
        q = db.query(Borrower).filter(Borrower.tenant_id == tenant_id)
        q = scope.apply_client(q, Borrower)
        if profile_status:
            q = q.filter(Borrower.profile_status == profile_status)
        if search:
            like = f"%{search}%"
            # PII-02: national_id is encrypted at rest, so a substring ILIKE can no
            # longer match it. Match the full national_id via its blind index
            # (exact match) instead, alongside the plaintext name/phone search.
            q = q.filter(or_(Borrower.first_name.ilike(like), Borrower.last_name.ilike(like),
                             Borrower.phone.ilike(like),
                             Borrower.national_id_hash == pii_hash(search.strip())))
        if kyc_status:
            q = q.filter(Borrower.kyc_status == kyc_status)
        total = q.count()
        rows = (q.order_by(Borrower.id.desc())
                .offset((page - 1) * page_size).limit(page_size).all())
        return {"total": total, "page": page, "items": [_client_dict(b) for b in rows]}

    @router.get("/{client_id}")
    def client_detail(client_id: int, tenant_id: int = Depends(require_module("lending")),
                      db: Session = Depends(get_db), scope: UserScope = Depends(get_scope)):
        c = _get_client(db, tenant_id, client_id)
        if not scope.can_see_client(c):
            raise HTTPException(403, "Client is outside your data scope")
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
                      user: User = Depends(require_permission("clients.create")),
                      request: Request = None):
        # Age gate (CBK): reject an under-age applicant up front. A missing DOB is
        # allowed at draft capture but leaves the client un-age-verified (the KYC
        # decision engine treats "age unknown" as a fail when age is mandatory).
        age = _age_years(getattr(body, "date_of_birth", None))
        if age is not None and age < MIN_ONBOARDING_AGE:
            obs.log_cbk_event(obs.CBK_AGE_REJECTED, tenant_id=tenant_id,
                              user_id=user.id, entity_type="client", outcome="rejected",
                              detail=f"age={age}")
            raise HTTPException(422, f"Applicant must be at least {MIN_ONBOARDING_AGE} "
                                     f"years old (computed age: {age}).")
        client = Borrower(tenant_id=tenant_id)
        _apply_scalars(client, body)
        if age is not None and age >= MIN_ONBOARDING_AGE:
            client.date_of_birth_verified = True
            client.age_verified_at = datetime.utcnow()
        if not client.onboarded_by:
            client.onboarded_by = user.full_name
        # A relationship officer owns the client they create, and their new
        # profiles start pending branch-manager approval.
        if user.role in ("relationship_officer", "loan_officer") and user.staff_id:
            client.officer_staff_id = user.staff_id
            client.profile_status = "pending_approval"
        elif not getattr(client, "profile_status", None):
            client.profile_status = "approved"
        db.add(client)
        db.flush()                     # need the id for the nested rows
        _sync_nested(db, client, tenant_id, body)
        write_audit(db, tenant_id=tenant_id, user=user, action="client.create",
                    entity_type="client", entity_id=client.id,
                    details={"profile_status": client.profile_status}, request=request)
        db.commit()
        db.refresh(client)
        return _client_dict(client, nested=True)

    @router.put("/{client_id}")
    def update_client(client_id: int, body: ClientCreate,
                      tenant_id: int = Depends(require_module("lending")),
                      db: Session = Depends(get_db),
                      user: User = Depends(require_permission("clients.edit")),
                      scope: UserScope = Depends(get_scope),
                      request: Request = None):
        client = _get_client(db, tenant_id, client_id)
        if not scope.can_see_client(client):
            raise HTTPException(403, "Client is outside your data scope")
        # Field-level lock: primary identity fields can only be changed by users
        # with clients.edit_locked (branch/regional managers, admins).
        may_edit_locked = has_permission(user.role, "clients.edit_locked")
        changed_locked = []
        for f in LOCKED_FIELDS:
            new_val = getattr(body, f, None)
            old_val = getattr(client, f, None)
            # normalise for comparison
            if str(new_val or "") != str(old_val or ""):
                changed_locked.append(f)
        # Record-level lock (Phase 2): once a client has an active loan (or has been
        # explicitly edit-locked) their locked identity fields are frozen for
        # EVERYONE — even users with clients.edit_locked. Such changes must go
        # through the maker-checker edit-request workflow (routers/client_edits.py).
        record_locked = bool(getattr(client, "edit_locked", False)) or \
            client_edit.has_active_loan(db, tenant_id=tenant_id, client_id=client.id)
        if changed_locked and record_locked:
            obs.log_cbk_event(obs.CBK_EDIT_LOCK_ENFORCED, tenant_id=tenant_id,
                              user_id=user.id, entity_type="client", entity_id=client.id,
                              outcome="blocked", detail=",".join(changed_locked))
            raise HTTPException(
                409, "This client is edit-locked (active loan). Locked field(s) "
                     f"{', '.join(changed_locked)} must be changed via an approved "
                     "edit request (POST /clients/{id}/edit-request).")
        if changed_locked and not may_edit_locked:
            raise HTTPException(
                422, f"You are not permitted to change locked field(s): "
                     f"{', '.join(changed_locked)}. Ask a branch manager.")
        _apply_scalars(client, body)
        _sync_nested(db, client, tenant_id, body)
        write_audit(db, tenant_id=tenant_id, user=user, action="client.edit",
                    entity_type="client", entity_id=client.id,
                    details={"locked_override": changed_locked if may_edit_locked else []},
                    request=request)
        db.commit()
        db.refresh(client)
        return _client_dict(client, nested=True)

    # ---- documents ---------------------------------------------------------
    @router.get("/{client_id}/documents")
    def list_documents(client_id: int, tenant_id: int = Depends(require_module("lending")),
                       db: Session = Depends(get_db),
                       scope: UserScope = Depends(get_scope)):
        client = _get_client(db, tenant_id, client_id)
        if not scope.can_see_client(client):
            raise HTTPException(403, "Client is outside your data scope")
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
                               user: User = Depends(require_permission("docs.upload")),
                               scope: UserScope = Depends(get_scope)):
        """Accepts ANY file type, several at a time. `doc_types` is a parallel,
        comma-separated list (missing entries default to 'other')."""
        client = _get_client(db, tenant_id, client_id)
        if not scope.can_see_client(client):
            raise HTTPException(403, "Client is outside your data scope")
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
                original_name=upload.filename, mime_type=_safe_mime(upload.content_type),
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
                          db: Session = Depends(get_db),
                          scope: UserScope = Depends(get_scope)):
        client = _get_client(db, tenant_id, client_id)
        if not scope.can_see_client(client):
            raise HTTPException(403, "Client is outside your data scope")
        doc = (db.query(ClientDocument)
               .filter(ClientDocument.id == doc_id, ClientDocument.client_id == client_id,
                       ClientDocument.tenant_id == tenant_id).first())
        if not doc or not doc.storage_path or not os.path.exists(doc.storage_path):
            raise HTTPException(404, "Document not found")
        # INPUT-01: force a safe, non-rendering download regardless of stored MIME.
        safe_name = (doc.original_name or "document").replace('"', "").replace("\r", "").replace("\n", "")
        return Response(
            content=storage.read_bytes(doc.storage_path),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.delete("/{client_id}/documents/{doc_id}")
    def delete_document(client_id: int, doc_id: int,
                        tenant_id: int = Depends(require_module("lending")),
                        db: Session = Depends(get_db),
                        scope: UserScope = Depends(get_scope)):
        client = _get_client(db, tenant_id, client_id)
        if not scope.can_see_client(client):
            raise HTTPException(403, "Client is outside your data scope")
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
        # Locate the client's latest document up-front so the structured field
        # map can be persisted onto it (ocr_field_mapping / ocr_confidence /
        # ocr_version) when a client_id is supplied.
        doc = None
        if client_id:
            client = _get_client(db, tenant_id, client_id)
            doc = (db.query(ClientDocument)
                   .filter(ClientDocument.client_id == client.id,
                           ClientDocument.tenant_id == tenant_id)
                   .order_by(ClientDocument.id.desc()).first())
        try:
            # Phase 2: return the validated 1:1 canonical field map and persist it
            # onto the document when one exists. Falls back cleanly for anonymous
            # (no client_id) OCR previews.
            result = extract_fields_structured(payload, db=db if doc else None,
                                               document=doc)
        except OcrUnavailable as exc:
            # 503 (not 500) so the UI can show an actionable message.
            raise HTTPException(503, f"OCR engine unavailable: {exc}")
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

        try:
            result = ekyc.verify_identity(
                national_id=national_id, first_name=first, last_name=last,
                middle_name=body.middle_name or (client.middle_name if client else None),
                date_of_birth=body.date_of_birth or (client.date_of_birth if client else None),
                phone=body.phone or (client.phone if client else None),
            )
        except ekyc.EkycNotConfigured as exc:
            raise HTTPException(422, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"eKYC provider request failed: {exc}")
        status_map = {"VERIFIED": "verified", "NOT_VERIFIED": "not_verified"}
        kyc_decision = None
        if client:
            client.ekyc_status = status_map.get(result.get("status"), "error")
            client.ekyc_reference = result.get("reference")
            client.ekyc_checked_at = datetime.utcnow()
            # Phase 2: the eKYC provider result is ONE input, not the verdict. The
            # authoritative pass/fail/escalate comes from the centralised decision
            # engine, which enforces every check the tenant marked mandatory. We no
            # longer flip kyc_status to "validated" on a provider VERIFIED alone
            # (that was the manual-override shortcut) — the engine decides.
            decision = ekyc.authoritative_decision(db, tenant_id=tenant_id,
                                                   client_id=client.id)
            kyc_decision = decision.as_dict()
            new_status = ekyc.KYC_STATUS_BY_DECISION.get(decision.decision)
            if new_status and client.kyc_status in (None, "draft", "pending", "escalation"):
                client.kyc_status = new_status
            db.commit()
        return {
            "status": status_map.get(result.get("status"), "error"),
            "reference": result.get("reference"),
            "match_score": result.get("matchScore"),
            "verified_name": result.get("verifiedName"),
            "checks": result.get("checks", {}),
            "provider": result.get("provider", settings_provider_name()),
            # Authoritative KYC verdict (None for an anonymous, client-less check).
            "kyc_decision": kyc_decision,
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
        locked_wallets = []
        if client:
            client.mpesa_validated = matched
            client.mpesa_validation_name = resp.get("RegisteredName")
            client.mpesa_validated_at = datetime.utcnow()
            # Auto-lock the validated wallet(s): once the M-Pesa name check passes,
            # the client's payout number is frozen so it cannot be silently swapped
            # after underwriting. Matching is by the validated MSISDN; if none match
            # (e.g. formatting), lock all of the client's wallets defensively.
            if matched:
                msisdn = str(resp.get("MSISDN") or "")
                tail = msisdn[-9:]
                for w in client.wallets:
                    num = str(w.mobile_number or w.wallet_number or "")
                    if not w.wallet_locked and (not tail or tail in num or num[-9:] == tail):
                        w.wallet_locked = True
                        w.locked_at = datetime.utcnow()
                        w.locked_by = "system"
                        locked_wallets.append(w.id)
                if locked_wallets:
                    obs.log_cbk_event(obs.CBK_WALLET_LOCKED, tenant_id=tenant_id,
                                      entity_type="client", entity_id=client.id,
                                      outcome="locked", detail=str(locked_wallets))
        db.commit()
        return {
            "matched": matched,
            "msisdn": resp["MSISDN"],
            "registered_name": resp.get("RegisteredName"),
            "national_id": national_id,
            "result_desc": resp["ResultDesc"],
            "checked_at": resp["CheckedAt"],
            "wallets_locked": locked_wallets,
            "raw": payload,
        }

    # ---- alternate-phone validation ---------------------------------------
    @router.post("/{client_id}/validate-alt-phone")
    def validate_alt_phone(client_id: int,
                           tenant_id: int = Depends(require_module("lending")),
                           db: Session = Depends(get_db),
                           user: User = Depends(require_permission("clients.edit")),
                           scope: UserScope = Depends(get_scope),
                           request: Request = None):
        """Validate the client's stored alternate phone (operator + M-Pesa name check)."""
        client = _get_client(db, tenant_id, client_id)
        if not scope.can_see_client(client):
            raise HTTPException(403, "Client is outside your data scope")
        if not client.alt_phone:
            raise HTTPException(400, "Client has no alternate phone on file")
        result = alt_phone_validation.validate(
            db, tenant_id=tenant_id, phone=client.alt_phone,
            expected_name=client.full_name)
        ok = result.get("status") == "validated"
        client.alt_phone_operator = result.get("operator")
        client.alt_phone_validated = ok
        if ok:
            client.alt_phone_validated_at = datetime.utcnow()
        write_audit(db, tenant_id=tenant_id, user=user, action="client.validate_alt_phone",
                    entity_type="client", entity_id=client.id,
                    details={"operator": result.get("operator"),
                             "status": result.get("status"),
                             "name_match": result.get("name_match")}, request=request)
        db.commit()
        return {"client_id": client.id, "alt_phone_validated": ok, **result}

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

    # ---- M-Pesa statement analysis (creditworthiness) ----------------------
    def _statement_dict(a: MpesaStatementAnalysis) -> dict:
        return {
            "id": a.id, "client_id": a.client_id, "loan_id": a.loan_id,
            "period_start": a.period_start, "period_end": a.period_end,
            "months_covered": a.months_covered, "transactions_count": a.transactions_count,
            "summary": a.summary or {}, "detected_lenders": a.detected_lenders or [],
            "integrity_flags": a.integrity_flags or [],
            "affordability_score": a.affordability_score,
            "comfortable_installment": float(a.comfortable_installment or 0),
            "monthly_debt_service": float(a.monthly_debt_service or 0),
            "net_monthly_cash_flow": float(a.net_monthly_cash_flow or 0),
            "tampering_suspected": bool(a.tampering_suspected),
            "source_filename": a.source_filename, "created_at": a.created_at,
        }

    @router.post("/{client_id}/mpesa-statement")
    async def upload_mpesa_statement(
            client_id: int,
            file: UploadFile = File(...),
            password: str | None = Query(None, description="PDF password (defaults to the client's National ID)"),
            loan_id: int | None = Query(None),
            tenant_id: int = Depends(require_module("lending")),
            db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
        """Upload an official Safaricom M-Pesa statement PDF, decrypt + analyse it
        for creditworthiness (inflows/outflows, external borrowing, affordability
        score, integrity check) and persist the latest analysis."""
        client = _get_client(db, tenant_id, client_id)
        data = await file.read()
        if not data:
            raise HTTPException(400, "Empty file.")
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"File exceeds the {storage.settings.MAX_UPLOAD_MB}MB limit")
        # Password default: the client's National ID (statements are usually locked to it).
        pw = password or client.national_id
        try:
            result = analyze_statement(data, pw, file.filename or "statement.pdf")
        except StatementError as exc:
            # Retry once without the pre-filled ID password in case it was wrong.
            if password is None and pw:
                try:
                    result = analyze_statement(data, None, file.filename or "statement.pdf")
                except StatementError as exc2:
                    raise HTTPException(422, str(exc2))
            else:
                raise HTTPException(422, str(exc))
        except Exception as exc:
            raise HTTPException(500, f"Statement analysis failed: {exc}")

        analysis = MpesaStatementAnalysis(
            tenant_id=tenant_id, client_id=client.id, loan_id=loan_id,
            period_start=result.get("period_start"), period_end=result.get("period_end"),
            months_covered=result.get("months_covered", 0),
            transactions_count=result.get("transactions_count", 0),
            summary=result["summary"], detected_lenders=result["detected_lenders"],
            integrity_flags=result["integrity_flags"],
            affordability_score=result["affordability_score"],
            comfortable_installment=result["comfortable_installment"],
            monthly_debt_service=result["monthly_debt_service"],
            net_monthly_cash_flow=result["net_monthly_cash_flow"],
            tampering_suspected=result["tampering_suspected"],
            source_filename=result.get("source_filename"),
            created_by_user_id=user.id,
        )
        db.add(analysis)
        db.commit()
        db.refresh(analysis)
        return _statement_dict(analysis)

    @router.get("/{client_id}/mpesa-statement")
    def latest_mpesa_statement(client_id: int,
                               tenant_id: int = Depends(require_module("lending")),
                               db: Session = Depends(get_db)):
        _get_client(db, tenant_id, client_id)
        a = (db.query(MpesaStatementAnalysis)
             .filter(MpesaStatementAnalysis.client_id == client_id,
                     MpesaStatementAnalysis.tenant_id == tenant_id)
             .order_by(MpesaStatementAnalysis.id.desc()).first())
        return _statement_dict(a) if a else None

    # ---- CRB (credit reference bureau) check -------------------------------
    def _crb_dict(c: CrbCheck) -> dict:
        return {
            "id": c.id, "client_id": c.client_id, "provider": c.provider,
            "status": c.status, "reference": c.reference, "credit_score": c.credit_score,
            "active_accounts": c.active_accounts, "defaults_count": c.defaults_count,
            "total_outstanding": float(c.total_outstanding) if c.total_outstanding is not None else None,
            "error": c.error, "created_at": c.created_at,
        }

    @router.post("/{client_id}/crb-check")
    def crb_check(client_id: int,
                  tenant_id: int = Depends(require_module("lending")),
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
        """Run a Credit Reference Bureau check for the client. Credential-gated:
        when the bureau is not configured the response reports status
        'not_configured' with a clear message — no fabricated score."""
        client = _get_client(db, tenant_id, client_id)
        result = crb.run_check(national_id=client.national_id,
                               first_name=client.first_name, last_name=client.last_name,
                               phone=client.phone)
        row = CrbCheck(
            tenant_id=tenant_id, client_id=client.id, provider=result.get("provider"),
            status=result.get("status"), reference=result.get("reference"),
            credit_score=result.get("credit_score"),
            active_accounts=result.get("active_accounts"),
            defaults_count=result.get("defaults_count"),
            total_outstanding=result.get("total_outstanding"),
            raw=result.get("raw", {}), error=result.get("error"),
            created_by_user_id=user.id,
        )
        db.add(row)
        # Surface the bureau score on the client profile when we got one.
        if result.get("status") == "ok" and result.get("credit_score") is not None:
            client.credit_score = int(result["credit_score"])
        db.commit()
        db.refresh(row)
        return _crb_dict(row)

    @router.get("/{client_id}/crb-check")
    def latest_crb_check(client_id: int,
                         tenant_id: int = Depends(require_module("lending")),
                         db: Session = Depends(get_db)):
        _get_client(db, tenant_id, client_id)
        c = (db.query(CrbCheck)
             .filter(CrbCheck.client_id == client_id, CrbCheck.tenant_id == tenant_id)
             .order_by(CrbCheck.id.desc()).first())
        return _crb_dict(c) if c else None

    # -------------------------------------------------------------------------
    # KYC consent capture (data-processing consent mandatory when submitted).
    # Legacy borrowers with no consent row are grandfathered — create is not
    # hard-blocked; consent is only enforced on this endpoint.
    # -------------------------------------------------------------------------
    @router.post("/{client_id}/consent")
    def capture_consent(client_id: int, body: ConsentIn,
                        tenant_id: int = Depends(require_module("lending")),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_permission("clients.edit")),
                        request: Request = None):
        _get_client(db, tenant_id, client_id)
        if not body.consent_data_processing:
            raise HTTPException(422, "Data-processing consent is required to record consent")
        ip = request.client.host if (request and request.client) else None
        row = KycConsent(
            tenant_id=tenant_id, borrower_id=client_id,
            consent_data_processing=body.consent_data_processing,
            consent_credit_check=body.consent_credit_check,
            consent_marketing=body.consent_marketing,
            consent_version=body.consent_version,
            ip_address=ip,
        )
        db.add(row)
        db.flush()
        write_audit(db, tenant_id=tenant_id, user=user, action="client.consent.capture",
                    entity_type="kyc_consent", entity_id=row.id,
                    details={"borrower_id": client_id,
                             "consent_credit_check": body.consent_credit_check,
                             "consent_marketing": body.consent_marketing,
                             "consent_version": body.consent_version}, request=request)
        db.commit()
        return {
            "id": row.id, "borrower_id": client_id,
            "consent_data_processing": row.consent_data_processing,
            "consent_credit_check": row.consent_credit_check,
            "consent_marketing": row.consent_marketing,
            "consent_version": row.consent_version,
            "consented_at": row.consented_at.isoformat() if row.consented_at else None,
        }

    @router.get("/{client_id}/consent")
    def get_consent(client_id: int, tenant_id: int = Depends(require_module("lending")),
                    db: Session = Depends(get_db)):
        _get_client(db, tenant_id, client_id)
        row = (db.query(KycConsent)
               .filter(KycConsent.tenant_id == tenant_id,
                       KycConsent.borrower_id == client_id)
               .order_by(KycConsent.id.desc()).first())
        if not row:
            return {"borrower_id": client_id, "consent": None,
                    "note": "No consent on file (legacy borrower grandfathered)."}
        return {
            "borrower_id": client_id,
            "consent": {
                "id": row.id,
                "consent_data_processing": row.consent_data_processing,
                "consent_credit_check": row.consent_credit_check,
                "consent_marketing": row.consent_marketing,
                "consent_version": row.consent_version,
                "consented_at": row.consented_at.isoformat() if row.consented_at else None,
            },
        }

    return router


def settings_provider_name() -> str:
    from app.core.config import settings
    return "live" if not settings.EKYC_MOCK else "creditinfo-idm (mock)"


# Canonical + legacy alias mounts.
router = build_router("/api/v1/clients", "clients")
alias_router = build_router("/api/v1/borrowers", "clients (alias)")
