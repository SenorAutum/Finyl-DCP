// DCP Configuration — self-service settings for a DCP's OWN administrator
// (system_admin), scoped to the caller's own tenant. Tabs: M-Pesa/Daraja
// credentials, SMS automation, approver tiers, maker-checker thresholds, modules.
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { PageHeader, Spinner, Badge } from "../../components/ui";

const TABS = [
  ["daraja", "M-Pesa / Daraja"],
  ["banks", "Bank Notifications"],
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
      {tab === "banks" && <BanksTab />}
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
// WP12: bank payment notifications — one set-up per bank account. Finyl gives a
// private address to hand to the bank; the bank signs each message with the
// agreed secret; the DCP says which field holds what.
// --------------------------------------------------------------------------- //
const BANK_FIELDS = [
  ["reference", "Bank reference *", "e.g. transactionReference"],
  ["amount", "Amount *", "e.g. amount or transaction.amount"],
  ["date", "Date and time", "e.g. transactionDate"],
  ["account", "Account the payer typed", "e.g. billNumber or customerReference"],
  ["narrative", "Narrative", "e.g. narration"],
  ["payer_name", "Payer name", "e.g. debitCustName"],
  ["payer_phone", "Payer phone", "e.g. phoneNumber"],
  ["direction", "Credit / debit flag (optional)", "e.g. transactionType"],
];
const emptyBank = { name: "", auth_mode: "hmac_sha256", auth_header: "X-Signature", secret: "",
  allowed_ips: "", amount_in_cents: false, active: true, field_map: {} };

function BanksTab() {
  const [banks, setBanks] = useState(null);
  const [edit, setEdit] = useState(null);           // { id?, ...form }
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const [sample, setSample] = useState(""); const [test, setTest] = useState(null);
  const load = () => api("/api/v1/settings/bank-notifications").then((d) => setBanks(d.banks)).catch((e) => setErr(e.detail));
  useEffect(() => { load(); }, []);
  if (!banks) return err ? <Notice msg={err} tone="bad" /> : <Spinner />;
  const set = (k, v) => setEdit((f) => ({ ...f, [k]: v }));
  const setField = (k, v) => setEdit((f) => ({ ...f, field_map: { ...f.field_map, [k]: v } }));
  async function save() {
    setErr(""); setMsg("");
    try {
      const body = { ...edit, allowed_ips: edit.allowed_ips };
      const b = edit.id
        ? await api(`/api/v1/settings/bank-notifications/${edit.id}`, { method: "PUT", body })
        : await api("/api/v1/settings/bank-notifications", { method: "POST", body });
      setMsg(`Saved. Give the bank this address: ${b.url}`);
      setEdit(null); load();
    } catch (e) { setErr(e.detail || "Save failed"); }
  }
  async function remove(b) {
    if (!window.confirm(`Remove ${b.name}? Notifications from this bank will be refused.`)) return;
    try { const d = await api(`/api/v1/settings/bank-notifications/${b.id}`, { method: "DELETE" }); setBanks(d.banks); }
    catch (e) { setErr(e.detail || "Could not remove"); }
  }
  async function runTest(b) {
    setTest(null); setErr("");
    let parsed;
    try { parsed = JSON.parse(sample); } catch { setErr("The sample is not valid JSON."); return; }
    try { setTest({ bank: b.id, ...(await api(`/api/v1/settings/bank-notifications/${b.id}/test`, { method: "POST", body: { sample: parsed } })) }); }
    catch (e) { setErr(e.detail || "Test failed"); }
  }
  return (
    <div className="max-w-3xl space-y-4">
      <Notice msg={msg} /><Notice msg={err} tone="bad" />
      <div className="card p-5">
        <h3 className="font-bold">Bank payment notifications</h3>
        <p className="text-xs text-gray-500 mt-1">
          Many banks can tell Finyl the moment money lands in your account. Add a set-up per bank account,
          agree a shared secret with the bank, tell Finyl which field holds what, and give the bank the address
          Finyl shows. Payments that name a loan account number or customer number are applied at once; the rest
          go to the Suspense Account. The same bank reference is never counted twice.
        </p>
        <div className="mt-3 space-y-2">
          {banks.length === 0 && <div className="text-sm text-gray-400">No bank set up yet.</div>}
          {banks.map((b) => (
            <div key={b.id} className="border border-border rounded-lg p-3">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="font-semibold">{b.name} {!b.active && <span className="text-xs text-gray-400">(off)</span>}</div>
                <div className="space-x-1">
                  <button className="btn-ghost text-xs" onClick={() => setEdit({ ...emptyBank, ...b, secret: "", allowed_ips: (b.allowed_ips || []).join(", ") })}>Edit</button>
                  <button className="btn-ghost text-xs text-red-600" onClick={() => remove(b)}>Remove</button>
                </div>
              </div>
              <div className="text-xs text-gray-500 mt-1 break-all">Address for the bank: <span className="font-mono">{b.url}</span></div>
              <div className="text-xs text-gray-500">Proof: {b.auth_mode === "hmac_sha256" ? "HMAC-SHA256 signature" : "shared key"} in header {b.auth_header}{b.allowed_ips?.length ? ` · only from ${b.allowed_ips.join(", ")}` : ""}</div>
              <div className="mt-2">
                <textarea className="input font-mono text-xs" rows={3} placeholder='Paste a sample notification from the bank, e.g. {"transactionReference": "...", "amount": "1500.00", ...}'
                  value={sample} onChange={(e) => setSample(e.target.value)} />
                <button className="btn-ghost text-xs mt-1" onClick={() => runTest(b)}>Test with this sample (records nothing)</button>
                {test?.bank === b.id && (
                  <div className="text-xs mt-1 bg-canvas rounded p-2">
                    Reference {test.reference || "—"} · amount {test.amount ?? "—"} · account {test.account || "—"} · payer {test.payer_name || "—"}
                    <div className="font-semibold mt-1">Result: {test.result}</div>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
        {!edit && <button className="btn-primary mt-3" onClick={() => setEdit({ ...emptyBank })}>Add a bank</button>}
      </div>

      {edit && (
        <div className="card p-5 space-y-3">
          <h3 className="font-bold">{edit.id ? `Edit ${edit.name}` : "Add a bank"}</h3>
          <div className="grid sm:grid-cols-2 gap-3">
            <div><label className="text-xs font-semibold text-gray-600">Name</label>
              <input className="input" placeholder="e.g. Equity collections account" value={edit.name} onChange={(e) => set("name", e.target.value)} /></div>
            <div><label className="text-xs font-semibold text-gray-600">How the bank proves it is the bank</label>
              <select className="input" value={edit.auth_mode} onChange={(e) => { set("auth_mode", e.target.value); set("auth_header", e.target.value === "hmac_sha256" ? "X-Signature" : "X-Api-Key"); }}>
                <option value="hmac_sha256">Signature (HMAC-SHA256 of the message)</option>
                <option value="shared_key">Shared key in a header</option>
              </select></div>
            <div><label className="text-xs font-semibold text-gray-600">Header name</label>
              <input className="input" value={edit.auth_header} onChange={(e) => set("auth_header", e.target.value)} /></div>
            <div><label className="text-xs font-semibold text-gray-600">Shared secret {edit.secret_set ? "(set — leave blank to keep)" : "(at least 16 characters)"}</label>
              <input className="input" type="password" autoComplete="new-password" value={edit.secret} onChange={(e) => set("secret", e.target.value)} /></div>
            <div className="sm:col-span-2"><label className="text-xs font-semibold text-gray-600">Bank's internet addresses (optional, comma separated)</label>
              <input className="input" placeholder="e.g. 196.201.214.0/24" value={edit.allowed_ips} onChange={(e) => set("allowed_ips", e.target.value)} /></div>
          </div>
          <div className="text-xs font-semibold text-gray-600 pt-2">Which field holds what (use dots for nested fields, e.g. transaction.amount)</div>
          <div className="grid sm:grid-cols-2 gap-3">
            {BANK_FIELDS.map(([k, label, ph]) => (
              <div key={k}><label className="text-xs text-gray-600">{label}</label>
                <input className="input font-mono text-xs" placeholder={ph} value={edit.field_map?.[k] || ""} onChange={(e) => setField(k, e.target.value)} /></div>
            ))}
          </div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!edit.amount_in_cents} onChange={(e) => set("amount_in_cents", e.target.checked)} /> The bank sends amounts in cents</label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!edit.active} onChange={(e) => set("active", e.target.checked)} /> Accept notifications from this bank</label>
          <div className="flex gap-2 justify-end">
            <button className="btn-ghost" onClick={() => setEdit(null)}>Cancel</button>
            <button className="btn-primary" onClick={save}>Save</button>
          </div>
        </div>
      )}
    </div>
  );
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
              till_numbers: (d.till_numbers || []).join(", "),
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
                     till_numbers: form.till_numbers || "",
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
            <label className="text-xs font-semibold text-gray-600">Paybill number (shortcode)</label>
            <input className="input" inputMode="numeric" value={form.shortcode} onChange={(e) => set("shortcode", e.target.value)} />
            <p className="text-[11px] text-gray-500 mt-1">Payments into this paybill are credited to your DCP only.</p>
          </div>
          <div>
            <label className="text-xs font-semibold text-gray-600">Till numbers (Buy Goods)</label>
            <input className="input" inputMode="numeric" placeholder="e.g. 5123456, 5123457"
              value={form.till_numbers} onChange={(e) => set("till_numbers", e.target.value)} />
            <p className="text-[11px] text-gray-500 mt-1">
              Optional. Separate several with commas. Add the store (head office) number too if
              Safaricom gave you one. Till payments carry no account number, so each one goes to the
              Suspense Account with a suggested loan for a person to confirm.
            </p>
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
      <AllocationCard />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// WP2 — how a repayment is split between interest, fees/charges and principal
// --------------------------------------------------------------------------- //
function AllocationCard() {
  const [cfg, setCfg] = useState(null);
  const [order, setOrder] = useState(""); const [basis, setBasis] = useState(""); const [early, setEarly] = useState("");
  const [msg, setMsg] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const load = () => api("/api/v1/payments/settings").then((d) => {
    setCfg(d); setOrder(d.allocation_order); setBasis(d.allocation_order_basis || ""); setEarly(d.early_settlement_method);
  }).catch((e) => setErr(e.detail || "Failed to load payment settings"));
  useEffect(() => { load(); }, []);
  if (!cfg) return err ? <div className="mt-4"><Notice msg={err} tone="bad" /></div> : null;

  async function save() {
    setBusy(true); setErr(""); setMsg("");
    try {
      await api("/api/v1/payments/settings", { method: "PUT", body: { allocation_order: order, basis, early_settlement_method: early } });
      setMsg("Payment settings saved. They apply to payments received from now on."); load();
    } catch (e) { setErr(e.detail || "Save failed"); } finally { setBusy(false); }
  }
  return (
    <div className="card p-5 space-y-3 mt-4">
      <Notice msg={msg} /><Notice msg={err} tone="bad" />
      <h3 className="font-bold">Repayment allocation order</h3>
      <p className="text-xs text-gray-500">
        How each payment is split between principal, fees/charges and interest. The CBK
        Non-Deposit Taking Credit Providers Regulations, 2026 (Legal Notice 191), regulation 56(2),
        require principal first, then fees and charges, then interest — the default.
      </p>
      <select className="input" value={order} onChange={(e) => setOrder(e.target.value)}>
        {Object.entries(cfg.allocation_order_options || {}).map(([k, label]) => (
          <option key={k} value={k}>{label}</option>
        ))}
      </select>
      <div>
        <label className="text-xs font-semibold text-gray-600">Basis (e.g. "LN 191/2026, regulation 56(2)")</label>
        <input className="input" value={basis} onChange={(e) => setBasis(e.target.value)} />
      </div>
      {cfg.allocation_order_is_default && (
        <p className="text-[11px] text-gray-500">Using the default required by regulation 56(2).</p>
      )}
      <h3 className="font-bold pt-3 border-t border-border">Early full repayment</h3>
      <p className="text-xs text-gray-500">
        A client who repays in full before the due date does not pay interest for the remaining
        period (CBK LN 191/2026, regulation 56(4)). Choose how the interest earned so far is counted.
      </p>
      <select className="input" value={early} onChange={(e) => setEarly(e.target.value)}>
        {Object.entries(cfg.early_settlement_options || {}).map(([k, label]) => (
          <option key={k} value={k}>{label}</option>
        ))}
      </select>
      <button className="btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save payment settings"}</button>
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
  if (err && !data) return <Notice msg={err} tone="bad" />;
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
        action type. Relationship Officers are front-line originators and are never eligible approvers.
        Roles you created appear here once they hold the matching approval permission (Roles &amp; Permissions);
        they are off until you switch them on, and a role you created needs a loan approval limit
        (Thresholds tab) before it can approve loans.</p>
      <Notice msg={err} tone="bad" />
      {data.approval_types.map((t) => (
        <div key={t.approval_type} className="card p-4">
          <h3 className="font-bold mb-2">{t.label}</h3>
          <div className="grid sm:grid-cols-2 gap-2">
            {t.roles.map((r) => (
              <label key={r.role} className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="h-4 w-4 accent-emerald-600"
                  checked={!!r.enabled} disabled={saving === `${t.approval_type}:${r.role}`}
                  onChange={(e) => toggle(t.approval_type, r.role, e.target.checked)} />
                {r.label}{r.custom && <span className="text-[10px] text-indigo-500">(your role)</span>}
                {!r.configured && <span className="text-[10px] text-gray-400">(default)</span>}
                {r.custom && r.has_limit === false && <span className="text-[10px] text-amber-600">needs a loan limit</span>}
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
              <option value="manual_payment">Manual payment / suspense allocation</option>
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
