// Guarantor registry — guarantors are scoped to a client, so the officer first
// selects a client (searchable) and then sees/creates that client's guarantors.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

const emptyForm = { full_name: "", national_id: "", phone: "", relationship: "", guarantor_type: "personal" };

export default function Guarantors() {
  const nav = useNavigate();
  const { can } = useAuth();
  const [clientSearch, setClientSearch] = useState("");
  const [clients, setClients] = useState([]);
  const [clientId, setClientId] = useState("");
  const [data, setData] = useState(null);
  const [meta, setMeta] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => { api("/api/v1/guarantors/meta").then(setMeta).catch(() => {}); }, []);

  // Debounced client lookup for the selector.
  useEffect(() => {
    const t = setTimeout(() => {
      api(`/api/v1/clients?search=${encodeURIComponent(clientSearch)}&page=1`)
        .then((r) => setClients(r.items || [])).catch(() => setClients([]));
    }, 250);
    return () => clearTimeout(t);
  }, [clientSearch]);

  const loadGuarantors = () => {
    if (!clientId) { setData(null); return; }
    setData(null);
    api(`/api/v1/guarantors?client_id=${clientId}`).then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(loadGuarantors, [clientId]);

  const set = (k) => (e) => setForm((p) => ({ ...p, [k]: e.target.value }));

  const save = async (e) => {
    e.preventDefault();
    setSaving(true); setErr("");
    try {
      await api("/api/v1/guarantors", { method: "POST", body: { ...form, client_id: Number(clientId) } });
      setCreating(false); setForm(emptyForm); loadGuarantors();
    } catch (ex) { setErr(ex.detail || "Could not create guarantor"); }
    finally { setSaving(false); }
  };

  const selectedClient = clients.find((c) => String(c.id) === String(clientId));

  return (
    <div>
      <PageHeader title="Guarantors" crumbs={["Registry", "Guarantors"]}
        actions={clientId && can("guarantors.manage")
          ? <button className="btn-primary" onClick={() => { setForm(emptyForm); setCreating(true); }}>+ New Guarantor</button>
          : null} />

      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="flex-1 min-w-[220px]">
          <label className="label">Find a client</label>
          <input className="input" placeholder="Search name, National ID or phone…"
            value={clientSearch} onChange={(e) => setClientSearch(e.target.value)} />
        </div>
        <div className="flex-1 min-w-[220px]">
          <label className="label">Client</label>
          <select className="input" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">Select a client…</option>
            {clients.map((c) => <option key={c.id} value={c.id}>{c.full_name}{c.phone ? ` · ${c.phone}` : ""}</option>)}
          </select>
        </div>
      </div>

      {!clientId ? (
        <div className="card"><Empty text="Select a client to view their guarantors." /></div>
      ) : (
        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b border-border font-bold text-base">
            Guarantors {selectedClient ? `for ${selectedClient.full_name}` : ""}
          </div>
          {!data ? <Spinner /> : data.items.length === 0 ? <Empty text="No guarantors for this client yet." /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr>
                  <th className="th">Name</th><th className="th">Phone</th><th className="th">Relationship</th>
                  <th className="th">Type</th><th className="th">M-Pesa</th><th className="th">KYC</th>
                  <th className="th">Active</th><th className="th"></th>
                </tr></thead>
                <tbody>
                  {data.items.map((g) => (
                    <tr key={g.id} className="border-t border-border hover:bg-canvas/60 cursor-pointer" onClick={() => nav(`/guarantors/${g.id}`)}>
                      <td className="td font-semibold">{g.full_name}</td>
                      <td className="td">{g.phone || "—"}</td>
                      <td className="td capitalize">{g.relationship || "—"}</td>
                      <td className="td capitalize">{g.guarantor_type}</td>
                      <td className="td">
                        {g.mpesa_validated
                          ? <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-100 text-emerald-700">✓ validated</span>
                          : <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-gray-200 text-gray-600">unvalidated</span>}
                      </td>
                      <td className="td"><Badge value={g.kyc_status} /></td>
                      <td className="td">{g.active ? "Yes" : "No"}</td>
                      <td className="td text-right">
                        <button className="btn-ghost !py-1 !px-2.5 text-xs"
                          onClick={(e) => { e.stopPropagation(); nav(`/guarantors/${g.id}`); }}>Open</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {creating && (
        <Modal title="New Guarantor" onClose={() => setCreating(false)}>
          <form onSubmit={save} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <div><label className="label">Full name</label>
              <input className="input" value={form.full_name} onChange={set("full_name")} required /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="label">National ID</label>
                <input className="input" value={form.national_id} onChange={set("national_id")} required /></div>
              <div><label className="label">Phone</label>
                <input className="input" value={form.phone} onChange={set("phone")} placeholder="2547…" /></div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="label">Relationship</label>
                <input className="input" value={form.relationship} onChange={set("relationship")} placeholder="e.g. Spouse, Business partner" /></div>
              <div><label className="label">Guarantor type</label>
                <select className="input" value={form.guarantor_type} onChange={set("guarantor_type")}>
                  {(meta?.guarantor_types || ["personal", "business"]).map((t) => <option key={t} value={t} className="capitalize">{t}</option>)}
                </select></div>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn-primary" disabled={saving}>{saving ? "Saving…" : "Create guarantor"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
