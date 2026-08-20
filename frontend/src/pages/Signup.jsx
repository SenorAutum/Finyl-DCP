// Self-service DCP (tenant) signup / onboarding page.
// Registers a brand-new Digital Credit Provider and its first administrator,
// then routes to the login page. Mirrors the two-column style of Login.jsx.
import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { api } from "../lib/api";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export default function Signup() {
  const nav = useNavigate();
  const [org, setOrg] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const validate = () => {
    if (!org.trim()) return "Organization name is required";
    if (org.trim().length > 120) return "Organization name must be 120 characters or fewer";
    if (!fullName.trim()) return "Administrator full name is required";
    if (!EMAIL_RE.test(email.trim())) return "A valid administrator email address is required";
    if (password.length < 8) return "Password must be at least 8 characters";
    if (password.length > 128) return "Password must be 128 characters or fewer";
    if (password !== confirm) return "Passwords do not match";
    return "";
  };

  const submit = async (e) => {
    e.preventDefault();
    const v = validate();
    if (v) { setErr(v); return; }
    setBusy(true); setErr("");
    try {
      await api("/api/v1/auth/signup", {
        method: "POST",
        body: {
          organization_name: org.trim(),
          admin_full_name: fullName.trim(),
          admin_email: email.trim(),
          password,
          confirm_password: confirm,
        },
      });
      nav("/login", {
        state: { signupSuccess: "Account created — sign in with your new credentials." },
      });
    }
    catch (ex) { setErr(ex.detail || "Signup failed. Please try again."); }
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
          <h1 className="text-4xl font-extrabold leading-tight">Launch your<br /><span className="text-accent">Digital Credit Provider</span> in minutes.</h1>
          <p className="mt-4 text-gray-400 max-w-md">Register your organization to get a dedicated, fully isolated workspace — lending engine, M-Pesa integration, analytics, CRM, collections and CBK compliance, all switched on and ready.</p>
        </div>
        <div className="text-xs text-gray-500">© 2026 Finyl-DCP · Built for Kenya's DCP ecosystem</div>
      </div>

      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          <div className="lg:hidden flex items-center gap-2.5 mb-6">
            <div className="w-9 h-9 rounded-lg bg-accent flex items-center justify-center font-extrabold text-white">F</div>
            <span className="text-lg font-extrabold">Finyl-DCP</span>
          </div>
          <h2 className="text-2xl font-extrabold">Create your DCP account</h2>
          <p className="text-sm text-gray-500 mb-6">Register your organization and its first administrator</p>
          <form onSubmit={submit} className="space-y-4">
            <div><label className="label">Organization name</label>
              <input className="input" value={org} onChange={(e) => setOrg(e.target.value)}
                placeholder="e.g. Acme Credit Ltd" autoComplete="organization" /></div>
            <div><label className="label">Administrator full name</label>
              <input className="input" value={fullName} onChange={(e) => setFullName(e.target.value)}
                placeholder="e.g. Jane Wanjiru" autoComplete="name" /></div>
            <div><label className="label">Administrator email</label>
              <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                placeholder="you@yourcompany.co.ke" autoComplete="email" /></div>
            <div><label className="label">Password</label>
              <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password" /></div>
            <div><label className="label">Confirm password</label>
              <input className="input" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password" /></div>
            <p className="text-xs text-gray-400">Password must be at least 8 characters.</p>
            {err && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err}</div>}
            <button className="btn-primary w-full justify-center" disabled={busy}>{busy ? "Creating account…" : "Create account"}</button>
          </form>

          <p className="mt-6 text-sm text-gray-500 text-center">
            Already have an account?{" "}
            <Link to="/login" className="text-accent font-semibold hover:underline">Sign in</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
