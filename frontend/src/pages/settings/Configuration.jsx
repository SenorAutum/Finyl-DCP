// DCP Configuration — self-service settings for a DCP's OWN administrator
// (system_admin), scoped to the caller's own tenant. Tabs: M-Pesa/Daraja
// credentials, SMS automation, approver tiers, maker-checker thresholds, modules.
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { PageHeader, Spinner, Badge } from "../../components/ui";

const TABS = [
  ["daraja", "M-Pesa / Daraja"],
  ["sms", "SMS Automation"],
  ["approvers", "Approver Tiers"],
  ["thresholds", "Maker-Checker Thresholds"],
  ["modules", "Modules"],
];

export default function Configuration() {
  const [tab, setTab] = useState("daraja");
  return (
    <div>
      <PageHeader title="DCP Configuration" crumbs={["Settings", "Configuration"]} />
      <div className="flex flex-wrap gap-1 border-b border-border mb-5">
        {TABS.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-3 py-2 text-sm font-semibold border-b-2 -mb-px transition-colors ${
              tab === k ? "border-accent text-accent" : "border-transparent text-gray-500 hover:text-charcoal"}`}>
            {label}
          </button>
        ))}
      </div>
      {tab === "daraja" && <DarajaTab />}
      {tab === "sms" && <SmsTab />}
      {tab === "approvers" && <ApproversTab />}
      {tab === "thresholds" && <ThresholdsTab />}
      {tab === "modules" && <ModulesTab />}
    </div>
  );
}

function Notice({ msg, tone = "ok" }) {
  if (!msg) return null;
  const cls = tone === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700";
  return <div className={`rounded-lg px-3 py-2 text-sm mb-3 ${cls}`}>{msg}</div>;
}

// --------------------------------------------------------------------------- //
// M-Pesa / Daraja credentials
// --------------------------------------------------------------------------- //
function DarajaTab() {
  const [cfg, setCfg] = useState(null);
  const [form, setForm] = useState({});
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false); const [test, setTest] = useState(null);

  const load = () => api("/api/v1/settings/daraja").then((d) => {
    setCfg(d);
    setForm({ environment: d.environment || "sandbox", shortcode: d.shortcode || "",
              initiator_name: d.initiator_name || "" });
  }).catch((e) => setErr(e.detail || "Failed to load"));
  useEffect(() => { load(); }, []);
  if (!cfg) return <Spinner />;

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const secretRow = (key, label) => {
    const s = cfg.secrets?.[key] || {};
    return (
      <div>
        <label className="text-xs font-semibold text-gray-600">{label}
          {s.configured
            ? <span className="ml-2 text-emerald-600">● set {s.hint ? `(${s.hint})` : ""}</span>
            : <span className="ml-2 text-gray-400">○ not set</span>}
        </label>
        <input type="password" autoComplete="new-password" className="input"
          placeholder={s.configured ? "•••••••• leave blank to keep" : "Enter value"}
          value={form[key] || ""} onChange={(e) => set(key, e.target.value)} />
      </div>
    );
  };

  async function save() {
    setBusy(true); setErr(""); setMsg(""); setTest(null);
    try {
      // Only send secret fields the admin actually typed (blank = keep existing).
      const body = { environment: form.environment, shortcode: form.shortcode,
                     initiator_name: form.initiator_name };
      for (const k of ["consumer_key", "consumer_secret", "passkey", "security_credential"])
        if (form[k]) body[k] = form[k];
      await api("/api/v1/settings/daraja", { method: "PUT", body });
      setMsg("Credentials saved (secrets encrypted at rest).");
      setForm((f) => ({ ...f, consumer_key: "", consumer_secret: "", passkey: "", security_credential: "" }));
      load();
    } catch (e) { setErr(e instanceof ApiError ? e.detail : "Save failed"); }
    finally { setBusy(false); }
  }
  async function runTest() {
    setTest(null); setErr("");
    try { setTest(await api("/api/v1/settings/daraja/test", { method: "POST" })); }
    catch (e) { setErr(e.detail || "Test failed"); }
  }

  const statusTone = { LIVE: "approved", SANDBOX: "underwriting", "NOT CONFIGURED": "pending", ERROR: "failed" };
  return (
    <div className="max-w-2xl">
      <Notice msg={msg} /><Notice msg={err} tone="bad" />
      <div className="card p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-bold">M-Pesa (Daraja) credentials</h3>
          <Badge value={statusTone[cfg.integration_status] || "pending"}>{cfg.integration_status}</Badge>
        </div>
        <p className="text-xs text-gray-500">
          These credentials are used for your DCP's disbursements (B2C) and collections (STK push).
          Secret fields are encrypted at rest and never shown again — leave a secret blank to keep the
          stored value. Any field you leave empty falls back to the platform default.
        </p>
        <div className="grid sm:grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-semibold text-gray-600">Environment</label>
            <select className="input" value={form.environment} onChange={(e) => set("environment", e.target.value)}>
              <option value="sandbox">Sandbox</option>
              <option value="production">Production</option>
            </select>
          </div>
          <div>
            <label className="text-xs font-semibold text-gray-600">Shortcode</label>
            <input className="input" value={form.shortcode} onChange={(e) => set("shortcode", e.target.value)} />
          </div>
          <div>
            <label className="text-xs font-semibold text-gray-600">Initiator name</label>
            <input className="input" value={form.initiator_name} onChange={(e) => set("initiator_name", e.target.value)} />
          </div>
        </div>
        <div className="grid sm:grid-cols-2 gap-3">
          {secretRow("consumer_key", "Consumer key")}
          {secretRow("consumer_secret", "Consumer secret")}
          {secretRow("passkey", "Passkey (STK)")}
          {secretRow("security_credential", "Security credential (B2C)")}
        </div>
        {test && (
          <div className={`rounded-lg px-3 py-2 text-sm ${test.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>
            {test.ok ? "✓ " : "✗ "}{test.status} — {test.detail}
          </div>
        )}
        <div className="flex gap-2">
          <button className="btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save credentials"}</button>
          <button className="btn-ghost" onClick={runTest}>Test connection</button>
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// SMS automation
// --------------------------------------------------------------------------- //
function SmsTab() {
  const [cfg, setCfg] = useState(null);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const load = () => api("/api/v1/settings/sms-automation").then(setCfg).catch((e) => setErr(e.detail));
  useEffect(() => { load(); }, []);
  if (!cfg) return <Spinner />;

  async function save() {
    setBusy(true); setErr(""); setMsg("");
    try {
      await api("/api/v1/settings/sms-automation", { method: "PUT",
        body: { automation_enabled: cfg.automation_enabled, send_hour: Number(cfg.send_hour) } });
      setMsg("SMS automation settings saved."); load();
    } catch (e) { setErr(e.detail || "Save failed"); } finally { setBusy(false); }
  }
  return (
    <div className="max-w-xl">
      <Notice msg={msg} /><Notice msg={err} tone="bad" />
      <div className="card p-5 space-y-4">
        <h3 className="font-bold">Automated SMS reminders</h3>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="h-4 w-4 accent-emerald-600" checked={!!cfg.automation_enabled}
            onChange={(e) => setCfg({ ...cfg, automation_enabled: e.target.checked })} />
          Enable daily repayment reminders & overdue alerts
        </label>
        <div>
          <label className="text-xs font-semibold text-gray-600">Daily send time (hour, 0–23, server time)</label>
          <input type="number" min="0" max="23" className="input !w-32" value={cfg.send_hour}
            onChange={(e) => setCfg({ ...cfg, send_hour: e.target.value })} />
        </div>
        <button className="btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Approver tiers
// --------------------------------------------------------------------------- //
function ApproversTab() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(""); const [saving, setSaving] = useState(null);
  const load = () => api("/api/v1/settings/approver-config").then(setData).catch((e) => setErr(e.detail));
  useEffect(() => { load(); }, []);
  if (err) return <Notice msg={err} tone="bad" />;
  if (!data) return <Spinner />;

  async function toggle(at, role, enabled) {
    const id = `${at}:${role}`; setSaving(id); setErr("");
    setData((d) => ({ ...d, approval_types: d.approval_types.map((t) =>
      t.approval_type === at ? { ...t, roles: t.roles.map((r) =>
        r.role === role ? { ...r, enabled, configured: true } : r) } : t) }));
    try {
      await api("/api/v1/settings/approver-config", { method: "POST",
        body: { tenant_id: data.tenant_id, approval_type: at, role, enabled } });
    } catch (e) { setErr(e.detail || "Save failed"); load(); } finally { setSaving(null); }
  }
  return (
    <div className="max-w-3xl space-y-4">
      <p className="text-sm text-gray-500">Enable or disable which roles may act as approvers for each
        action type. Relationship Officers are front-line originators and are never eligible approvers.</p>
      {data.approval_types.map((t) => (
        <div key={t.approval_type} className="card p-4">
          <h3 className="font-bold mb-2">{t.label}</h3>
          <div className="grid sm:grid-cols-2 gap-2">
            {t.roles.map((r) => (
              <label key={r.role} className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="h-4 w-4 accent-emerald-600"
                  checked={!!r.enabled} disabled={saving === `${t.approval_type}:${r.role}`}
                  onChange={(e) => toggle(t.approval_type, r.role, e.target.checked)} />
                {r.label}{!r.configured && <span className="text-[10px] text-gray-400">(default)</span>}
              </label>
            ))}
            {!t.roles.length && <span className="text-xs text-gray-400">No eligible roles.</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Maker-checker thresholds
// --------------------------------------------------------------------------- //
function ThresholdsTab() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(""); const [msg, setMsg] = useState("");
  const [form, setForm] = useState({ scope_type: "role", scope_key: "", threshold_type: "loan_approval", amount: 0 });
  const load = () => api("/api/v1/settings/thresholds").then(setRows).catch((e) => setErr(e.detail));
  useEffect(() => { load(); }, []);
  if (!rows) return <Spinner />;

  async function add() {
    setErr(""); setMsg("");
    try {
      await api("/api/v1/settings/thresholds", { method: "POST", body: { ...form, amount: Number(form.amount) } });
      setMsg("Threshold saved."); setForm({ ...form, scope_key: "", amount: 0 }); load();
    } catch (e) { setErr(e.detail || "Save failed"); }
  }
  async function del(id) {
    setErr("");
    try { await api(`/api/v1/settings/thresholds/${id}`, { method: "DELETE" }); load(); }
    catch (e) { setErr(e.detail || "Delete failed"); }
  }
  return (
    <div className="max-w-3xl space-y-4">
      <Notice msg={msg} /><Notice msg={err} tone="bad" />
      <div className="card p-4">
        <h3 className="font-bold mb-3">Add / update threshold</h3>
        <div className="grid sm:grid-cols-5 gap-2 items-end">
          <div>
            <label className="text-[11px] font-semibold text-gray-600">Scope</label>
            <select className="input" value={form.scope_type} onChange={(e) => setForm({ ...form, scope_type: e.target.value })}>
              <option value="role">Role</option><option value="branch">Branch</option><option value="region">Region</option>
            </select>
          </div>
          <div>
            <label className="text-[11px] font-semibold text-gray-600">Scope key</label>
            <input className="input" placeholder="role/branch id" value={form.scope_key} onChange={(e) => setForm({ ...form, scope_key: e.target.value })} />
          </div>
          <div>
            <label className="text-[11px] font-semibold text-gray-600">Type</label>
            <select className="input" value={form.threshold_type} onChange={(e) => setForm({ ...form, threshold_type: e.target.value })}>
              <option value="loan_approval">Loan approval</option>
              <option value="disbursement">Disbursement</option>
              <option value="refund">Refund</option>
            </select>
          </div>
          <div>
            <label className="text-[11px] font-semibold text-gray-600">Amount (KES)</label>
            <input type="number" className="input" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
          </div>
          <button className="btn-primary" disabled={!form.scope_key} onClick={add}>Save</button>
        </div>
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="border-b border-border bg-canvas text-left">
            <th className="th">Scope</th><th className="th">Key</th><th className="th">Type</th><th className="th">Amount</th><th className="th"></th>
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-border/50">
                <td className="td capitalize">{r.scope_type}</td><td className="td">{r.scope_key}</td>
                <td className="td">{r.threshold_type}</td><td className="td">KES {Number(r.amount).toLocaleString()}</td>
                <td className="td text-right"><button className="text-red-600 hover:underline text-xs" onClick={() => del(r.id)}>Delete</button></td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan="5" className="td text-center text-gray-400 py-6">No thresholds configured.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Modules
// --------------------------------------------------------------------------- //
function ModulesTab() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(""); const [saving, setSaving] = useState(null);
  const load = () => api("/api/v1/settings/modules").then(setData).catch((e) => setErr(e.detail));
  useEffect(() => { load(); }, []);
  if (err) return <Notice msg={err} tone="bad" />;
  if (!data) return <Spinner />;

  async function toggle(key, enabled) {
    setSaving(key); setErr("");
    setData((d) => ({ ...d, modules: { ...d.modules, [key]: enabled } }));
    try { await api("/api/v1/settings/modules/toggle", { method: "POST", body: { module_key: key, enabled } }); }
    catch (e) { setErr(e.detail || "Save failed"); load(); } finally { setSaving(null); }
  }
  return (
    <div className="max-w-2xl">
      <Notice msg={err} tone="bad" />
      <p className="text-sm text-gray-500 mb-3">Enable or disable feature modules for your organisation.
        Disabled modules disappear from the sidebar and their APIs return 403.</p>
      <div className="card divide-y divide-border">
        {data.module_keys.map((k) => (
          <label key={k} className="flex items-center justify-between px-4 py-3 text-sm">
            <span className="font-medium capitalize">{k.replace(/_/g, " ")}</span>
            <input type="checkbox" className="h-4 w-4 accent-emerald-600" checked={!!data.modules[k]}
              disabled={saving === k} onChange={(e) => toggle(k, e.target.checked)} />
          </label>
        ))}
      </div>
    </div>
  );
}
