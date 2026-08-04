// Approval Thresholds — configurable loan-approval limits per role/branch/region
// plus maker-checker amount thresholds for disbursement & refund.
import { useEffect, useState } from "react";
import { api, fmtKES } from "../../lib/api";
import { useAuth } from "../../hooks/useAuth";
import { PageHeader, Spinner, Empty, Modal, Badge } from "../../components/ui";

const TYPE_LABEL = { loan_approval: "Loan approval limit", disbursement: "Disbursement maker-checker", refund: "Refund maker-checker" };

export default function Thresholds() {
  const { can } = useAuth();
  const [rows, setRows] = useState(null);
  const [org, setOrg] = useState({ regions: [], branches: [] });
  const [editing, setEditing] = useState(null);
  const [msg, setMsg] = useState("");
  const load = () => api("/api/v1/access/thresholds").then(setRows).catch((e) => { setMsg(e.detail); setRows([]); });
  useEffect(() => { load(); if (can("org.view")) api("/api/v1/access/org").then(setOrg).catch(() => {}); }, []); // eslint-disable-line
  const canManage = can("thresholds.manage");

  const del = async (id) => { if (!confirm("Delete this threshold?")) return;
    try { await api(`/api/v1/access/thresholds/${id}`, { method: "DELETE" }); load(); } catch (e) { setMsg(e.detail); } };

  const save = async (body) => { try { await api("/api/v1/access/thresholds", { method: "POST", body });
    setEditing(null); load(); setMsg("Threshold saved"); } catch (e) { setMsg(e.detail); } };

  if (!rows) return <Spinner />;
  return (
    <div>
      <PageHeader title="Approval Thresholds" crumbs={["Administration", "Thresholds"]}
        actions={canManage && <button className="btn-primary" onClick={() => setEditing({ scope_type: "role", scope_key: "branch_manager", threshold_type: "loan_approval", amount: 0 })}>+ Threshold</button>} />
      {msg && <div className="mb-3 text-sm px-3 py-2 rounded-lg bg-teal-50 text-teal-700 border border-teal-200">{msg}</div>}
      <p className="text-sm text-gray-500 mb-4">
        Loan approvals above a role/branch/region limit auto-escalate (branch → region → HQ).
        Disbursements & refunds above their maker-checker amount need a second authorised approver.
      </p>
      <div className="card overflow-hidden">
        {rows.length === 0 ? <Empty text="No thresholds configured" /> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left border-b border-border bg-canvas">
              <th className="th">Type</th><th className="th">Scope</th><th className="th">Key</th>
              <th className="th">Amount</th>{canManage && <th className="th text-right">Actions</th>}
            </tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id} className="border-b border-border/60">
                  <td className="td">{TYPE_LABEL[t.threshold_type] || t.threshold_type}</td>
                  <td className="td capitalize"><Badge value="draft">{t.scope_type}</Badge></td>
                  <td className="td font-mono text-xs">{t.scope_key}</td>
                  <td className="td font-semibold">{fmtKES(t.amount)}</td>
                  {canManage && <td className="td text-right space-x-1">
                    <button className="btn-ghost !py-1 !px-2 text-xs" onClick={() => setEditing(t)}>Edit</button>
                    <button className="btn-ghost !py-1 !px-2 text-xs text-red-600" onClick={() => del(t.id)}>Delete</button>
                  </td>}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {editing && <ThresholdForm form={editing} org={org} onClose={() => setEditing(null)} onSave={save} />}
    </div>
  );
}

const ROLES = ["relationship_officer", "branch_manager", "regional_manager"];

function ThresholdForm({ form, org, onClose, onSave }) {
  const [f, setF] = useState({ scope_type: form.scope_type, scope_key: form.scope_key, threshold_type: form.threshold_type, amount: form.amount });
  const up = (k) => (e) => setF({ ...f, [k]: e.target.value });

  // suggest keys based on scope_type
  const keyOptions = f.scope_type === "role" ? ROLES.map((r) => [r, r])
    : f.scope_type === "branch" ? org.branches.map((b) => [String(b.id), b.name])
    : f.scope_type === "region" ? org.regions.map((r) => [String(r.id), r.name])
    : [["all", "all (company-wide)"]];

  return (
    <Modal title="Approval threshold" onClose={onClose}>
      <div className="space-y-3">
        <div><label className="label">Threshold type</label>
          <select className="input" value={f.threshold_type} onChange={up("threshold_type")}>
            <option value="loan_approval">Loan approval limit</option>
            <option value="disbursement">Disbursement maker-checker</option>
            <option value="refund">Refund maker-checker</option>
          </select></div>
        <div><label className="label">Scope type</label>
          <select className="input" value={f.scope_type} onChange={(e) => setF({ ...f, scope_type: e.target.value, scope_key: e.target.value === "role" ? "branch_manager" : "all" })}>
            <option value="role">Role</option><option value="branch">Branch</option>
            <option value="region">Region</option><option value="all">Company-wide (all)</option>
          </select></div>
        {f.scope_type === "all" ? (
          <div className="text-xs text-gray-500">Applies company-wide (scope key <code>all</code>).</div>
        ) : (
          <div><label className="label">Scope key</label>
            <select className="input" value={f.scope_key} onChange={up("scope_key")}>
              {keyOptions.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select></div>
        )}
        <div><label className="label">Amount (KES)</label>
          <input className="input" type="number" value={f.amount} onChange={up("amount")} /></div>
        <div className="flex justify-end gap-2 pt-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={() => onSave({ ...f, scope_key: f.scope_type === "all" ? "all" : f.scope_key, amount: parseFloat(f.amount) || 0 })}>Save</button></div>
      </div>
    </Modal>
  );
}
