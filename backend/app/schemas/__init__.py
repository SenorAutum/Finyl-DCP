"""Pydantic request schemas. Responses are serialized in routers as plain dicts
to keep the payload shapes obvious and greppable next to each endpoint."""
from datetime import date
from typing import Optional

from pydantic import BaseModel


# ---- Auth ------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class SignupRequest(BaseModel):
    """Public self-service DCP (tenant) registration payload.

    organization_name -> new Tenant. admin_* -> the tenant's first user, created
    as the tenant-scoped administrator (role=system_admin). Validated server-side
    in the router (email format, strong password, length limits) since
    email-validator isn't installed for pydantic EmailStr.
    """
    organization_name: str
    admin_full_name: str
    admin_email: str
    password: str
    confirm_password: Optional[str] = None
    logo_color: Optional[str] = "#10B981"


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
    """Legacy/basic client payload — kept for the /lending/borrowers alias."""
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


# ---- Clients (KYC onboarding) --------------------------------------------------
class MobileWalletIn(BaseModel):
    id: Optional[int] = None
    mobile_number: Optional[str] = None
    wallet_number: Optional[str] = None
    operator: Optional[str] = None      # M-Pesa | Airtel Money | T-Kash | Equitel
    active: bool = True


class NextOfKinIn(BaseModel):
    id: Optional[int] = None
    full_name: Optional[str] = None
    relationship: Optional[str] = None  # Spouse | Parent | Sibling | Child | Guardian | Other
    mobile_number: Optional[str] = None
    national_id: Optional[str] = None
    address: Optional[str] = None
    active: bool = True


class ClientCreate(BaseModel):
    """Full client payload — ID details, business/impact profile and the nested
    Mobile Wallet / Next of Kin collections saved in the same request."""
    # Identity
    serial_number: Optional[str] = None
    national_id: str
    phone: Optional[str] = ""
    first_name: str
    middle_name: Optional[str] = None
    last_name: str
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    district_of_birth: Optional[str] = None
    place_of_issue: Optional[str] = None
    date_of_issue: Optional[date] = None
    district: Optional[str] = None
    division: Optional[str] = None
    location: Optional[str] = None
    sub_location: Optional[str] = None
    kyc_status: str = "draft"
    current_credit_rating: Optional[str] = None
    is_active: bool = True
    onboarded_by: Optional[str] = None
    approved_by_user_id: Optional[int] = None
    # Business & profile (pre-existing impact fields)
    region_id: Optional[int] = None
    branch_id: Optional[int] = None
    business_sector: Optional[str] = None
    baseline_monthly_sales: float = 0
    baseline_employees: int = 0
    credit_score: Optional[int] = 0
    # Nested collections
    wallets: list[MobileWalletIn] = []
    next_of_kin: list[NextOfKinIn] = []


class ValidateMpesaRequest(BaseModel):
    """Validate an arbitrary number/ID pair (draft form) or an existing client."""
    client_id: Optional[int] = None
    phone: Optional[str] = None
    national_id: Optional[str] = None
    expected_name: Optional[str] = None


class EkycVerifyRequest(BaseModel):
    client_id: Optional[int] = None
    national_id: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    phone: Optional[str] = None


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


# ---- Messaging (per-DCP customizable SMS templates) ---------------------------
class MessageTemplateIn(BaseModel):
    body: str
    active: bool = True


class MessagePreviewIn(BaseModel):
    # When body is omitted the stored/default template is previewed instead.
    body: Optional[str] = None


class MessageTestIn(BaseModel):
    phone: str
    # When body is omitted the stored/default template is sent instead.
    body: Optional[str] = None


class SmsAutomationIn(BaseModel):
    """Per-DCP SMS automation config. send_hour is a server-local hour 0-23."""
    automation_enabled: bool = True
    send_hour: int = 7


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



# ---- RBAC: users / access management -----------------------------------------------------
class UserCreate(BaseModel):
    email: str
    full_name: str
    role: str = "relationship_officer"
    password: Optional[str] = None          # defaults to platform demo password when omitted
    branch_id: Optional[int] = None
    region_id: Optional[int] = None
    staff_id: Optional[int] = None
    active: bool = True


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    branch_id: Optional[int] = None
    region_id: Optional[int] = None
    staff_id: Optional[int] = None
    active: Optional[bool] = None


class RoleAssign(BaseModel):
    role: str


# ---- RBAC: editable roles & permissions --------------------------------------------------
class RoleCreate(BaseModel):
    role_key: str            # new custom role key (lowercase, a-z0-9_)
    label: str


class RoleLabelUpdate(BaseModel):
    label: str


class RolePermissionUpdate(BaseModel):
    permission_key: str
    granted: bool = True


# ---- Per-DCP configuration (System Administrator) -----------------------------------------
class DarajaConfigIn(BaseModel):
    """Per-DCP M-Pesa/Daraja credentials. Secret fields are write-only — send a
    value to set/replace, omit or send blank to keep the stored value."""
    environment: Optional[str] = None        # sandbox | production
    shortcode: Optional[str] = None
    initiator_name: Optional[str] = None
    consumer_key: Optional[str] = None
    consumer_secret: Optional[str] = None
    passkey: Optional[str] = None
    security_credential: Optional[str] = None
    enabled: Optional[bool] = None


class SettingsModuleToggle(BaseModel):
    """Tenant is pinned server-side (own tenant) — only the target module + state."""
    module_key: str
    enabled: bool


class PasswordReset(BaseModel):
    password: Optional[str] = None          # if omitted, forces a reset flag only


# ---- RBAC: org structure -----------------------------------------------------------------
class RegionCreate(BaseModel):
    name: str


class BranchCreate(BaseModel):
    name: str
    region_id: int


# ---- RBAC: approval thresholds ------------------------------------------------------------
class ThresholdCreate(BaseModel):
    scope_type: str          # role | branch | region
    scope_key: str           # role name OR branch/region id (as text)
    threshold_type: str      # loan_approval | disbursement | refund
    amount: float = 0


class ApproverConfigIn(BaseModel):
    tenant_id: int
    approval_type: str       # loan | client | disbursement | refund
    role: str                # approver role key
    enabled: bool


# ---- RBAC: loan decisions & money movement -----------------------------------------------
class LoanDecision(BaseModel):
    action: str              # approve | reject | escalate
    note: Optional[str] = None


class DisburseRequest(BaseModel):
    loan_id: int
    reason: Optional[str] = None


class RefundRequest(BaseModel):
    # MPESA-05: refunds are always tied to a loan; the destination phone and the
    # maximum amount are derived server-side from the loan's recorded overpayment.
    # `amount` is optional (defaults to the full recorded overpayment); `phone` is
    # advisory only and, if supplied, must match the borrower's registered number.
    loan_id: int
    amount: Optional[float] = None
    phone: Optional[str] = None
    reason: Optional[str] = None


class ReconcileRequest(BaseModel):
    loan_id: int
    amount: float
    mpesa_ref: Optional[str] = None
    method: str = "mpesa_c2b"


class ApprovalDecision(BaseModel):
    action: str              # approve | reject
    note: Optional[str] = None


class ClientProfileDecision(BaseModel):
    action: str              # approve | reject
    note: Optional[str] = None


class ReassignRequest(BaseModel):
    staff_id: int
    reason: Optional[str] = None


# ---- HQ Operations: reporting -------------------------------------------------------------
class ReportScheduleCreate(BaseModel):
    name: str
    report_type: str = "loan_book"
    frequency: str = "weekly"
    recipients: Optional[str] = None


class ReportTemplateCreate(BaseModel):
    name: str
    definition: dict = {}


class AnomalyFlagCreate(BaseModel):
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    note: str
