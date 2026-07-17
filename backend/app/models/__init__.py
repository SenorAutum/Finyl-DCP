"""Aggregate model imports so Base.metadata sees every table."""
from app.models.tenancy import Tenant, TenantModule, User, MODULE_KEYS
from app.models.org import Region, Branch, Staff, Product
from app.models.lending import Borrower, Loan, Repayment, PaymentTransaction, SmsLog, LOAN_STATUSES
from app.models.engagement import (
    CrmLead, SiteVisit, CallLog, Complaint, ImpactSurvey, AmlFlag,
    CRM_STAGES, CALL_OUTCOMES, COMPLAINT_CATEGORIES, COMPLAINT_STATUSES, SLA_DAYS,
)
