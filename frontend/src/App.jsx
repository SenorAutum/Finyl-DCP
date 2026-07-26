// Route table with auth + feature-flag guards. Disabled modules are hidden from
// nav (Layout) AND blocked here, mirroring the backend's require_module() 403s.
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import Login from "./pages/Login";
import Dashboard from "./pages/dashboard/Dashboard";
import Clients from "./pages/clients/Clients";
import ClientDetail from "./pages/clients/ClientDetail";
import Loans from "./pages/lending/Loans";
import LoanDetail from "./pages/lending/LoanDetail";
import Products from "./pages/lending/Products";
import Payments from "./pages/payments/Payments";
import Complaints from "./pages/complaints/Complaints";
import Crm from "./pages/crm/Crm";
import CallCenter from "./pages/callcenter/CallCenter";
import Impact from "./pages/impact/Impact";
import Cbk from "./pages/cbk/Cbk";
import Admin from "./pages/admin/Admin";
import { Spinner } from "./components/ui";

// Ordered fallbacks: first module the user can access becomes their home page.
const HOME_ORDER = [
  ["dashboard", "/"], ["lending", "/clients"], ["call_center", "/call-center"],
  ["complaints", "/complaints"], ["crm", "/crm"], ["payments", "/payments"],
];

function homePath(canAccess, role) {
  for (const [mod, path] of HOME_ORDER) if (canAccess(mod)) return path;
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

function HomeRedirect() {
  const { user, loading, canAccess } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  if (canAccess("dashboard")) return <Guard module="dashboard"><Dashboard /></Guard>;
  return <Navigate to={homePath(canAccess, user.role)} replace />;
}

function Shell() {
  const { user, loading } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  return <Layout />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<Shell />}>
            <Route index element={<HomeRedirect />} />
            <Route path="/clients" element={<Guard module="lending"><Clients /></Guard>} />
            <Route path="/clients/:id" element={<Guard module="lending"><ClientDetail /></Guard>} />
            {/* Legacy path kept so old bookmarks/links still resolve. */}
            <Route path="/borrowers" element={<Navigate to="/clients" replace />} />
            <Route path="/loans" element={<Guard module="lending"><Loans /></Guard>} />
            <Route path="/loans/:id" element={<Guard module="lending"><LoanDetail /></Guard>} />
            <Route path="/products" element={<Guard module="lending"><Products /></Guard>} />
            <Route path="/payments" element={<Guard module="payments"><Payments /></Guard>} />
            <Route path="/complaints" element={<Guard module="complaints"><Complaints /></Guard>} />
            <Route path="/crm" element={<Guard module="crm"><Crm /></Guard>} />
            <Route path="/call-center" element={<Guard module="call_center"><CallCenter /></Guard>} />
            <Route path="/impact" element={<Guard module="impact"><Impact /></Guard>} />
            <Route path="/cbk" element={<Guard module="cbk_reporting"><Cbk /></Guard>} />
            <Route path="/admin" element={<AdminGuard><Admin /></AdminGuard>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
