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
  useEffect(load, []);

  const toggle = async (tenant, key) => {
    const enabled = !tenant.modules[key];
    // optimistic update
    setMatrix((m) => ({ ...m, tenants: m.tenants.map((t) => t.id === tenant.id ? { ...t, modules: { ...t.modules, [key]: enabled } } : t) }));
    try {
      await api("/api/v1/admin/modules/toggle", { method: "POST", body: { tenant_id: tenant.id, module_key: key, enabled } });
      setMsg(`${MODULE_LABELS[key] || key} ${enabled ? "enabled" : "disabled"} for ${tenant.name}.`);
    } catch (e) { setErr(e.detail); load(); }
  };

  const createTenant = async (e) => {
    e.preventDefault(); setErr("");
    try {
      await api("/api/v1/admin/tenants", { method: "POST", body: { ...form, active: true } });
      setCreating(false); setForm({ name: "", code: "", logo_color: "#10B981" });
      setMsg("Tenant created with all modules enabled.");
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
            <p className="text-xs text-gray-400">New tenants start with every module enabled; adjust flags in the matrix afterwards.</p>
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
