// App shell: charcoal sidebar with grouped nav (feature-flag + role aware),
// topbar with tenant switcher (super_admin), mobile hamburger, AI panel launcher.
// Desktop sidebar is collapsible to an icon rail (state persisted in localStorage).
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import ErrorBoundary from "./ErrorBoundary";
import { useAuth } from "../hooks/useAuth";
import { api } from "../lib/api";
import AiPanel from "./AiPanel";
import GlobalSearch from "./GlobalSearch";

// Nav items gate on a tenant module flag (`module`) and/or a permission key
// (`perm` = require one, `anyPerm` = hold any of). Items with neither are always shown.
const NAV = [
  { group: "Overview", items: [
    { to: "/", label: "Dashboard", module: "dashboard", icon: "▦" },
    { to: "/dashboard/director", label: "Director View", icon: "📊", anyPerm: ["dashboard.company"] },
  ]},
  { group: "Transactions", items: [
    { to: "/clients", label: "Clients", module: "lending", icon: "👥",
      anyPerm: ["clients.view_all", "clients.view_portfolio", "clients.create", "clients.edit"] },
    { to: "/loans", label: "Loans", module: "lending", icon: "📋",
      anyPerm: ["loans.view_all", "loans.view_portfolio", "loans.create"] },
    { to: "/payments", label: "Payments & SMS", module: "payments", icon: "₿" },
    { to: "/payments/suspense", label: "Suspense Account", module: "payments", icon: "⏳", anyPerm: ["reconcile.execute"] },
  ]},
  { group: "Registry", items: [
    { to: "/guarantors", label: "Guarantors", module: "lending", icon: "🤝",
      anyPerm: ["guarantors.manage", "clients.view_all", "loans.view_all"] },
    { to: "/client-edits", label: "Edit Requests", icon: "✏️",
      anyPerm: ["client_edits.request", "client_edits.approve_secondary", "client_edits.approve_primary"] },
    { to: "/kyc-escalations", label: "KYC Escalations", icon: "🔍",
      anyPerm: ["kyc.escalate", "kyc.resolve"] },
    { to: "/face-validation", label: "Face Validation", icon: "🧑‍💼",
      anyPerm: ["clients.edit", "clients.create"] },
  ]},
  { group: "Field & Collections", items: [
    { to: "/field", label: "Field Ops", icon: "📍",
      anyPerm: ["field_ops.tasks_manage", "field_ops.gps_view"] },
    { to: "/gps-tracking", label: "GPS Tracking", icon: "🛰",
      anyPerm: ["field_ops.gps_view"] },
    { to: "/site-visits", label: "Site Visits", icon: "🗺",
      anyPerm: ["field_ops.tasks_manage"] },
    { to: "/collections", label: "Collections", icon: "💼",
      anyPerm: ["collections.ptp_manage", "collections.efficiency_view"] },
    { to: "/collections/efficiency", label: "Efficiency", icon: "📈",
      anyPerm: ["collections.efficiency_view"] },
    { to: "/bank-statements", label: "Bank Statements", icon: "🏦",
      anyPerm: ["collections.ptp_manage"] },
    { to: "/ratiba", label: "M-Pesa Ratiba", icon: "🔁",
      anyPerm: ["collections.ptp_manage"] },
  ]},
  { group: "Security & Monitoring", items: [
    { to: "/activity-monitor", label: "Activity Monitor", icon: "🖥", anyPerm: ["activity.view"] },
  ]},
  { group: "Approvals", items: [
    { to: "/approvals", label: "Approvals Inbox", icon: "✅",
      anyPerm: ["loans.approve", "clients.approve", "disburse.approve", "refund.approve"] },
  ]},
  { group: "Engagement", items: [
    { to: "/crm", label: "CRM Pipeline", module: "crm", icon: "🧭",
      anyPerm: ["clients.approve", "clients.create"] },
    { to: "/call-center", label: "Call Center", module: "call_center", icon: "☎" },
    { to: "/complaints", label: "Complaints", module: "complaints", icon: "⚠" },
    { to: "/impact", label: "Impact & Investors", module: "impact", icon: "🌱" },
  ]},
  { group: "Reporting", items: [
    { to: "/reporting", label: "HQ Reporting", icon: "📊",
      anyPerm: ["reports.export", "reports.schedule", "reports.template", "reports.flag"] },
    { to: "/accounting", label: "Accounting & GL", icon: "📒", anyPerm: ["accounting.export"] },
  ]},
  { group: "Messaging", items: [
    { to: "/messaging/opt-outs", label: "SMS Opt-Outs", icon: "🔕", anyPerm: ["messaging.manage"] },
  ]},
  { group: "Compliance", items: [
    { to: "/cbk", label: "CBK Reporting", module: "cbk_reporting", icon: "🏛" },
  ]},
  { group: "Settings", items: [
    { to: "/settings", label: "DCP Configuration", icon: "🛠", anyPerm: ["thresholds.manage"] },
  ]},
  { group: "Administration", superOnly: true, items: [
    { to: "/access/users", label: "Users & Access", icon: "👤" },
    { to: "/access/roles", label: "Roles & Permissions", icon: "🔑" },
    { to: "/access/org", label: "Branches & Regions", icon: "🏢" },
    { to: "/access/thresholds", label: "Approval Thresholds", icon: "⚖" },
    { to: "/access/payments", label: "Payment Upload", icon: "📥" },
    { to: "/access/backups", label: "Backups & Integrity", icon: "🗄" },
    { to: "/access/audit", label: "Audit Trail", icon: "📜" },
    { to: "/access/api-clients", label: "API Clients", icon: "🔑" },
    { to: "/messaging", label: "SMS Messaging", icon: "✉" },
    { to: "/security-config", label: "Security Config", icon: "🛡" },
    { to: "/validation-preferences", label: "Validation Preferences", icon: "☑" },
    { to: "/devices", label: "Device Management", icon: "📱" },
    { to: "/otp-history", label: "OTP History", icon: "🔐" },
  ]},
  { group: "Configuration", superOnly: true, items: [
    { to: "/products", label: "Loan Products", icon: "⚙" },
  ]},
];

// Extra links shown to super_admin under a "Platform" group.
const PLATFORM = [
  { to: "/admin", label: "Super Admin", icon: "🛠" },
  { to: "/integrations", label: "Integrations", icon: "🔌" },
  { to: "/approver-config", label: "Approver Config", icon: "✅" },
];

export default function Layout() {
  const { user, logout, canAccess, can, switchTenant } = useAuth();
  const { pathname } = useLocation();            // resets the error boundary per route
  const [open, setOpen] = useState(false);       // mobile sidebar
  const [aiOpen, setAiOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [tenants, setTenants] = useState([]);
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("finyl.sidebar.collapsed") === "1"
  );
  const isAdmin = user?.role === "super_admin";

  useEffect(() => {
    if (isAdmin) api("/api/v1/auth/tenants").then(setTenants).catch(() => {});
  }, [isAdmin]);

  useEffect(() => {
    localStorage.setItem("finyl.sidebar.collapsed", collapsed ? "1" : "0");
  }, [collapsed]);

  // ⌘K / Ctrl+K opens the global search palette (component handles Esc to close).
  useEffect(() => {
    const h = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  // A single nav row — shows icon + label, or an icon-only rail when `mini`.
  const navClass = (mini) => ({ isActive }) =>
    `group relative flex items-center rounded-lg text-sm font-medium mb-0.5 transition-colors ${
      mini ? "justify-center px-0 py-2.5" : "gap-2.5 px-3 py-2"
    } ${
      isActive
        ? "bg-white/10 text-white before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:h-5 before:w-1 before:rounded-r before:bg-accent"
        : "text-gray-300 hover:bg-white/10 hover:text-white"
    }`;

  // `mini` collapses to an icon rail (desktop only); the mobile drawer always passes false.
  const NavItems = ({ mini = false }) => (
    <nav className={`flex-1 overflow-y-auto pb-4 space-y-4 ${mini ? "px-2" : "px-3"}`}>
      {NAV.map((g) => {
        if (g.superOnly && !isAdmin) return null;
        const items = g.items.filter((i) =>
          (i.module ? canAccess(i.module) : true) &&
          (i.perm ? can(i.perm) : true) &&
          (i.anyPerm ? can(...i.anyPerm) : true));
        if (!items.length) return null;
        return (
          <div key={g.group}>
            {!mini && (
              <div className="px-2 pb-1 text-[10px] font-bold uppercase tracking-widest text-gray-400">{g.group}</div>
            )}
            {items.map((i) => (
              <NavLink key={i.to} to={i.to} end={i.to === "/"} onClick={() => setOpen(false)}
                title={mini ? i.label : undefined} className={navClass(mini)}>
                <span className="w-5 text-center text-base leading-none">{i.icon}</span>
                {!mini && <span>{i.label}</span>}
              </NavLink>
            ))}
          </div>
        );
      })}
      {isAdmin && (
        <div>
          {!mini && (
            <div className="px-2 pb-1 text-[10px] font-bold uppercase tracking-widest text-gray-400">Platform</div>
          )}
          {PLATFORM.map((i) => (
            <NavLink key={i.to} to={i.to} onClick={() => setOpen(false)}
              title={mini ? i.label : undefined} className={navClass(mini)}>
              <span className="w-5 text-center text-base leading-none">{i.icon}</span>
              {!mini && <span>{i.label}</span>}
            </NavLink>
          ))}
        </div>
      )}
    </nav>
  );

  // `mini` renders the collapsed icon rail; used on desktop only.
  const Sidebar = ({ mini = false }) => (
    <div className="flex flex-col h-full bg-charcoal">
      <div className={`flex items-center py-5 ${mini ? "justify-center px-2" : "gap-2.5 px-5"}`}>
        <div
          className={`w-9 h-9 rounded-lg flex items-center justify-center font-extrabold text-white shrink-0 ${
            user?.tenant_color ? "" : "bg-brand-gradient"
          }`}
          style={user?.tenant_color ? { background: user.tenant_color } : undefined}
        >F</div>
        {!mini && (
          <div className="min-w-0">
            <div className="text-white font-extrabold leading-tight truncate">Finyl-DCP</div>
            <div className="text-[10px] text-gray-400 uppercase tracking-wider truncate">{user?.tenant_name || "Platform"}</div>
          </div>
        )}
      </div>
      {/* Plain function call (not <NavItems/>) so the <nav> DOM node is stable
          across Layout re-renders and its scroll position is preserved. */}
      {NavItems({ mini })}
      {/* Collapse toggle — desktop only */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        title={mini ? "Expand sidebar" : "Collapse sidebar"}
        className={`hidden lg:flex items-center gap-2 border-t border-white/10 text-gray-400 hover:text-white hover:bg-white/5 transition-colors py-3 ${
          mini ? "justify-center px-2" : "px-5"
        }`}
      >
        <span className="text-sm leading-none">{mini ? "»" : "«"}</span>
        {!mini && <span className="text-xs font-medium">Collapse</span>}
      </button>
    </div>
  );

  const initials = (user?.full_name || "")
    .split(" ").filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

  return (
    <div className="min-h-screen flex">
      {/* Desktop sidebar */}
      <aside className={`hidden lg:block fixed inset-y-0 transition-[width] duration-200 ${collapsed ? "w-[68px]" : "w-60"}`}>
        {Sidebar({ mini: collapsed })}
      </aside>
      {/* Mobile drawer (always full width, never mini) */}
      {open && (
        <div className="lg:hidden fixed inset-0 z-40">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 animate-slide-up">{Sidebar({})}</aside>
        </div>
      )}

      <div className={`flex-1 flex flex-col min-w-0 transition-[margin] duration-200 ${collapsed ? "lg:ml-[68px]" : "lg:ml-60"}`}>
        {/* Topbar */}
        <header className="sticky top-0 z-30 bg-surface/90 backdrop-blur border-b border-border px-4 py-3 flex items-center gap-3">
          <button className="lg:hidden btn-ghost !px-2.5" onClick={() => setOpen(true)}>☰</button>
          {/* Global search launcher (⌘K) */}
          <button
            onClick={() => setSearchOpen(true)}
            className="hidden md:flex items-center gap-2 w-72 max-w-full rounded-xl border border-border bg-canvas px-3 py-1.5 text-gray-400 hover:border-accent/40 transition-colors"
          >
            <span className="text-sm leading-none">🔍</span>
            <span className="flex-1 text-left text-sm">Search…</span>
            <kbd className="text-[10px] font-semibold border border-border rounded px-1.5 py-0.5">⌘K</kbd>
          </button>
          <button className="md:hidden btn-ghost !px-2.5" onClick={() => setSearchOpen(true)}>🔍</button>
          <div className="flex-1" />
          {isAdmin && tenants.length > 0 && (
            <select className="input !w-auto text-sm" value={user?.tenant_id || ""}
              onChange={(e) => switchTenant(e.target.value)}>
              {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          )}
          {(user?.role === "tenant_admin" || isAdmin) && user?.modules?.ai_agent && (
            <button className="btn-primary !py-1.5" onClick={() => setAiOpen(true)}>✦ AI Agent</button>
          )}
          <div className="text-right hidden sm:block">
            <div className="text-sm font-semibold leading-tight">{user?.full_name}</div>
            <div className="text-[11px] text-gray-400 capitalize">{user?.role?.replace("_", " ")}</div>
          </div>
          <div
            className="hidden sm:flex w-9 h-9 rounded-full bg-brand-gradient text-white items-center justify-center text-xs font-bold shrink-0"
            title={user?.full_name}
          >{initials}</div>
          <button className="btn-ghost !py-1.5" onClick={logout}>Logout</button>
        </header>

        <main className="flex-1 p-4 md:p-6 max-w-[1500px] w-full mx-auto">
          <ErrorBoundary key={pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>

      {aiOpen && <AiPanel onClose={() => setAiOpen(false)} />}
      <GlobalSearch open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  );
}
