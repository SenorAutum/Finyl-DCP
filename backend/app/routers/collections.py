"""Collections: promise-to-pay, bank statements, M-Pesa Ratiba (Phase 2)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (require_module, require_permission, get_current_user,
                           get_scope, write_audit)
from app.models import PromiseToPay, BankStatement, Loan
from app.schemas import (PtpCreate, PtpUpdate, BankStatementIn,
                         RatibaConsentIn, RatibaInitiateIn)
from app.services import ptp as ptp_service
from app.services import bank_statement as bank_service
from app.services import ratiba as ratiba_service
from app.services import collection_analytics

router = APIRouter(prefix="/api/v1/collections", tags=["collections"])

# ---------------------------------------------------------------- serializers
def _ptp_dict(p: PromiseToPay) -> dict:
    return {"id": p.id, "loan_id": p.loan_id, "client_id": p.client_id,
            "logged_by": p.logged_by, "ptp_date": p.ptp_date,
            "amount": float(p.amount) if p.amount is not None else None,
            "honored_amount": float(p.honored_amount) if p.honored_amount is not None else None,
            "contact_method": p.contact_method, "status": p.status, "notes": p.notes,
            "honored_at": p.honored_at, "broken_at": p.broken_at,
            "created_at": p.created_at}


def _stmt_dict(s: BankStatement) -> dict:
    return {"id": s.id, "client_id": s.client_id, "bank_name": s.bank_name,
            "period_start": s.period_start, "period_end": s.period_end,
            "months_covered": s.months_covered, "transactions_count": s.transactions_count,
            "avg_monthly_credit": float(s.avg_monthly_credit) if s.avg_monthly_credit is not None else None,
            "avg_monthly_debit": float(s.avg_monthly_debit) if s.avg_monthly_debit is not None else None,
            "net_monthly_cashflow": float(s.net_monthly_cashflow) if s.net_monthly_cashflow is not None else None,
            "affordability_score": float(s.affordability_score) if s.affordability_score is not None else None,
            "comfortable_installment": float(s.comfortable_installment) if s.comfortable_installment is not None else None,
            "detected_lenders": s.detected_lenders, "tampering_suspected": s.tampering_suspected,
            "integrity_flags": s.integrity_flags, "created_at": s.created_at}


# ------------------------------------------------------------------------ PTP
@router.post("/ptp")
def create_ptp(body: PtpCreate,
               tenant_id: int = Depends(require_module("lending")),
               user=Depends(require_permission("collections.ptp_manage")),
               db: Session = Depends(get_db), request: Request = None):
    try:
        p = ptp_service.create(db, tenant_id=tenant_id, loan_id=body.loan_id,
                               logged_by=user.id, ptp_date=body.ptp_date,
                               amount=body.amount, contact_method=body.contact_method,
                               notes=body.notes)
    except ValueError as e:
        raise HTTPException(404, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="collections.ptp_create",
                entity_type="loan", entity_id=body.loan_id,
                details={"ptp_id": p.id, "amount": float(body.amount)}, request=request)
    db.commit()
    return _ptp_dict(p)


@router.get("/ptp")
def list_ptps(loan_id: int = 0, officer_id: int = 0, status: str = "",
              tenant_id: int = Depends(require_module("lending")),
              user=Depends(require_permission("collections.ptp_manage")),
              scope=Depends(get_scope), db: Session = Depends(get_db)):
    q = db.query(PromiseToPay).filter(PromiseToPay.tenant_id == tenant_id)
    if loan_id:
        q = q.filter(PromiseToPay.loan_id == loan_id)
    if officer_id:
        q = q.filter(PromiseToPay.logged_by == officer_id)
    if status:
        q = q.filter(PromiseToPay.status == status)
    rows = q.order_by(PromiseToPay.ptp_date.desc()).limit(500).all()
    return {"items": [_ptp_dict(p) for p in rows]}


@router.get("/ptp/{ptp_id}")
def get_ptp(ptp_id: int, tenant_id: int = Depends(require_module("lending")),
            user=Depends(require_permission("collections.ptp_manage")),
            db: Session = Depends(get_db)):
    p = (db.query(PromiseToPay)
         .filter(PromiseToPay.id == ptp_id, PromiseToPay.tenant_id == tenant_id).first())
    if not p:
        raise HTTPException(404, "PTP not found")
    return _ptp_dict(p)


@router.patch("/ptp/{ptp_id}")
def update_ptp(ptp_id: int, body: PtpUpdate,
               tenant_id: int = Depends(require_module("lending")),
               user=Depends(require_permission("collections.ptp_manage")),
               db: Session = Depends(get_db), request: Request = None):
    try:
        p = ptp_service.update_status(db, tenant_id=tenant_id, ptp_id=ptp_id,
                                      status=body.status, honored_amount=body.honored_amount,
                                      ptp_date=body.ptp_date, notes=body.notes)
    except ValueError as e:
        raise HTTPException(404, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="collections.ptp_update",
                entity_type="ptp", entity_id=ptp_id,
                details={"status": body.status}, request=request)
    db.commit()
    return _ptp_dict(p)


# ----------------------------------------------------------------- efficiency
@router.get("/efficiency")
def efficiency(period_month: str = "",
               tenant_id: int = Depends(require_module("lending")),
               user=Depends(require_permission("collections.efficiency_view")),
               db: Session = Depends(get_db)):
    pm = None
    if period_month:
        try:
            pm = date.fromisoformat(period_month if len(period_month) > 7
                                    else period_month + "-01")
        except ValueError:
            raise HTTPException(400, "Invalid period_month (YYYY-MM)")
    return {"items": collection_analytics.leaderboard(db, tenant_id=tenant_id,
                                                      period_month=pm)}


# ------------------------------------------------------------- bank statements
@router.post("/bank-statement")
def ingest_bank_statement(body: BankStatementIn,
                          tenant_id: int = Depends(require_module("lending")),
                          user=Depends(require_permission("collections.ptp_manage")),
                          db: Session = Depends(get_db), request: Request = None):
    row = bank_service.ingest(db, tenant_id=tenant_id, client_id=body.client_id,
                              bank_name=body.bank_name,
                              transactions=body.transactions or [],
                              account_number=body.account_number,
                              source_filename=body.source_filename, created_by=user.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="collections.bank_statement_ingest",
                entity_type="client", entity_id=body.client_id,
                details={"statement_id": row.id}, request=request)
    db.commit()
    return _stmt_dict(row)


# --------------------------------------------------------------------- Ratiba
@router.post("/ratiba/consent")
def ratiba_consent(body: RatibaConsentIn,
                   tenant_id: int = Depends(require_module("lending")),
                   user=Depends(require_permission("collections.ptp_manage")),
                   db: Session = Depends(get_db), request: Request = None):
    ip = None
    if request is not None:
        ip = request.headers.get("x-forwarded-for") or (
            request.client.host if request.client else None)
    try:
        row = ratiba_service.record_consent(
            db, tenant_id=tenant_id, loan_id=body.loan_id, phone=body.phone,
            consent_given=body.consent_given, deduction_amount=body.deduction_amount,
            deduction_day=body.deduction_day, ip=ip)
    except ValueError as e:
        raise HTTPException(404, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="collections.ratiba_consent",
                entity_type="loan", entity_id=body.loan_id,
                details={"consent_given": body.consent_given}, request=request)
    db.commit()
    return ratiba_service.status_for_loan(db, tenant_id=tenant_id, loan_id=body.loan_id)


@router.post("/ratiba/initiate")
def ratiba_initiate(body: RatibaInitiateIn,
                    tenant_id: int = Depends(require_module("lending")),
                    user=Depends(require_permission("collections.ptp_manage")),
                    db: Session = Depends(get_db), request: Request = None):
    try:
        row = ratiba_service.initiate(db, tenant_id=tenant_id, loan_id=body.loan_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    write_audit(db, tenant_id=tenant_id, user=user, action="collections.ratiba_initiate",
                entity_type="loan", entity_id=body.loan_id,
                details={"status": row.ratiba_status, "ref": row.ratiba_ref}, request=request)
    db.commit()
    return ratiba_service.status_for_loan(db, tenant_id=tenant_id, loan_id=body.loan_id)


# --- Cross-prefix read endpoints (client / loan scoped) --------------------
extra_router = APIRouter(prefix="/api/v1", tags=["collections"])


@extra_router.get("/clients/{client_id}/bank-statements")
def client_bank_statements(client_id: int,
                           tenant_id: int = Depends(require_module("lending")),
                           user=Depends(require_permission("collections.ptp_manage")),
                           db: Session = Depends(get_db)):
    rows = (db.query(BankStatement)
            .filter(BankStatement.tenant_id == tenant_id,
                    BankStatement.client_id == client_id)
            .order_by(BankStatement.created_at.desc()).all())
    return {"items": [_stmt_dict(s) for s in rows]}


@extra_router.get("/loans/{loan_id}/ratiba-status")
def loan_ratiba_status(loan_id: int,
                       tenant_id: int = Depends(require_module("lending")),
                       user=Depends(require_permission("collections.ptp_manage")),
                       db: Session = Depends(get_db)):
    return ratiba_service.status_for_loan(db, tenant_id=tenant_id, loan_id=loan_id)
