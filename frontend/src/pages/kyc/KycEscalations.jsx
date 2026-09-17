// KYC mismatch escalations — officers raise identity/document mismatches; KYC
// resolvers review and either resolve or override (dismiss) them with a note.
import { useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

const STATUS_MAP = { open: "open", resolved: "resolved", overridden: "closed" };

export default function KycEscalations() {
  const { can } = useAuth();
  const [status, setStatus] = useState("open");
  const [data, setData] = useState(null);
  const [meta, setMeta] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ client_id: "", mismatch_type: "", mismatch_detail: "", escalated_to: "" });
  const [resolving, setResolving] = useState(null); // { id, mode: "resolved"|"overridden", note }
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => { api("/api/v1/kyc-escalations/meta").then(setMeta).catch(() => {}); }, []);

  const load = () => {
    setData(null);
    api(`/api/v1/kyc-escalations?status=${status}`).then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(load, [status]);

  const set = (k) => (e) => setForm((p) => ({ ...p, [k]: e.target.value }));

  const create = async (e) => {
    e.preventDefault();
    setSaving(true); setErr("");
    try {
      await api("/api/v1/kyc-escalations", { method: "POST", body: {
        client_id: Number(form.client_id), mismatch_type: form.mismatch_type,
        mismatch_detail: form.mismatch_detail,
        escalated_to: form.escalated_to ? Number(form.escalated_to) : undefined,
      }});
      setCreating(false); setForm({ client_id: "", mismatch_type: "", mismatch_detail: "", escalated_to: "" }); load();
    } catch (ex) { setErr(ex.detail || "Could not raise escalation"); }
    finally { setSaving(false); }
  };

  const submitResolution = async (e) => {
    e.preventDefault();
    setSaving(true); setErr("");
    try {
      await api(`/api/v1/kyc-escalations/${resolving.id}`, { method: "PATCH", body: {
        status: resolving.mode, resolution_note: resolving.note || undefined,
      }});
      setResolving(null); load();
    } catch (ex) { setErr(ex.detail || "Could not update escalation"); }
    finally { setSaving(false); }
  };

  const canResolve = can("kyc.resolve");
  const canEscalate = can("kyc.escalate");

  return (
    <div>
      <PageHeader title="KYC Escalations" crumbs={["Registry", "KYC Escalations"]}
        actions={canEscalate ? <button className="btn-primary" onClick={() => setCreating(true)}>+ New Escalation</button> : null} />

      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="card overflow-hidden">
        <div className="p-3 border-b border-border flex flex-wrap gap-2 items-center">
          <select className="input max-w-[200px]" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {(meta?.statuses || ["open", "resolved", "overridden"]).map((s) => <option key={s} value={s} className="capitalize">{s}</option>)}
          </select>
        </div>

        {!data ? <Spinner /> : data.items.length === 0 ? <Empty text="No escalations found." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Client</th><th className="th">Mismatch Type</th><th className="th">Detail</th>
                <th className="th">Status</th><th className="th">Raised</th><th className="th">Resolved</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {data.items.map((e) => (
                  <tr key={e.id} className="border-t border-border">
                    <td className="td font-semibold">#{e.client_id}</td>
                    <td className="td capitalize">{(e.mismatch_type || "").replace(/_/g, " ")}</td>
                    <td className="td max-w-xs truncate" title={e.mismatch_detail}>{e.mismatch_detail || "—"}</td>
                    <td className="td"><Badge value={STATUS_MAP[e.status] || "open"}>{e.status}</Badge></td>
                    <td className="td">{fmtDate(e.created_at)}</td>
                    <td className="td">{e.resolved_at ? fmtDate(e.resolved_at) : "—"}</td>
                    <td className="td text-right whitespace-nowrap">
                      {canResolve && e.status === "open" && (
                        <>
                          <button className="btn-ghost !py-1 !px-2.5 text-xs text-accent"
                            onClick={() => setResolving({ id: e.id, mode: "resolved", note: "" })}>Resolve</button>
                          <button className="btn-ghost !py-1 !px-2.5 text-xs text-red-600"
                            onClick={() => setResolving({ id: e.id, mode: "overridden", note: "" })}>Dismiss</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating && (
        <Modal title="Raise KYC Escalation" onClose={() => setCreating(false)}>
          <form onSubmit={create} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <div className="grid grid-cols-2 gap-3">
              <div><label className="label">Client ID</label>
                <input className="input" type="number" value={form.client_id} onChange={set("client_id")} required /></div>
              <div><label className="label">Escalate to (user ID)</label>
                <input className="input" type="number" value={form.escalated_to} onChange={set("escalated_to")} placeholder="Optional" /></div>
            </div>
            <div><label className="label">Mismatch type</label>
              <select className="input" value={form.mismatch_type} onChange={set("mismatch_type")} required>
                <option value="">Select…</option>
                {(meta?.mismatch_types || []).map((t) => <option key={t} value={t} className="capitalize">{t.replace(/_/g, " ")}</option>)}
              </select></div>
            <div><label className="label">Detail</label>
              <textarea className="input" rows={3} value={form.mismatch_detail} onChange={set("mismatch_detail")}
                placeholder="Describe the mismatch (e.g. ID photo does not match selfie)" required /></div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn-primary" disabled={saving}>{saving ? "Saving…" : "Raise escalation"}</button>
            </div>
          </form>
        </Modal>
      )}

      {resolving && (
        <Modal title={resolving.mode === "resolved" ? "Resolve Escalation" : "Dismiss Escalation"} onClose={() => setResolving(null)}>
          <form onSubmit={submitResolution} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <p className="text-sm text-gray-500">
              {resolving.mode === "resolved"
                ? "Mark this mismatch as resolved. Add a note describing the resolution."
                : "Dismiss (override) this mismatch. Add a note explaining why it can be overridden."}
            </p>
            <div><label className="label">Resolution note</label>
              <textarea className="input" rows={3} value={resolving.note}
                onChange={(e) => setResolving((p) => ({ ...p, note: e.target.value }))} required /></div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn-ghost" onClick={() => setResolving(null)}>Cancel</button>
              <button className={resolving.mode === "resolved" ? "btn-primary" : "btn-primary"} disabled={saving}>
                {saving ? "Saving…" : resolving.mode === "resolved" ? "Resolve" : "Dismiss"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
