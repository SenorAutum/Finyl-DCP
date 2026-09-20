"""Aggregate model imports so Base.metadata sees every table."""
from app.models.tenancy import Tenant, TenantModule, User, MODULE_KEYS
from app.models.org import Region, Branch, Staff, Product
from app.models.lending import (Borrower, Loan, Repayment, PaymentTransaction, SmsLog,
                                SmsTemplate, LOAN_STATUSES, WALLET_OPERATORS,
                                NEXT_OF_KIN_RELATIONSHIPS, DOC_TYPES)
from app.models.client_kyc import ClientMobileWallet, ClientNextOfKin, ClientDocument
from app.models.rbac import (ApprovalThreshold, ApproverSetting, AuditLog,
                             PendingApproval, ReportSchedule, ReportTemplate,
                             AnomalyFlag, SmsAutomationSetting, THRESHOLD_TYPES,
                             SCOPE_TYPES, APPROVAL_TYPES,
                             RolePermissionOverride, CustomRole,
                             SMS_AUTOMATION_DEFAULT_ENABLED, SMS_AUTOMATION_DEFAULT_HOUR)
from app.models.engagement import (
    CrmLead, SiteVisit, CallLog, Complaint, ImpactSurvey, AmlFlag,
    CRM_STAGES, CALL_OUTCOMES, COMPLAINT_CATEGORIES, COMPLAINT_STATUSES, SLA_DAYS,
)
from app.models.integrations import (MpesaStatementAnalysis, CrbCheck,
                                     TenantIntegrationConfig, SmsRateCard,
                                     IntegrationTestLog, MpesaWebhookEvent)
from app.models.additive import (EclProvisionConfig, SuspenseEntry, SmsOptOut,
                                  KycConsent, ChartOfAccount,
                                  ECL_DEFAULT_STAGE1_RATE, ECL_DEFAULT_STAGE2_RATE,
                                  ECL_DEFAULT_STAGE3_RATE, SUSPENSE_SOURCES,
                                  SUSPENSE_REASONS, SUSPENSE_STATUSES,
                                  OPT_OUT_SOURCES, COA_TYPES)

# ── Phase 2 additive domains ───────────────────────────────────────────────
from app.models.guarantor import (Guarantor, GuarantorBusiness, GuarantorDocument,
                                   GUARANTOR_TYPES, LOAN_CATEGORIES,
                                   GUARANTOR_KYC_STATUSES)
from app.models.kyc import (KycMismatchEscalation, TenantValidationPrefs,
                            FaceValidationLog, KYC_ESCALATION_STATUSES,
                            FACE_VALIDATION_RESULTS, MISMATCH_TYPES)
from app.models.field_ops import (StaffGpsLog, StaffDailyTask, LocationAlert,
                                  ClientHomeGeo, BusinessSitePhoto,
                                  LOCATION_ALERT_TYPES, DAILY_TASK_STATUSES,
                                  GPS_TASK_TYPES)
from app.models.security import (OtpToken, UserDevice, TenantSecurityConfig,
                                 ClientConsentLog, OTP_PURPOSES)
from app.models.activity import (ActivityLog, ScreenshotLog, CAPTURE_TRIGGERS)
from app.models.collections import (MpesaRatibaConsent, BankStatement,
                                    PromiseToPay, CollectionEfficiency,
                                    PTP_CONTACT_METHODS, PTP_STATUSES,
                                    RATIBA_STATUSES)
from app.models.api_clients import (ThirdPartyApiClient, API_SCOPES)
from app.models.client_edit import (ClientEditRequest, LoanActiveLockLog,
                                    EDIT_TIERS, EDIT_REQUEST_STATUSES)

# `Client` is the canonical business name; the ORM class (and its table) keep the
# historical `Borrower`/`borrowers` naming so existing joins/analytics keep working.
Client = Borrower
