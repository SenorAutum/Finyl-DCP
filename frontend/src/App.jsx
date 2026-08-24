// Route table with auth + feature-flag guards. Disabled modules are hidden from
// nav (Layout) AND blocked here, mirroring the backend's require_module() 403s.
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import ChangePassword from "./pages/ChangePassword";
import Dashboard from "./pages/dashboard/Dashboard";
import Clients from "./pages/clients/Clients";
import ClientDetail from "./pages/clients/ClientDetail";
import Loans from "./pages/lending/Loans";
import LoanDetail from "./pages/lending/LoanDetail";
import Products from "./pages/lending/Products";
import Payments from "./pages/payments/Payments";
import Suspense from "./pages/payments/Suspense";
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
import { Spinner } from "./components/ui";

// Ordered fallbacks: first module the user can access becomes their home page.
const HOME_ORDER = [
  ["dashboard", "/"], ["lending", "/clients"], ["call_center", "/call-center"],
  ["complaints", "/complaints"], ["crm", "/crm"], ["payments", "/payments"],
];

// Permission-based home fallbacks for RBAC roles with no enabled module dashboard
// (e.g. disbursement/reconciliation/HQ ops/system-admin land on a valid screen).
const PERM_HOME = [
  ["users.view", "/access/users"],
  ["loans.approve", "/approvals"], ["clients.approve", "/approvals"],
  ["disburse.approve", "/approvals"], ["refund.approve", "/approvals"],
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

function HomeRedirect() {
  const { user, loading, canAccess, can } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  if (canAccess("dashboard")) return <Guard module="dashboard"><Dashboard /></Guard>;
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
            <Route path="/loans" element={<Guard module="lending"><Loans /></Guard>} />
            <Route path="/loans/:id" element={<Guard module="lending"><LoanDetail /></Guard>} />
            <Route path="/products" element={<AdminGuard><Products /></AdminGuard>} />
            <Route path="/payments" element={<Guard module="payments"><Payments /></Guard>} />
            <Route path="/payments/suspense" element={<Guard module="payments"><PermGuard perms={["reconcile.execute"]}><Suspense /></PermGuard></Guard>} />
            <Route path="/messaging/opt-outs" element={<PermGuard perms={["messaging.manage"]}><OptOuts /></PermGuard>} />
            <Route path="/accounting" element={<PermGuard perms={["accounting.export"]}><Accounting /></PermGuard>} />
            <Route path="/complaints" element={<Guard module="complaints"><Complaints /></Guard>} />
            <Route path="/crm" element={<Guard module="crm"><Crm /></Guard>} />
            <Route path="/call-center" element={<Guard module="call_center"><CallCenter /></Guard>} />
            <Route path="/impact" element={<Guard module="impact"><Impact /></Guard>} />
            <Route path="/cbk" element={<Guard module="cbk_reporting"><Cbk /></Guard>} />
            {/* RBAC: approvals, administration & reporting (permission-gated) */}
            <Route path="/approvals" element={<PermGuard perms={["loans.approve", "clients.approve", "disburse.approve", "refund.approve"]}><Approvals /></PermGuard>} />
            <Route path="/access/users" element={<AdminGuard><Users /></AdminGuard>} />
            <Route path="/access/roles" element={<AdminGuard><RolesPermissions /></AdminGuard>} />
            <Route path="/access/org" element={<AdminGuard><BranchesRegions /></AdminGuard>} />
            <Route path="/access/thresholds" element={<AdminGuard><Thresholds /></AdminGuard>} />
            <Route path="/access/payments" element={<AdminGuard><PaymentUpload /></AdminGuard>} />
            <Route path="/access/backups" element={<AdminGuard><Backups /></AdminGuard>} />
            <Route path="/access/audit" element={<AdminGuard><AuditLog /></AdminGuard>} />
            <Route path="/reporting" element={<PermGuard perms={["reports.export", "reports.schedule", "reports.template", "reports.flag"]}><Reporting /></PermGuard>} />
            {/* Per-DCP configuration — DCP's own admin (system_admin holds thresholds.manage) */}
            <Route path="/settings" element={<PermGuard perms={["thresholds.manage"]}><Configuration /></PermGuard>} />
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
