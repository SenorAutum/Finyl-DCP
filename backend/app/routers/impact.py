"""Customer Journey, Social Impact & Investor Analytics + P2P Mentorship Engine."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_module
from app.models import Borrower, ImpactSurvey, Loan
from app.schemas import ImpactSurveyCreate
from app.services import analytics, mentorship

router = APIRouter(prefix="/api/v1/impact", tags=["impact"])


@router.get("/check-survey-required/{borrower_id}")
def check_survey_required(borrower_id: int, tenant_id: int = Depends(require_module("impact")),
                          db: Session = Depends(get_db)):
    """UI gate: does this borrower need an impact survey before their next application?"""
    prior = db.query(Loan).filter(Loan.borrower_id == borrower_id,
                                  Loan.tenant_id == tenant_id).count()
    next_cycle = prior + 1
    if next_cycle < 2:
        return {"required": False, "next_cycle": next_cycle}
    done = (db.query(ImpactSurvey)
            .filter(ImpactSurvey.borrower_id == borrower_id,
                    ImpactSurvey.loan_cycle_number == next_cycle).count() > 0)
    return {"required": not done, "next_cycle": next_cycle}


@router.post("/surveys")
def submit_survey(body: ImpactSurveyCreate, tenant_id: int = Depends(require_module("impact")),
                  db: Session = Depends(get_db)):
    borrower = db.query(Borrower).filter(Borrower.id == body.borrower_id,
                                         Borrower.tenant_id == tenant_id).first()
    if not borrower:
        raise HTTPException(404, "Borrower not found")
    prior = db.query(Loan).filter(Loan.borrower_id == borrower.id).count()
    cycle = prior + 1
    seq = db.query(ImpactSurvey).filter(ImpactSurvey.tenant_id == tenant_id).count() + 1
    s = ImpactSurvey(
        tenant_id=tenant_id, survey_id=f"IMP/{tenant_id}/{seq}",
        loan_cycle_number=cycle, survey_date=date.today(), **body.model_dump(),
    )
    db.add(s)
    db.commit()
    return {"id": s.id, "survey_id": s.survey_id, "loan_cycle_number": cycle}


@router.get("/surveys")
def list_surveys(tenant_id: int = Depends(require_module("impact")), db: Session = Depends(get_db),
                 page: int = 1, page_size: int = 20):
    q = (db.query(ImpactSurvey).options(joinedload(ImpactSurvey.borrower))
         .filter(ImpactSurvey.tenant_id == tenant_id))
    total = q.count()
    rows = q.order_by(ImpactSurvey.survey_date.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "items": [{
        "id": s.id, "survey_id": s.survey_id, "borrower_id": s.borrower_id,
        "borrower_name": s.borrower.full_name if s.borrower else None,
        "loan_cycle_number": s.loan_cycle_number,
        "monthly_sales_pre": float(s.monthly_sales_pre or 0),
        "monthly_sales_post": float(s.monthly_sales_post or 0),
        "jobs_created": s.jobs_created, "sales_improved": s.sales_improved,
        "next_capital_plan": s.next_capital_plan, "survey_date": s.survey_date,
    } for s in rows]}


@router.get("/investor-dashboard")
def investor_dashboard(tenant_id: int = Depends(require_module("impact")),
                       db: Session = Depends(get_db)):
    """Aggregate social-impact metrics translated into financial-style KPIs."""
    return analytics.impact_analytics(db, tenant_id)


@router.get("/mentorship-pairings")
def mentorship_pairings(tenant_id: int = Depends(require_module("impact")),
                        db: Session = Depends(get_db)):
    """Recommended veteran→rookie mentor pairings with match rationale."""
    return mentorship.recommend_pairings(db, tenant_id)
