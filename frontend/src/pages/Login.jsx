// Login page with Finyl-DCP branding + demo credentials.
import { useState } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import OtpInput from "../components/OtpInput";

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
  const { login, loginOtp } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const signupSuccess = loc.state?.signupSuccess || "";
  const [email, setEmail] = useState("admin@mularcredit.co.ke");
  const [password, setPassword] = useState("Finyl@2026");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState("password"); // "password" | "otp"
  const [otp, setOtp] = useState("");
  const [otpInfo, setOtpInfo] = useState(null); // { detail, delivery }

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const res = await login(email, password);
      if (res?.otp_required) {
        setOtpInfo({ detail: res.detail, delivery: res.delivery });
        setOtp("");
        setStep("otp");
        return;
      }
      nav(res?.force_password_reset ? "/change-password" : "/");
    }
    catch (ex) { setErr(ex.detail || "Login failed"); }
    finally { setBusy(false); }
  };

  const submitOtp = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const res = await loginOtp(email, otp);
      nav(res?.force_password_reset ? "/change-password" : "/");
    }
    catch (ex) { setErr(ex.detail || "Invalid or expired code"); }
    finally { setBusy(false); }
  };

  const backToPassword = () => { setStep("password"); setErr(""); setOtp(""); setOtpInfo(null); };

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
          <h2 className="text-2xl font-extrabold">{step === "otp" ? "Verify your identity" : "Sign in"}</h2>
          <p className="text-sm text-gray-500 mb-6">{step === "otp" ? "Enter the one-time code to continue" : "Access your DCP workspace"}</p>
          {signupSuccess && step === "password" && <div className="mb-4 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">{signupSuccess}</div>}

          {step === "password" ? (
            <form onSubmit={submit} className="space-y-4">
              <div><label className="label">Email</label>
                <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" /></div>
              <div><label className="label">Password</label>
                <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></div>
              {err && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err}</div>}
              <button className="btn-primary w-full justify-center" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
            </form>
          ) : (
            <form onSubmit={submitOtp} className="space-y-4">
              <div className="text-sm text-gray-600 bg-canvas border border-border rounded-lg px-3 py-2">
                {otpInfo?.detail || "A one-time code was sent to you."}
                {otpInfo?.delivery && <span className="text-gray-400"> · via {otpInfo.delivery}</span>}
              </div>
              <div>
                <label className="label">One-time code</label>
                <OtpInput value={otp} onChange={setOtp} disabled={busy} />
              </div>
              {err && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err}</div>}
              <button className="btn-primary w-full justify-center" disabled={busy || otp.length < 6}>{busy ? "Verifying…" : "Verify & sign in"}</button>
              <button type="button" className="btn-ghost w-full justify-center" onClick={backToPassword} disabled={busy}>← Back</button>
            </form>
          )}

          <p className="mt-4 text-sm text-gray-500 text-center">
            New to Finyl-DCP?{" "}
            <Link to="/signup" className="text-accent font-semibold hover:underline">Create your DCP account</Link>
          </p>

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
