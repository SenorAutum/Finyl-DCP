// Super Admin console: tenant onboarding + tenant × module feature-flag matrix.
// Toggling a switch instantly gates the module's API (403) and hides its UI nav.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Modal, PageHeader, Spinner } from "../../components/ui";

const MODULE_LABELS = {
  lending: "Lending Engine", payments: "Payments & SMS", dashboard: "Executive Dashboard",
  crm: "CRM & Field Sales", call_center: "Call Center", complaints: "Complaints",
  impact: "Impact & Investors", cbk_reporting: "CBK Reporting", ai_agent: "AI Agent",
};

function Switch({ on, onChange }) {
  return (
    <button type="button" onClick={onChange} aria-pressed={on}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${on ? "bg-accent" : "bg-gray-300"}`}>
      <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${on ? "translate-x-[18px]" : "translate-x-[3px]"}`} />
    </button>
  );
}

export default function Admin() {
  const [matrix, setMatrix] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", code: "", logo_color: "#10B981" });
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => api("/api/v1/admin/module-matrix").then(setMatrix).catch((e) => setErr(e.detail));
  useEffect(() => { load(); }, []);

  const toggle = async (tenant, key) => {
    const enabled = !tenant.modules[key];
    // optimistic update
    setMatrix((m) => ({ ...m, tenants: m.tenants.map((t) => t.id === tenant.id ? { ...t, modules: { ...t.modules, [key]: enabled } } : t) }));
    try {
      await api("/api/v1/admin/modules/toggle", { method: "POST", body: { tenant_id: tenant.id, module_key: key, enabled } });
      setMsg(`${MODULE_LABELS[key] || key} ${enabled ? "enabled" : "disabled"} for ${tenant.name}.`);
    } catch (e) { setErr(e.detail); load(); }
  };

  // WP7: switch a whole DCP off (its staff can no longer sign in) or back on.
  const setTenantState = async (t) => {
    let reason = "";
    if (t.active) {
      reason = prompt(`Deactivate ${t.name}? None of its staff will be able to sign in.\n\nReason (recorded in the audit trail):`);
      if (!reason) return;
    }
    setErr(""); setMsg("");
    try {
      await api(`/api/v1/admin/tenants/${t.id}/state`, { method: "POST", body: { active: !t.active, reason } });
      setMsg(`${t.name} ${t.active ? "deactivated" : "reactivated"}.`);
      load();
    } catch (e) { setErr(e.detail); }
  };

  const createTenant = async (e) => {
    e.preventDefault(); setErr("");
    try {
      await api("/api/v1/admin/tenants", { method: "POST", body: { ...form, active: true } });
      setCreating(false); setForm({ name: "", code: "", logo_color: "#10B981" });
      setMsg("Tenant created with the core modules (lending, payments, dashboard, complaints). Switch on others below when the DCP is ready.");
      load();
    } catch (e2) { setErr(e2.detail); }
  };

  if (!matrix) return <Spinner />;

  return (
    <div>
      <PageHeader title="Super Admin — Module Matrix" crumbs={["Platform", "Admin"]}
        actions={<button className="btn-primary" onClick={() => setCreating(true)}>+ New Tenant</button>} />

      {err && <div className="mb-3 text-sm text-red-600 bg-red-50 rounded-lg p-3">{err}</div>}
      {msg && <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 rounded-lg p-3">{msg}</div>}

      <div className="card overflow-hidden">
        <div className="px-5 py-3.5 border-b border-border">
          <h3 className="font-bold">Tenant × Module Feature Flags</h3>
          <p className="text-xs text-gray-400">Switching a module off returns HTTP 403 on its APIs and removes it from that tenant's navigation immediately.</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr>
              <th className="th sticky left-0 bg-canvas">Tenant</th>
              {matrix.module_keys.map((k) => (
                <th key={k} className="th text-center whitespace-nowrap">{MODULE_LABELS[k] || k}</th>
              ))}
            </tr></thead>
            <tbody>
              {matrix.tenants.map((t) => (
                <tr key={t.id} className="hover:bg-canvas/60">
                  <td className="td sticky left-0 bg-surface">
                    <div className="flex items-center gap-2">
                      <span className="w-6 h-6 rounded-md flex items-center justify-center text-white text-xs font-extrabold" style={{ background: t.logo_color }}>
                        {t.name[0]}
                      </span>
                      <div>
                        <div className="font-semibold whitespace-nowrap">{t.name}</div>
                        <div className="text-[10px] text-gray-400 font-mono">{t.code}</div>
                        {/* WP7: DCP on/off switch and product proposals waiting */}
                        <div className="flex items-center gap-1.5 mt-1">
                          <span className={`text-[10px] font-semibold rounded-full px-1.5 py-0.5 ${t.active ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}`}>
                            {t.active ? "active" : "deactivated"}
                          </span>
                          <button className="text-[10px] underline text-gray-500" onClick={() => setTenantState(t)}>
                            {t.active ? "Deactivate" : "Reactivate"}
                          </button>
                          {t.pending_product_requests > 0 && (
                            <span className="text-[10px] font-semibold rounded-full px-1.5 py-0.5 bg-amber-100 text-amber-800"
                              title="Open Loan Products for this DCP to review">
                              {t.pending_product_requests} product proposal{t.pending_product_requests > 1 ? "s" : ""}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </td>
                  {matrix.module_keys.map((k) => (
                    <td key={k} className="td text-center">
                      <Switch on={!!t.modules[k]} onChange={() => toggle(t, k)} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <JobRuns />

      {creating && (
        <Modal title="Onboard New Tenant" onClose={() => setCreating(false)}>
          <form onSubmit={createTenant} className="space-y-3">
            <div><label className="label">Company name *</label>
              <input className="input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
            <div><label className="label">Code *</label>
              <input className="input" required placeholder="e.g. ACME" value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} /></div>
            <div><label className="label">Brand color</label>
              <input type="color" className="input !h-10 !p-1" value={form.logo_color} onChange={(e) => setForm({ ...form, logo_color: e.target.value })} /></div>
            <p className="text-xs text-gray-400">New DCPs start with the core modules only (lending, payments, dashboard, complaints); switch on the rest in the matrix afterwards.</p>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn-primary">Create tenant</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}


// WP8: background jobs (reconciliation, SMS reminders, report e-mails, CBK
// filing...). One row per job per time slot, whichever server copy ran it.
const JOB_LABELS = {
  auto_reconcile: "Stuck payout check", webhook_retry: "M-Pesa message retry",
  webhook_purge: "M-Pesa message clean-up", ptp_reminders: "Promise-to-pay reminders",
  gps_stipend: "GPS stipends", collection_efficiency: "Collection efficiency",
  otp_cleanup: "Expired code clean-up", cbk_gdi_submit: "CBK GDI filing",
  lifecycle_sms: "Repayment SMS (each DCP's hour)", report_schedules: "Scheduled report e-mails",
  purge_job_runs: "Old job record clean-up",
};
function JobRuns() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api("/api/v1/admin/scheduled-jobs?limit=60").then((r) => setRows(r.items || []))
      .catch((e) => { setErr(e.detail || "Could not load background jobs"); setRows([]); });
  }, []);
  const tone = { ok: "bg-emerald-100 text-emerald-700", failed: "bg-red-100 text-red-700", running: "bg-blue-100 text-blue-700" };
  return (
    <div className="card overflow-hidden mt-5">
      <div className="px-5 py-3 border-b border-border">
        <div className="font-bold text-base">Background jobs</div>
        <div className="text-xs text-gray-400">Latest runs. Each job runs once per time slot even when several copies of the server are running.</div>
      </div>
      {err && <div className="m-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      {rows === null ? <Spinner /> : rows.length === 0 ? <div className="p-5 text-sm text-gray-400">No runs recorded yet.</div> : (
        <div className="overflow-x-auto max-h-[420px]">
          <table className="w-full text-sm">
            <thead><tr><th className="th">Job</th><th className="th">Started</th><th className="th">Result</th><th className="th">Details</th><th className="th">Server</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id} className="border-t border-border">
                <td className="td font-semibold">{JOB_LABELS[r.job_id] || r.job_id}</td>
                <td className="td whitespace-nowrap">{new Date(r.started_at).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" })}</td>
                <td className="td"><span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold ${tone[r.status] || "bg-gray-100 text-gray-600"}`}>{r.status}</span></td>
                <td className="td text-xs text-gray-500 max-w-[360px] truncate" title={r.detail || ""}>{r.detail || "—"}</td>
                <td className="td text-xs text-gray-400">{r.host || "—"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

