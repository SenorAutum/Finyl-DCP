// Super Admin — per-DCP Approver Configuration.
// For each DCP (tenant) and each approval type (Loan / Client profile /
// Disbursement / Refund), toggle which roles act as approvers. Absence of a
// stored toggle falls back to the permission-derived default (shown as such).
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { PageHeader, Spinner } from "../../components/ui";

function Switch({ on, onChange, disabled }) {
  return (
    <button type="button" onClick={onChange} aria-pressed={on} disabled={disabled}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
        on ? "bg-accent" : "bg-gray-300"} ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}>
      <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
        on ? "translate-x-[18px]" : "translate-x-[3px]"}`} />
    </button>
  );
}

export default function ApproverConfig() {
  const [data, setData] = useState(null);          // { tenants, tenant_id, approval_types }
  const [tenantId, setTenantId] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = (tid) => {
    setErr("");
    const qs = tid ? `?tenant_id=${tid}` : "";
    api(`/api/v1/access/approver-config${qs}`).then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(() => { load(""); }, []);

  const pickTenant = (e) => {
    const tid = e.target.value;
    setTenantId(tid); setMsg("");
    load(tid);
  };

  const toggle = async (approval_type, role, next) => {
    setBusy(true); setErr(""); setMsg("");
    // optimistic
    setData((d) => ({
      ...d,
      approval_types: d.approval_types.map((g) => g.approval_type !== approval_type ? g : {
        ...g, roles: g.roles.map((r) => r.role !== role ? r : { ...r, enabled: next, configured: true }),
      }),
    }));
    try {
      await api("/api/v1/access/approver-config", {
        method: "POST",
        body: { tenant_id: Number(tenantId), approval_type, role, enabled: next },
      });
      setMsg(`Saved — ${role} ${next ? "enabled" : "disabled"} for ${approval_type} approvals.`);
    } catch (e) { setErr(e.detail); load(tenantId); }
    finally { setBusy(false); }
  };

  if (!data) return <Spinner />;

  return (
    <div>
      <PageHeader title="Approver Configuration" crumbs={["Platform", "Approver Config"]} />

      <p className="mb-4 text-sm text-gray-500 max-w-3xl">
        Choose a DCP and toggle which roles may act as approvers for each approval type.
        Unconfigured roles fall back to their permission-based default. Disabling a role
        blocks it from approving that action (403) and removes it from the loan-escalation
        ladder (escalation jumps to the next enabled tier).
      </p>

      <div className="mb-4 flex items-center gap-3">
        <label className="text-sm font-medium text-gray-700">DCP (Tenant)</label>
        <select className="input max-w-xs" value={tenantId} onChange={pickTenant}>
          <option value="">— Select a DCP —</option>
          {data.tenants.map((t) => (
            <option key={t.id} value={t.id}>{t.name} ({t.code}){t.active ? "" : " — inactive"}</option>
          ))}
        </select>
      </div>

      {err && <div className="mb-3 text-sm text-red-600 bg-red-50 rounded-lg p-3">{err}</div>}
      {msg && <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 rounded-lg p-3">{msg}</div>}

      {!tenantId && <div className="text-sm text-gray-400">Select a DCP above to configure its approver model.</div>}

      {tenantId && (
        <div className="grid gap-4 md:grid-cols-2">
          {data.approval_types.map((g) => (
            <div key={g.approval_type} className="card p-4">
              <div className="font-semibold text-gray-800 mb-3">{g.label}</div>
              {g.roles.length === 0 && <div className="text-sm text-gray-400">No eligible roles.</div>}
              <div className="space-y-2">
                {g.roles.map((r) => (
                  <div key={r.role} className="flex items-center justify-between py-1">
                    <div>
                      <div className="text-sm font-medium text-gray-800">{r.label}</div>
                      <div className="text-[11px] text-gray-400">
                        {r.configured ? "Custom" : "Default"}
                        {" · default "}{r.default ? "on" : "off"}
                      </div>
                    </div>
                    <Switch on={r.enabled} disabled={busy}
                      onChange={() => toggle(g.approval_type, r.role, !r.enabled)} />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
