// Third-party API clients — issue scoped API keys for external systems to push
// M-Pesa / bank statements into the platform. The full key is shown exactly once
// at creation and never again, so it is surfaced in a copyable confirmation.
import { useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { Empty, Modal, PageHeader, Spinner } from "../../components/ui";

const SCOPES = [
  ["mpesa_statement", "M-Pesa statement ingestion"],
  ["bank_statement", "Bank statement ingestion"],
];

const fmtTime = (d) => (d ? new Date(d).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }) : "—");

export default function ApiClients() {
  const [data, setData] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ client_name: "", scopes: [], rate_limit_per_min: 60 });
  const [saving, setSaving] = useState(false);
  const [newKey, setNewKey] = useState(null); // { client_name, api_key }
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState("");

  const load = () => {
    setData(null);
    api("/api/v1/api-clients").then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(load, []);

  const toggleScope = (s) => setForm((p) => ({
    ...p, scopes: p.scopes.includes(s) ? p.scopes.filter((x) => x !== s) : [...p.scopes, s],
  }));

  const create = async (e) => {
    e.preventDefault();
    setSaving(true); setErr("");
    try {
      const res = await api("/api/v1/api-clients", { method: "POST", body: {
        client_name: form.client_name, scopes: form.scopes,
        rate_limit_per_min: Number(form.rate_limit_per_min) || 60,
      }});
      setCreating(false);
      setForm({ client_name: "", scopes: [], rate_limit_per_min: 60 });
      setNewKey({ client_name: res.client_name, api_key: res.api_key });
      setCopied(false);
      load();
    } catch (ex) { setErr(ex.detail || "Could not create API client"); }
    finally { setSaving(false); }
  };

  const revoke = async (id) => {
    if (!window.confirm("Revoke this API client? Its key will stop working immediately.")) return;
    setErr("");
    try { await api(`/api/v1/api-clients/${id}`, { method: "DELETE" }); load(); }
    catch (ex) { setErr(ex.detail || "Could not revoke"); }
  };

  const copyKey = async () => {
    try { await navigator.clipboard.writeText(newKey.api_key); setCopied(true); } catch { /* clipboard blocked */ }
  };

  return (
    <div>
      <PageHeader title="API Clients" crumbs={["Administration", "API Clients"]}
        actions={<button className="btn-primary" onClick={() => setCreating(true)}>+ New API Client</button>} />

      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-border font-bold text-base">Registered API Clients</div>
        {!data ? <Spinner /> : data.items.length === 0 ? <Empty text="No API clients issued yet." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Name</th><th className="th">Key Prefix</th><th className="th">Scopes</th>
                <th className="th text-right">Rate Limit</th><th className="th">Status</th><th className="th">Last Used</th>
                <th className="th">Created</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.id} className="border-t border-border">
                    <td className="td font-semibold">{c.client_name}</td>
                    <td className="td font-mono text-xs">{c.api_key_prefix}…</td>
                    <td className="td">
                      <div className="flex flex-wrap gap-1">
                        {(c.scopes || []).map((s) => <span key={s} className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-gray-100 text-gray-700">{s}</span>)}
                      </div>
                    </td>
                    <td className="td text-right tabnums">{c.rate_limit_per_min}/min</td>
                    <td className="td">
                      {c.active
                        ? <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-100 text-emerald-700">active</span>
                        : <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-gray-200 text-gray-600">revoked</span>}
                    </td>
                    <td className="td">{fmtTime(c.last_used_at)}</td>
                    <td className="td">{fmtDate(c.created_at)}</td>
                    <td className="td text-right">
                      {c.active && <button className="btn-ghost !py-1 !px-2.5 text-xs text-red-600" onClick={() => revoke(c.id)}>Revoke</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating && (
        <Modal title="New API Client" onClose={() => setCreating(false)}>
          <form onSubmit={create} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <div><label className="label">Client name</label>
              <input className="input" value={form.client_name} onChange={(e) => setForm((p) => ({ ...p, client_name: e.target.value }))} required placeholder="e.g. Statements ETL bot" /></div>
            <div>
              <label className="label">Scopes</label>
              <div className="space-y-2 mt-1">
                {SCOPES.map(([val, label]) => (
                  <label key={val} className="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" checked={form.scopes.includes(val)} onChange={() => toggleScope(val)} />
                    <span className="font-mono text-xs text-teal">{val}</span>
                    <span className="text-gray-500">— {label}</span>
                  </label>
                ))}
              </div>
            </div>
            <div><label className="label">Rate limit (requests / min)</label>
              <input className="input" type="number" value={form.rate_limit_per_min}
                onChange={(e) => setForm((p) => ({ ...p, rate_limit_per_min: e.target.value }))} min={1} /></div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn-primary" disabled={saving || form.scopes.length === 0}>{saving ? "Creating…" : "Create client"}</button>
            </div>
          </form>
        </Modal>
      )}

      {newKey && (
        <Modal title="API Key Created" onClose={() => setNewKey(null)}>
          <div className="space-y-4">
            <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-3">
              ⚠ Copy this key now — it is shown <span className="font-semibold">only once</span> and cannot be retrieved later.
            </div>
            <div>
              <label className="label">Client</label>
              <div className="text-sm font-semibold">{newKey.client_name}</div>
            </div>
            <div>
              <label className="label">API key</label>
              <div className="flex gap-2">
                <input className="input font-mono text-xs" value={newKey.api_key} readOnly onFocus={(e) => e.target.select()} />
                <button type="button" className="btn-primary whitespace-nowrap" onClick={copyKey}>{copied ? "Copied ✓" : "Copy"}</button>
              </div>
            </div>
            <div className="flex justify-end pt-1">
              <button className="btn-primary" onClick={() => setNewKey(null)}>Done</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
