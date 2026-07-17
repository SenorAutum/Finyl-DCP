"""Pydantic request schemas. Responses are serialized in routers as plain dicts
to keep the payload shapes obvious and greppable next to each endpoint."""
from datetime import date
from typing import Optional

from pydantic import BaseModel


# ---- Auth ------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str
    password: str


# ---- Tenancy / admin ---------------------------------------------------------
class TenantCreate(BaseModel):
    name: str
    code: str
    logo_color: str = "#10B981"
    active: bool = True


class ModuleToggle(BaseModel):
    tenant_id: int
    module_key: str
    enabled: bool


# ---- Lending -----------------------------------------------------------------
class BorrowerCreate(BaseModel):
    first_name: str
    middle_name: Optional[str] = None
    last_name: str
    national_id: str
    phone: str
    gender: Optional[str] = None
    date_of_birth: Optional[date] = None
    region_id: Optional[int] = None
    branch_id: Optional[int] = None
    business_sector: Optional[str] = None
    baseline_monthly_sales: float = 0
    baseline_employees: int = 0
    kyc_status: str = "draft"


class ProductCreate(BaseModel):
    name: str
    code: str
    interest_rate: float = 10.0
    interest_method: str = "flat"
    tenure_value: int = 4
    tenure_unit: str = "weeks"
    repayment_frequency: str = "weekly"
    min_amount: float = 1000
    max_amount: float = 100000
    min_age: int = 18
    max_age: int = 65
    penalty_rate: float = 1.0
    rules: dict = {}
    active: bool = True


class LoanApplication(BaseModel):
    borrower_id: int
    product_id: int
    principal: float
    staff_id: Optional[int] = None


class LoanStatusUpdate(BaseModel):
    status: str
    note: Optional[str] = None


# ---- Payments (Daraja mock shapes) ---------------------------------------------
class B2CRequest(BaseModel):
    loan_id: int


class StkPushRequest(BaseModel):
    loan_id: int
    amount: float


class C2BCallback(BaseModel):
    """Simplified Daraja C2B confirmation payload."""
    TransID: Optional[str] = None
    TransAmount: float
    MSISDN: str
    BillRefNumber: str  # loan account number


class SendSmsRequest(BaseModel):
    phone: str
    message: str
    trigger_type: str = "manual"


# ---- CRM -----------------------------------------------------------------------
class LeadCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    sector: Optional[str] = None
    region_id: Optional[int] = None
    stage: str = "lead"
    assigned_staff_id: Optional[int] = None
    estimated_loan_amount: float = 0
    notes: Optional[str] = None


class LeadStageUpdate(BaseModel):
    stage: str


class SiteVisitCreate(BaseModel):
    lead_id: int
    staff_id: Optional[int] = None
    visit_date: Optional[date] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    outcome: Optional[str] = None
    notes: Optional[str] = None


# ---- Call center ------------------------------------------------------------------
class CallLogCreate(BaseModel):
    borrower_id: int
    loan_id: Optional[int] = None
    duration_seconds: int = 0
    call_outcome: str = "no_answer"
    promise_to_pay_date: Optional[date] = None
    promise_amount: Optional[float] = None
    notes: Optional[str] = None


# ---- Complaints ---------------------------------------------------------------------
class ComplaintCreate(BaseModel):
    borrower_id: Optional[int] = None
    category: str = "other"
    description: Optional[str] = None
    assigned_staff_id: Optional[int] = None


class ComplaintUpdate(BaseModel):
    status: Optional[str] = None
    assigned_staff_id: Optional[int] = None
    remedial_action: Optional[str] = None


# ---- Impact ------------------------------------------------------------------------------
class ImpactSurveyCreate(BaseModel):
    borrower_id: int
    loan_id: Optional[int] = None
    monthly_sales_pre: float = 0
    monthly_sales_post: float = 0
    jobs_created: int = 0
    sales_improved: bool = False
    next_capital_plan: Optional[str] = None


# ---- AI agent ---------------------------------------------------------------------------
class AiChatRequest(BaseModel):
    message: str
    history: list[dict] = []
