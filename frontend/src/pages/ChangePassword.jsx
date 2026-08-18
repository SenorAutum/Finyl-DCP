// AUTH-03: forced / self-service password change screen. Shown when a user's
// force_password_reset flag is set (they cannot reach any other screen until
// they change their password) and reachable on demand from /change-password.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { getToken } from "../lib/api";

export default function ChangePassword() {
  const { user, changePassword } = useAuth();
  const nav = useNavigate();
  const forced = !!user?.force_password_reset;
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  // Not signed in at all -> back to login.
  if (!getToken()) { nav("/login", { replace: true }); return null; }

  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    if (next.length < 8) { setErr("New password must be at least 8 characters."); return; }
    if (next !== confirm) { setErr("New password and confirmation do not match."); return; }
    setBusy(true);
    try {
      await changePassword(current, next);
      nav("/", { replace: true });
    } catch (ex) {
      setErr(ex.detail || "Could not change password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6 bg-canvas">
      <div className="w-full max-w-md card p-8">
        <div className="flex items-center gap-2.5 mb-6">
          <div className="w-9 h-9 rounded-lg bg-accent flex items-center justify-center font-extrabold text-white">F</div>
          <span className="text-lg font-extrabold">Finyl-DCP</span>
        </div>
        <h2 className="text-2xl font-extrabold">Change your password</h2>
        {forced ? (
          <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mt-3">
            For security, you must set a new password before you can continue.
          </p>
        ) : (
          <p className="text-sm text-gray-500 mb-2">Update the password for your account.</p>
        )}
        <form onSubmit={submit} className="space-y-4 mt-4">
          <div><label className="label">Current password</label>
            <input className="input" type="password" value={current}
              onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" /></div>
          <div><label className="label">New password</label>
            <input className="input" type="password" value={next}
              onChange={(e) => setNext(e.target.value)} autoComplete="new-password" /></div>
          <div><label className="label">Confirm new password</label>
            <input className="input" type="password" value={confirm}
              onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" /></div>
          {err && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err}</div>}
          <button className="btn-primary w-full justify-center" disabled={busy}>
            {busy ? "Saving…" : "Change password"}
          </button>
        </form>
      </div>
    </div>
  );
}
