// App shell: charcoal sidebar with grouped nav (feature-flag + role aware),
// topbar with tenant switcher (super_admin), mobile hamburger, AI panel launcher.
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { api } from "../lib/api";
import AiPanel from "./AiPanel";

const NAV = [
  { group: "Overview", items: [
    { to: "/", label: "Dashboard", module: "dashboard", icon: "▦" },
  ]},
  { group: "Transactions", items: [
    { to: "/borrowers", label: "Borrowers", module: "lending", icon: "👥" },
    { to: "/loans", label: "Loans", module: "lending", icon: "📋" },
    { to: "/payments", label: "Payments & SMS", module: "payments", icon: "₿" },
  ]},
  { group: "Engagement", items: [
    { to: "/crm", label: "CRM Pipeline", module: "crm", icon: "🧭" },
    { to: "/call-center", label: "Call Center", module: "call_center", icon: "☎" },
    { to: "/complaints", label: "Complaints", module: "complaints", icon: "⚠" },
    { to: "/impact", label: "Impact & Investors", module: "impact", icon: "🌱" },
  ]},
  { group: "Compliance", items: [
    { to: "/cbk", label: "CBK Reporting", module: "cbk_reporting", icon: "🏛" },
  ]},
  { group: "Configuration", items: [
    { to: "/products", label: "Loan Products", module: "lending", icon: "⚙" },
  ]},
];

export default function Layout() {
  const { user, logout, canAccess, switchTenant } = useAuth();
  const [open, setOpen] = useState(false);       // mobile sidebar
  const [aiOpen, setAiOpen] = useState(false);
  const [tenants, setTenants] = useState([]);
  const isAdmin = user?.role === "super_admin";

  useEffect(() => {
    if (isAdmin) api("/api/v1/auth/tenants").then(setTenants).catch(() => {});
  }, [isAdmin]);

  const NavItems = () => (
    <nav className="flex-1 overflow-y-auto px-3 pb-4 space-y-4">
      {NAV.map((g) => {
        const items = g.items.filter((i) => canAccess(i.module));
        if (!items.length) return null;
        return (
          <div key={g.group}>
            <div className="px-2 pb-1 text-[10px] font-bold uppercase tracking-widest text-gray-400">{g.group}</div>
            {items.map((i) => (
              <NavLink key={i.to} to={i.to} end={i.to === "/"} onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium mb-0.5 transition-colors ${
                    isActive ? "bg-accent text-white" : "text-gray-300 hover:bg-white/10 hover:text-white"}`}>
                <span className="w-4 text-center">{i.icon}</span>{i.label}
              </NavLink>
            ))}
          </div>
        );
      })}
      {isAdmin && (
        <div>
          <div className="px-2 pb-1 text-[10px] font-bold uppercase tracking-widest text-gray-400">Platform</div>
          <NavLink to="/admin" onClick={() => setOpen(false)}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium ${
                isActive ? "bg-accent text-white" : "text-gray-300 hover:bg-white/10 hover:text-white"}`}>
            <span className="w-4 text-center">🛠</span>Super Admin
          </NavLink>
        </div>
      )}
    </nav>
  );

  const Sidebar = () => (
    <div className="flex flex-col h-full bg-charcoal">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <div className="w-9 h-9 rounded-lg flex items-center justify-center font-extrabold text-white" style={{ background: user?.tenant_color || "#10B981" }}>F</div>
        <div>
          <div className="text-white font-extrabold leading-tight">Finyl-DCP</div>
          <div className="text-[10px] text-gray-400 uppercase tracking-wider">{user?.tenant_name || "Platform"}</div>
        </div>
      </div>
      <NavItems />
    </div>
  );

  return (
    <div className="min-h-screen flex">
      {/* Desktop sidebar */}
      <aside className="hidden lg:block w-60 fixed inset-y-0">{Sidebar()}</aside>
      {/* Mobile drawer */}
      {open && (
        <div className="lg:hidden fixed inset-0 z-40">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64">{Sidebar()}</aside>
        </div>
      )}

      <div className="flex-1 lg:ml-60 flex flex-col min-w-0">
        {/* Topbar */}
        <header className="sticky top-0 z-30 bg-surface border-b border-border px-4 py-3 flex items-center gap-3">
          <button className="lg:hidden btn-ghost !px-2.5" onClick={() => setOpen(true)}>☰</button>
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
          <button className="btn-ghost !py-1.5" onClick={logout}>Logout</button>
        </header>

        <main className="flex-1 p-4 md:p-6 max-w-[1500px] w-full mx-auto">
          <Outlet />
        </main>
      </div>

      {aiOpen && <AiPanel onClose={() => setAiOpen(false)} />}
    </div>
  );
}
