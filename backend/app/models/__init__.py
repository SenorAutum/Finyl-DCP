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

# `Client` is the canonical business name; the ORM class (and its table) keep the
# historical `Borrower`/`borrowers` naming so existing joins/analytics keep working.
Client = Borrower
