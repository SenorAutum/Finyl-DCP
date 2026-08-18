// Login page with Finyl-DCP branding + demo credentials.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

const DEMO = [
  ["superadmin@finyl.app", "Super Admin (all tenants)"],
  ["admin@mularcredit.co.ke", "Tenant Admin — Mular Credit"],
  ["sysadmin@mularcredit.co.ke", "System Admin — users, access, thresholds, audit"],
  ["ro@mularcredit.co.ke", "Relationship Officer — own portfolio, initiate loans"],
  ["branchmgr@mularcredit.co.ke", "Branch Manager — approvals inbox (branch)"],
  ["regionalmgr@mularcredit.co.ke", "Regional Manager — approvals (region)"],
  ["disburse@mularcredit.co.ke", "Disbursement Officer — B2C disburse (maker-checker)"],
  ["reconcile@mularcredit.co.ke", "Reconciliation Officer — reconcile & refunds"],
  ["hqops@mularcredit.co.ke", "HQ Operations — read-only dashboards & reports"],
  ["admin@jengamicro.co.ke", "Tenant Admin — Jenga Micro (CRM & Impact disabled)"],
];

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("admin@mularcredit.co.ke");
  const [password, setPassword] = useState("Finyl@2026");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const res = await login(email, password);
      nav(res?.force_password_reset ? "/change-password" : "/");
    }
    catch (ex) { setErr(ex.detail || "Login failed"); }
    finally { setBusy(false); }
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-2">
      <div className="hidden lg:flex flex-col justify-between bg-charcoal p-10 text-white">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-accent flex items-center justify-center font-extrabold text-lg">F</div>
          <span className="text-xl font-extrabold">Finyl-DCP</span>
        </div>
        <div>
          <h1 className="text-4xl font-extrabold leading-tight">The operating system for<br /><span className="text-accent">Digital Credit Providers</span>.</h1>
          <p className="mt-4 text-gray-400 max-w-md">Lending engine, M-Pesa integration hub, executive analytics, consumer protection, CRM, collections scorecards, social-impact reporting and CBK compliance — one multi-tenant platform.</p>
        </div>
        <div className="text-xs text-gray-500">© 2026 Finyl-DCP · Built for Kenya's DCP ecosystem</div>
      </div>

      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          <div className="lg:hidden flex items-center gap-2.5 mb-6">
            <div className="w-9 h-9 rounded-lg bg-accent flex items-center justify-center font-extrabold text-white">F</div>
            <span className="text-lg font-extrabold">Finyl-DCP</span>
          </div>
          <h2 className="text-2xl font-extrabold">Sign in</h2>
          <p className="text-sm text-gray-500 mb-6">Access your DCP workspace</p>
          <form onSubmit={submit} className="space-y-4">
            <div><label className="label">Email</label>
              <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" /></div>
            <div><label className="label">Password</label>
              <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></div>
            {err && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err}</div>}
            <button className="btn-primary w-full justify-center" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
          </form>

          <div className="mt-6 card p-4">
            <div className="text-xs font-bold uppercase tracking-wider text-gray-500 mb-2">Demo credentials · password <span className="text-accent">Finyl@2026</span></div>
            <div className="space-y-1.5">
              {DEMO.map(([em, label]) => (
                <button key={em} className="w-full text-left text-xs px-2.5 py-1.5 rounded-lg hover:bg-canvas flex justify-between gap-2"
                  onClick={() => { setEmail(em); setPassword("Finyl@2026"); }}>
                  <span className="font-mono text-teal">{em}</span>
                  <span className="text-gray-400 text-right">{label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
