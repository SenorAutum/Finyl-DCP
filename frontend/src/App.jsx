// Route table with auth + feature-flag guards. Disabled modules are hidden from
// nav (Layout) AND blocked here, mirroring the backend's require_module() 403s.
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import ChangePassword from "./pages/ChangePassword";
import Dashboard from "./pages/dashboard/Dashboard";
import OfficerDashboard from "./pages/dashboard/OfficerDashboard";
import SuperAdminDashboard from "./pages/dashboard/SuperAdminDashboard";
import Clients from "./pages/clients/Clients";
import ClientDetail from "./pages/clients/ClientDetail";
import Loans from "./pages/lending/Loans";
import LoanDetail from "./pages/lending/LoanDetail";
import Products from "./pages/lending/Products";
import Payments from "./pages/payments/Payments";
import Suspense from "./pages/payments/Suspense";
import BankImport from "./pages/payments/BankImport";
import OptOuts from "./pages/messaging/OptOuts";
import Accounting from "./pages/accounting/Accounting";
import Complaints from "./pages/complaints/Complaints";
import Crm from "./pages/crm/Crm";
import CallCenter from "./pages/callcenter/CallCenter";
import Impact from "./pages/impact/Impact";
import Cbk from "./pages/cbk/Cbk";
import Admin from "./pages/admin/Admin";
import Integrations from "./pages/admin/Integrations";
import ApproverConfig from "./pages/admin/ApproverConfig";
import Messaging from "./pages/admin/Messaging";
import Approvals from "./pages/approvals/Approvals";
import Users from "./pages/access/Users";
import RolesPermissions from "./pages/access/RolesPermissions";
import BranchesRegions from "./pages/access/BranchesRegions";
import Thresholds from "./pages/access/Thresholds";
import PaymentUpload from "./pages/access/PaymentUpload";
import Backups from "./pages/access/Backups";
import AuditLog from "./pages/access/AuditLog";
import Reporting from "./pages/reporting/Reporting";
import Configuration from "./pages/settings/Configuration";
import Guarantors from "./pages/guarantors/Guarantors";
import GuarantorDetail from "./pages/guarantors/GuarantorDetail";
import Collections from "./pages/collections/Collections";
import Efficiency from "./pages/collections/Efficiency";
import FieldOps from "./pages/field/FieldOps";
import KycEscalations from "./pages/kyc/KycEscalations";
import ApiClients from "./pages/access/ApiClients";
import ClientEdits from "./pages/clients/ClientEdits";
import ExecutiveDashboard from "./pages/dashboard/ExecutiveDashboard";
import DirectorDashboard from "./pages/dashboard/DirectorDashboard";
// Phase 4: activity monitoring, GPS/site visits, face validation, security &
// device config, bank statements, Ratiba, global search, validation prefs, OTP.
import ActivityMonitor from "./pages/activity/ActivityMonitor";
import GpsTracker from "./pages/field/GpsTracker";
import SiteVisits from "./pages/field/SiteVisits";
import FaceValidation from "./pages/kyc/FaceValidation";
import SecurityConfig from "./pages/security/SecurityConfig";
import DeviceManagement from "./pages/security/DeviceManagement";
import OtpHistory from "./pages/security/OtpHistory";
import BankStatements from "./pages/collections/BankStatements";
import Ratiba from "./pages/collections/Ratiba";
import SearchResults from "./pages/search/SearchResults";
import ValidationPrefs from "./pages/admin/ValidationPrefs";
import { Spinner } from "./components/ui";
import Toaster from "./components/Toaster";
// WP13: the Finyl command centre.
import CommandCentre from "./pages/platform/CommandCentre";
import DcpDetail from "./pages/platform/DcpDetail";
import PlatformBilling from "./pages/platform/Billing";
import PlatformLending from "./pages/platform/Lending";
import PlatformPipeline from "./pages/platform/Pipeline";
import PlatformAlerts from "./pages/platform/Alerts";
import PlatformPeople from "./pages/platform/People";
// WP14 — ERP accounting
import BooksOverview from "./pages/books/Overview";
import BooksJournals from "./pages/books/Journals";
import BooksReports from "./pages/books/Reports";
import BooksPayables from "./pages/books/Payables";
import BooksClaims from "./pages/books/Claims";
import BooksReconciliation from "./pages/books/Reconciliation";
import BooksAssets from "./pages/books/Assets";
import BooksBudgets from "./pages/books/Budgets";
import BooksClose from "./pages/books/Close";
import BooksSettings from "./pages/books/Settings";
// WP15 — HR and payroll
import HrOverview from "./pages/hr/Overview";
import HrEmployees, { EmployeeProfile } from "./pages/hr/Employees";
import HrLeave from "./pages/hr/Leave";
import HrAttendance from "./pages/hr/Attendance";
import HrPayroll, { P9, PayrollRun, Payslip } from "./pages/hr/Payroll";
import HrAppraisals from "./pages/hr/Appraisals";
import HrSettings from "./pages/hr/Settings";
import MyHr from "./pages/hr/MyHr";
// WP16 — lending additions
import LoanRequests from "./pages/lending/LoanRequests";
import CollateralPage from "./pages/lending/Collateral";
import GroupsPage, { GroupDetail } from "./pages/lending/Groups";
// WP17 — CRM expansion
import CrmTasks from "./pages/crm/Tasks";
import CrmSegments from "./pages/crm/Segments";
import CrmCampaigns, { CampaignDetail } from "./pages/crm/Campaigns";
import CrmReport from "./pages/crm/Report";

// Ordered fallbacks: first module the user can access becomes their home page.
const HOME_ORDER = [
  ["dashboard", "/"], ["lending", "/clients"], ["call_center", "/call-center"],
  ["complaints", "/complaints"], ["crm", "/crm"], ["payments", "/payments"],
];

// Permission-based home fallbacks for RBAC roles with no enabled module dashboard
// (e.g. disbursement/reconciliation/HQ ops/system-admin land on a valid screen).
const PERM_HOME = [
  ["dashboard.company", "/dashboard/director"],
  ["users.view", "/access/users"],
  ["loans.approve", "/approvals"], ["clients.approve", "/approvals"],
  ["disburse.approve", "/approvals"], ["refund.approve", "/approvals"], ["reconcile.approve", "/approvals"],
  ["reports.export", "/reporting"],
  ["loans.view_portfolio", "/loans"], ["clients.view_portfolio", "/clients"],
];

function homePath(canAccess, can, role) {
  for (const [mod, path] of HOME_ORDER) if (canAccess(mod)) return path;
  for (const [perm, path] of PERM_HOME) if (can(perm)) return path;
  return role === "super_admin" ? "/admin" : "/login";
}

function Guard({ module: mod, children }) {
  const { user, loading, canAccess } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  if (mod && !canAccess(mod)) {
    return (
      <div className="card p-8 text-center max-w-md mx-auto mt-10">
        <div className="text-3xl mb-2">🔒</div>
        <h2 className="font-bold text-lg">Module not enabled</h2>
        <p className="text-sm text-gray-400 mt-1">
          This module is switched off for your organisation or role. Contact your administrator.
        </p>
      </div>
    );
  }
  return children;
}

function AdminGuard({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== "super_admin") return <Navigate to="/" replace />;
  return children;
}

// Permission-based route guard (RBAC). Accepts one or more permission keys;
// passes if the user holds ANY of them (super_admin always passes).
function PermGuard({ perms, children }) {
  const { user, loading, can } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  const keys = Array.isArray(perms) ? perms : [perms];
  if (!can(...keys)) {
    return (
      <div className="card p-8 text-center max-w-md mx-auto mt-10">
        <div className="text-3xl mb-2">🔒</div>
        <h2 className="font-bold text-lg">Access denied</h2>
        <p className="text-sm text-gray-400 mt-1">
          You do not have permission to view this page. Contact your System Administrator.
        </p>
      </div>
    );
  }
  return children;
}

// WP13: Finyl screens, gated on the person's command-centre switches.
function PlatformGuard({ perm, children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  const ok = user.role === "super_admin" || (user.platform?.permissions || []).includes(perm);
  if (!ok) {
    return (
      <div className="card p-8 text-center max-w-md mx-auto mt-10">
        <div className="text-3xl mb-2">🔒</div>
        <h2 className="font-bold text-lg">Access denied</h2>
        <p className="text-sm text-gray-400 mt-1">This Finyl screen is not switched on for you. Ask the Finyl super admin.</p>
      </div>
    );
  }
  return children;
}

function platformHome(user) {
  const perms = user.platform?.permissions || [];
  const order = [["platform.dashboard", "/platform"], ["platform.revenue", "/platform/billing"],
    ["platform.lending", "/platform/lending"], ["platform.pipeline", "/platform/pipeline"],
    ["platform.alerts", "/platform/alerts"], ["platform.users_manage", "/platform/people"]];
  const hit = order.find(([p]) => perms.includes(p));
  return hit ? hit[1] : null;
}

function HomeRedirect() {
  const { user, loading, canAccess, can } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  // Super-admin lands on the Finyl command centre (all tenants), NOT the
  // per-tenant executive dashboard (WP13).
  if (user.role === "super_admin") return <CommandCentre />;
  // Finyl staff and partners belong to no DCP: their first permitted Finyl screen.
  if (user.role === "platform_user") {
    const home = platformHome(user);
    return home ? <Navigate to={home} replace /> : (
      <div className="card p-8 text-center max-w-md mx-auto mt-10">
        <h2 className="font-bold text-lg">Nothing switched on yet</h2>
        <p className="text-sm text-gray-400 mt-1">Ask the Finyl super admin to give you access.</p>
      </div>
    );
  }
  if (canAccess("dashboard")) {
    // A relationship/loan officer sees their OWN portfolio (their clients +
    // onboarding + loan statuses); everyone else keeps the executive view.
    const officerRoles = ["relationship_officer", "loan_officer"];
    const Home = officerRoles.includes(user.role) ? OfficerDashboard : Dashboard;
    return <Guard module="dashboard"><Home /></Guard>;
  }
  return <Navigate to={homePath(canAccess, can, user.role)} replace />;
}

function Shell() {
  const { user, loading } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  // AUTH-03: a user with a forced password reset cannot enter the app until the
  // password is changed (mirrors the backend middleware gate).
  if (user.force_password_reset) return <Navigate to="/change-password" replace />;
  return <Layout />;
}

export default function App() {
  return (
    <AuthProvider>
      <Toaster />
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/change-password" element={<ChangePassword />} />
          <Route element={<Shell />}>
            <Route index element={<HomeRedirect />} />
            <Route path="/clients" element={<Guard module="lending"><Clients /></Guard>} />
            <Route path="/clients/:id" element={<Guard module="lending"><ClientDetail /></Guard>} />
            {/* Legacy path kept so old bookmarks/links still resolve. */}
            <Route path="/borrowers" element={<Navigate to="/clients" replace />} />
            <Route path="/guarantors" element={<Guard module="lending"><Guarantors /></Guard>} />
            <Route path="/guarantors/:id" element={<Guard module="lending"><GuarantorDetail /></Guard>} />
            <Route path="/loans" element={<Guard module="lending"><Loans /></Guard>} />
            <Route path="/loans/:id" element={<Guard module="lending"><LoanDetail /></Guard>} />
            <Route path="/products" element={<PermGuard perms={["products.manage"]}><Products /></PermGuard>} />
            <Route path="/payments" element={<Guard module="payments"><Payments /></Guard>} />
            <Route path="/payments/suspense" element={<Guard module="payments"><PermGuard perms={["reconcile.execute"]}><Suspense /></PermGuard></Guard>} />
            <Route path="/payments/bank-import" element={<Guard module="payments"><PermGuard perms={["payments.bank_import"]}><BankImport /></PermGuard></Guard>} />
            <Route path="/messaging/opt-outs" element={<PermGuard perms={["messaging.manage"]}><OptOuts /></PermGuard>} />
            <Route path="/accounting" element={<PermGuard perms={["accounting.export"]}><Accounting /></PermGuard>} />
            <Route path="/books" element={<PermGuard perms={["accounting.view"]}><BooksOverview /></PermGuard>} />
            <Route path="/books/journals" element={<PermGuard perms={["accounting.view"]}><BooksJournals /></PermGuard>} />
            <Route path="/books/reports" element={<PermGuard perms={["accounting.view"]}><BooksReports /></PermGuard>} />
            <Route path="/books/payables" element={<PermGuard perms={["accounting.view"]}><BooksPayables /></PermGuard>} />
            <Route path="/books/claims" element={<PermGuard perms={["expenses.submit"]}><BooksClaims /></PermGuard>} />
            <Route path="/books/reconciliation" element={<PermGuard perms={["accounting.view"]}><BooksReconciliation /></PermGuard>} />
            <Route path="/books/assets" element={<PermGuard perms={["accounting.view"]}><BooksAssets /></PermGuard>} />
            <Route path="/books/budgets" element={<PermGuard perms={["accounting.view"]}><BooksBudgets /></PermGuard>} />
            <Route path="/books/close" element={<PermGuard perms={["accounting.view"]}><BooksClose /></PermGuard>} />
            <Route path="/books/settings" element={<PermGuard perms={["accounting.view"]}><BooksSettings /></PermGuard>} />
            <Route path="/hr" element={<PermGuard perms={["hr.view"]}><HrOverview /></PermGuard>} />
            <Route path="/hr/employees" element={<PermGuard perms={["hr.view"]}><HrEmployees /></PermGuard>} />
            <Route path="/hr/employees/:id" element={<PermGuard perms={["hr.view"]}><EmployeeProfile /></PermGuard>} />
            <Route path="/hr/leave" element={<PermGuard perms={["hr.view", "leave.approve"]}><HrLeave /></PermGuard>} />
            <Route path="/hr/attendance" element={<PermGuard perms={["hr.view"]}><HrAttendance /></PermGuard>} />
            <Route path="/hr/payroll" element={<PermGuard perms={["hr.view"]}><HrPayroll /></PermGuard>} />
            <Route path="/hr/payroll/:id" element={<PermGuard perms={["hr.view"]}><PayrollRun /></PermGuard>} />
            <Route path="/hr/payslips/:id" element={<PermGuard perms={["hr.self"]}><Payslip /></PermGuard>} />
            <Route path="/hr/p9/:id" element={<PermGuard perms={["hr.self"]}><P9 /></PermGuard>} />
            <Route path="/hr/appraisals" element={<PermGuard perms={["hr.view", "appraisals.manage"]}><HrAppraisals /></PermGuard>} />
            <Route path="/hr/settings" element={<PermGuard perms={["hr.view"]}><HrSettings /></PermGuard>} />
            <Route path="/me/hr" element={<PermGuard perms={["hr.self"]}><MyHr /></PermGuard>} />
            <Route path="/loan-requests" element={<PermGuard perms={["loans.writeoff", "loans.waive"]}><LoanRequests /></PermGuard>} />
            <Route path="/collateral" element={<Guard module="lending"><CollateralPage /></Guard>} />
            <Route path="/groups" element={<Guard module="lending"><GroupsPage /></Guard>} />
            <Route path="/groups/:id" element={<Guard module="lending"><GroupDetail /></Guard>} />
            <Route path="/complaints" element={<Guard module="complaints"><Complaints /></Guard>} />
            <Route path="/crm" element={<Guard module="crm"><Crm /></Guard>} />
            <Route path="/crm/tasks" element={<Guard module="crm"><PermGuard perms={["crm.view"]}><CrmTasks /></PermGuard></Guard>} />
            <Route path="/crm/segments" element={<Guard module="crm"><PermGuard perms={["crm.view"]}><CrmSegments /></PermGuard></Guard>} />
            <Route path="/crm/campaigns" element={<Guard module="crm"><PermGuard perms={["crm.view"]}><CrmCampaigns /></PermGuard></Guard>} />
            <Route path="/crm/campaigns/:id" element={<Guard module="crm"><PermGuard perms={["crm.view"]}><CampaignDetail /></PermGuard></Guard>} />
            <Route path="/crm/report" element={<Guard module="crm"><PermGuard perms={["crm.view"]}><CrmReport /></PermGuard></Guard>} />
            <Route path="/call-center" element={<Guard module="call_center"><CallCenter /></Guard>} />
            <Route path="/impact" element={<Guard module="impact"><Impact /></Guard>} />
            <Route path="/cbk" element={<Guard module="cbk_reporting"><Cbk /></Guard>} />
            {/* Phase 3: collections, field ops, KYC escalations, client edits, executive dashboard */}
            <Route path="/collections" element={<Guard module="lending"><PermGuard perms={["collections.ptp_manage", "collections.efficiency_view"]}><Collections /></PermGuard></Guard>} />
            <Route path="/collections/efficiency" element={<Guard module="lending"><PermGuard perms={["collections.efficiency_view"]}><Efficiency /></PermGuard></Guard>} />
            <Route path="/field" element={<Guard module="lending"><PermGuard perms={["field_ops.tasks_manage", "field_ops.gps_view"]}><FieldOps /></PermGuard></Guard>} />
            <Route path="/kyc-escalations" element={<Guard module="lending"><PermGuard perms={["kyc.escalate", "kyc.resolve"]}><KycEscalations /></PermGuard></Guard>} />
            <Route path="/client-edits" element={<Guard module="lending"><PermGuard perms={["client_edits.request", "client_edits.approve_primary", "client_edits.approve_secondary"]}><ClientEdits /></PermGuard></Guard>} />
            <Route path="/dashboard/executive" element={<Guard module="dashboard"><PermGuard perms={["dashboard.company"]}><ExecutiveDashboard /></PermGuard></Guard>} />
            <Route path="/dashboard/director" element={<PermGuard perms={["dashboard.company"]}><DirectorDashboard /></PermGuard>} />
            <Route path="/access/api-clients" element={<AdminGuard><ApiClients /></AdminGuard>} />
            {/* Phase 4: activity, field GPS/site visits, face validation, security,
                bank statements, Ratiba, global search, validation prefs, devices, OTP */}
            <Route path="/activity-monitor" element={<Guard module="lending"><PermGuard perms={["activity.view"]}><ActivityMonitor /></PermGuard></Guard>} />
            <Route path="/gps-tracking" element={<Guard module="lending"><PermGuard perms={["field_ops.gps_view"]}><GpsTracker /></PermGuard></Guard>} />
            <Route path="/site-visits" element={<Guard module="lending"><PermGuard perms={["field_ops.tasks_manage"]}><SiteVisits /></PermGuard></Guard>} />
            <Route path="/face-validation" element={<PermGuard perms={["clients.edit", "clients.create"]}><FaceValidation /></PermGuard>} />
            <Route path="/bank-statements" element={<Guard module="lending"><PermGuard perms={["collections.ptp_manage"]}><BankStatements /></PermGuard></Guard>} />
            <Route path="/ratiba" element={<Guard module="lending"><PermGuard perms={["collections.ptp_manage"]}><Ratiba /></PermGuard></Guard>} />
            <Route path="/search" element={<PermGuard perms={["search.global"]}><SearchResults /></PermGuard>} />
            <Route path="/security-config" element={<PermGuard perms={["security.manage"]}><SecurityConfig /></PermGuard>} />
            <Route path="/validation-preferences" element={<PermGuard perms={["security.manage"]}><ValidationPrefs /></PermGuard>} />
            <Route path="/devices" element={<PermGuard perms={["security.manage"]}><DeviceManagement /></PermGuard>} />
            <Route path="/otp-history" element={<PermGuard perms={["security.manage"]}><OtpHistory /></PermGuard>} />
            {/* RBAC: approvals, administration & reporting (permission-gated) */}
            <Route path="/approvals" element={<PermGuard perms={["loans.approve", "clients.approve", "disburse.approve", "refund.approve", "reconcile.approve"]}><Approvals /></PermGuard>} />
            <Route path="/access/users" element={<PermGuard perms={["users.view"]}><Users /></PermGuard>} />
            <Route path="/access/roles" element={<PermGuard perms={["roles.view"]}><RolesPermissions /></PermGuard>} />
            <Route path="/access/org" element={<PermGuard perms={["org.view"]}><BranchesRegions /></PermGuard>} />
            <Route path="/access/thresholds" element={<PermGuard perms={["thresholds.view"]}><Thresholds /></PermGuard>} />
            <Route path="/access/payments" element={<AdminGuard><PaymentUpload /></AdminGuard>} />
            <Route path="/access/backups" element={<AdminGuard><Backups /></AdminGuard>} />
            <Route path="/access/audit" element={<PermGuard perms={["audit.view"]}><AuditLog /></PermGuard>} />
            <Route path="/reporting" element={<PermGuard perms={["reports.export", "reports.schedule", "reports.template", "reports.flag"]}><Reporting /></PermGuard>} />
            {/* Per-DCP configuration — DCP's own admin (system_admin holds thresholds.manage) */}
            <Route path="/settings" element={<PermGuard perms={["thresholds.manage"]}><Configuration /></PermGuard>} />
            {/* WP13: Finyl command centre */}
            <Route path="/platform" element={<PlatformGuard perm="platform.dashboard"><CommandCentre /></PlatformGuard>} />
            <Route path="/platform/dcp/:id" element={<PlatformGuard perm="platform.dashboard"><DcpDetail /></PlatformGuard>} />
            <Route path="/platform/billing" element={<PlatformGuard perm="platform.revenue"><PlatformBilling /></PlatformGuard>} />
            <Route path="/platform/lending" element={<PlatformGuard perm="platform.lending"><PlatformLending /></PlatformGuard>} />
            <Route path="/platform/pipeline" element={<PlatformGuard perm="platform.pipeline"><PlatformPipeline /></PlatformGuard>} />
            <Route path="/platform/alerts" element={<PlatformGuard perm="platform.alerts"><PlatformAlerts /></PlatformGuard>} />
            <Route path="/platform/people" element={<PlatformGuard perm="platform.users_manage"><PlatformPeople /></PlatformGuard>} />
            <Route path="/platform/classic" element={<AdminGuard><SuperAdminDashboard /></AdminGuard>} />
            <Route path="/admin" element={<AdminGuard><Admin /></AdminGuard>} />
            <Route path="/integrations" element={<AdminGuard><Integrations /></AdminGuard>} />
            <Route path="/approver-config" element={<AdminGuard><ApproverConfig /></AdminGuard>} />
            <Route path="/messaging" element={<AdminGuard><Messaging /></AdminGuard>} />
            {/* Legacy path — DCP Setup was absorbed into the Integrations module. */}
            <Route path="/dcp-setup" element={<Navigate to="/integrations" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
