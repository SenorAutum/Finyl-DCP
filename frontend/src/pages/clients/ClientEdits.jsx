// Client-edit approval queue (maker-checker). Edits to borrowers with active
// loans are tiered (secondary / primary) and must be approved or rejected by a
// user holding the matching approval permission.
import { useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

const fmtVal = (v) => (v == null || v === "" ? "—" : String(v));

function FieldChanges({ changes }) {
  const entries = Object.entries(changes || {});
  if (entries.length === 0) return <span className="text-gray-400 text-xs">No field changes</span>;
  return (
    <table className="w-full text-xs border border-border rounded-lg overflow-hidden">
      <thead><tr className="bg-canvas">
        <th className="th !py-1.5">Field</th><th className="th !py-1.5">Old</th><th className="th !py-1.5">New</th>
      </tr></thead>
      <tbody>
        {entries.map(([field, chg]) => (
          <tr key={field} className="border-t border-border">
            <td className="td !py-1.5 font-semibold capitalize">{field.replace(/_/g, " ")}</td>
            <td className="td !py-1.5 text-gray-500 line-through">{fmtVal(chg?.old_value)}</td>
            <td className="td !py-1.5 text-accent font-medium">{fmtVal(chg?.new_value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ClientEdits() {
  const { can } = useAuth();
  const [status, setStatus] = useState("pending");
  const [data, setData] = useState(null);
  const [rejecting, setRejecting] = useState(null); // { id, reason }
  const [busy, setBusy] = useState(0);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const load = () => {
    setData(null);
    api(`/api/v1/clients/edit-requests?status=${status}`).then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(load, [status]);

  const approve = async (id) => {
    setBusy(id); setErr("");
    try { await api(`/api/v1/clients/edit-requests/${id}/approve`, { method: "POST", body: {} }); load(); }
    catch (ex) { setErr(ex.detail || "Could not approve"); }
    finally { setBusy(0); }
  };

  const submitReject = async (e) => {
    e.preventDefault();
    setSaving(true); setErr("");
    try {
      await api(`/api/v1/clients/edit-requests/${rejecting.id}/reject`, { method: "POST", body: { rejection_reason: rejecting.reason } });
      setRejecting(null); load();
    } catch (ex) { setErr(ex.detail || "Could not reject"); }
    finally { setSaving(false); }
  };

  const canApprove = can("client_edits.approve_primary", "client_edits.approve_secondary");

  return (
    <div>
      <PageHeader title="Client Edit Requests" crumbs={["Registry", "Edit Requests"]} />

      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="card p-3 mb-5 flex flex-wrap gap-2 items-center">
        <select className="input max-w-[200px]" value={status} onChange={(e) => setStatus(e.target.value)}>
          {["pending", "approved", "rejected"].map((s) => <option key={s} value={s} className="capitalize">{s}</option>)}
        </select>
      </div>

      {!data ? <Spinner /> : data.items.length === 0 ? (
        <div className="card"><Empty text={`No ${status} edit requests.`} /></div>
      ) : (
        <div className="space-y-4">
          {data.items.map((r) => (
            <div key={r.id} className="card p-5">
              <div className="flex items-start justify-between flex-wrap gap-3 mb-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-base">Client #{r.client_id}</h3>
                    <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold ${r.edit_tier === "primary" ? "bg-red-100 text-red-700" : "bg-amber-100 text-amber-700"}`}>
                      {r.edit_tier} tier
                    </span>
                    <Badge value={r.status === "pending" ? "pending" : r.status === "approved" ? "approved" : "rejected"}>{r.status}</Badge>
                  </div>
                  <p className="text-xs text-gray-400 mt-1">Requested by user {r.requested_by} · {fmtDate(r.requested_at)}</p>
                </div>
                {r.status === "pending" && canApprove && (
                  <div className="flex gap-2">
                    <button className="btn-primary !py-1.5" disabled={busy === r.id} onClick={() => approve(r.id)}>{busy === r.id ? "…" : "Approve"}</button>
                    <button className="btn-ghost !py-1.5 text-red-600" onClick={() => setRejecting({ id: r.id, reason: "" })}>Reject</button>
                  </div>
                )}
              </div>

              <FieldChanges changes={r.field_changes} />

              {r.status === "rejected" && r.rejection_reason && (
                <div className="mt-3 text-xs text-red-700"><span className="font-semibold">Rejection reason: </span>{r.rejection_reason}</div>
              )}
              {(r.supporting_docs || []).length > 0 && (
                <div className="mt-3 text-xs text-gray-500"><span className="font-semibold">Supporting docs: </span>{(r.supporting_docs || []).length} attached</div>
              )}
            </div>
          ))}
        </div>
      )}

      {rejecting && (
        <Modal title="Reject Edit Request" onClose={() => setRejecting(null)}>
          <form onSubmit={submitReject} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <div><label className="label">Rejection reason</label>
              <textarea className="input" rows={3} value={rejecting.reason}
                onChange={(e) => setRejecting((p) => ({ ...p, reason: e.target.value }))} required /></div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn-ghost" onClick={() => setRejecting(null)}>Cancel</button>
              <button className="btn-primary" disabled={saving}>{saving ? "Saving…" : "Reject request"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
