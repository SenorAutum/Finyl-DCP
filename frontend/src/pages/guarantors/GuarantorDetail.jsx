// Guarantor profile — identity summary, KYC/M-Pesa status, linked businesses and
// uploaded documents. WP10: a colleague confirms the name M-Pesa shows for the
// guarantor's number (same rule as for clients), and documents are real
// uploads. National ID is PII (encrypted at rest) so it is never returned.
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, download, fmtDate, fmtKES, upload } from "../../lib/api";
import { Badge, Empty, KpiCard, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

const bytes = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

function Row({ label, value }) {
  return (
    <div>
      <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
      <div className="text-sm font-medium mt-0.5">{value || "—"}</div>
    </div>
  );
}

function ConfirmMpesaModal({ g, mode, onClose, onDone }) {
  const [name, setName] = useState("");
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      onDone(await api(`/api/v1/guarantors/${g.id}/confirm-mpesa`, {
        method: "POST", body: { registered_name: name, evidence, phone: g.phone },
      }));
    } catch (ex) { setErr(ex.detail || "Could not confirm"); setBusy(false); }
  };
  return (
    <Modal title="Confirm guarantor's M-Pesa name" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <p className="text-sm text-gray-600">
          Check who owns <span className="font-semibold">{g.phone}</span> — for example, start a
          KES 1 send-money to the number and read the name M-Pesa shows, or see it on the
          guarantor's own phone. Type the name exactly as M-Pesa shows it.
        </p>
        {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
        <div><label className="label">Name M-Pesa shows</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} required /></div>
        <div><label className="label">How you saw it</label>
          <textarea className="input" rows={3} value={evidence} required
            placeholder="e.g. KES 1 send-money test at 10:15 today; M-Pesa showed PETER K MWANGI"
            onChange={(e) => setEvidence(e.target.value)} /></div>
        <p className="text-[11px] text-gray-400">
          {mode === "single_person"
            ? "The name must match the guarantor's names."
            : "You cannot confirm a guarantor you captured, or one whose client is in your own portfolio. The name must match the guarantor's names."}
        </p>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Confirm name"}</button>
        </div>
      </form>
    </Modal>
  );
}

export default function GuarantorDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const [g, setG] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [meta, setMeta] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [docType, setDocType] = useState("national_id");
  const fileRef = useRef(null);

  useEffect(() => { api("/api/v1/guarantors/meta").then(setMeta).catch(() => {}); }, []);

  const load = useCallback(() => {
    api(`/api/v1/guarantors/${id}`).then(setG).catch((e) => setErr(e.detail));
  }, [id]);
  useEffect(() => { load(); }, [load]);

  const uploadFiles = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    setBusy(true); setErr("");
    try {
      await upload(`/api/v1/guarantors/${id}/documents?doc_types=${files.map(() => docType).join(",")}`, fd);
      load();
    } catch (ex) { setErr(ex.detail || "Upload failed"); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };

  const removeDoc = async (d) => {
    if (!window.confirm(`Remove ${d.original_name || d.file_name}?`)) return;
    setBusy(true); setErr("");
    try { await api(`/api/v1/guarantors/${id}/documents/${d.id}`, { method: "DELETE" }); load(); }
    catch (ex) { setErr(ex.detail || "Could not remove"); }
    finally { setBusy(false); }
  };

  const remove = async () => {
    if (!window.confirm("Delete this guarantor? This cannot be undone.")) return;
    setBusy(true); setErr("");
    try {
      await api(`/api/v1/guarantors/${id}`, { method: "DELETE" });
      nav("/guarantors");
    } catch (ex) { setErr(ex.detail || "Could not delete"); setBusy(false); }
  };

  if (err && !g) return <div className="card p-6 text-sm text-red-600">{err}</div>;
  if (!g) return <Spinner />;

  const canManage = can("guarantors.manage");
  const mode = meta?.mpesa_confirmation_mode;
  const canConfirm = mode === "single_person" ? canManage : can("clients.approve");

  return (
    <div>
      <PageHeader title={g.full_name} crumbs={["Registry", "Guarantors", g.full_name]}
        actions={
          <>
            <button className="btn-ghost" onClick={() => nav("/guarantors")}>← Back</button>
            {canConfirm && g.phone && !g.mpesa_validated &&
              <button className="btn-primary" onClick={() => setConfirming(true)} disabled={busy}>Confirm M-Pesa name</button>}
            {canManage && <button className="btn-ghost text-red-600" onClick={remove} disabled={busy}>Delete</button>}
          </>
        } />

      {confirming && <ConfirmMpesaModal g={g} mode={mode} onClose={() => setConfirming(false)}
        onDone={(ng) => { setG(ng); setConfirming(false); }} />}

      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <KpiCard label="KYC status" value={<Badge value={g.kyc_status} />} />
        <KpiCard label="M-Pesa" value={g.mpesa_validated ? "Confirmed" : "Not confirmed"}
          tone={g.mpesa_validated ? "good" : "warn"}
          sub={g.mpesa_validated ? g.mpesa_validation_name : (g.phone ? "A colleague must confirm the name" : "No phone number")} />
        <KpiCard label="Type" value={<span className="capitalize">{g.guarantor_type}</span>} />
        <KpiCard label="Active" value={g.active ? "Yes" : "No"} tone={g.active ? "good" : "warn"} />
      </div>

      <div className="card p-5 mb-5">
        <h2 className="font-bold text-base mb-4">Guarantor Details</h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
          <Row label="Full Name" value={g.full_name} />
          <Row label="National ID" value={<span className="text-gray-400">••• hidden</span>} />
          <Row label="Phone" value={g.phone} />
          <Row label="Relationship" value={g.relationship} />
          <Row label="Occupation" value={g.occupation} />
          <Row label="Monthly Income" value={g.monthly_income != null ? fmtKES(g.monthly_income) : "—"} />
          <Row label="Address" value={g.address} />
          <Row label="M-Pesa Name" value={g.mpesa_validation_name} />
          <Row label="Created" value={fmtDate(g.created_at)} />
        </div>
      </div>

      {(g.business || []).length > 0 && (
        <div className="card overflow-hidden mb-5">
          <div className="px-5 py-3 border-b border-border font-bold text-base">Linked Businesses</div>
          <table className="w-full text-sm">
            <thead><tr><th className="th">Business</th><th className="th">Registration</th><th className="th">Sector</th><th className="th">Monthly Turnover</th></tr></thead>
            <tbody>
              {g.business.map((b) => (
                <tr key={b.id} className="border-t border-border">
                  <td className="td font-semibold">{b.business_name || "—"}</td>
                  <td className="td">{b.business_reg_no || "—"}</td>
                  <td className="td capitalize">{b.sector || "—"}</td>
                  <td className="td">{b.monthly_turnover != null ? fmtKES(b.monthly_turnover) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-border flex flex-wrap items-center gap-2">
          <span className="font-bold text-base flex-1">Documents</span>
          {canManage && (
            <>
              <select className="input w-auto" value={docType} onChange={(e) => setDocType(e.target.value)}>
                {(meta?.document_types || ["national_id", "other"]).map((t) =>
                  <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
              </select>
              <input ref={fileRef} type="file" multiple className="hidden" onChange={uploadFiles} />
              <button className="btn-ghost" disabled={busy} onClick={() => fileRef.current?.click()}>
                {busy ? "Working…" : "Upload"}</button>
            </>
          )}
        </div>
        {(g.documents || []).length === 0 ? <Empty text="No documents uploaded" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">File</th><th className="th">Type</th><th className="th">Size</th>
                <th className="th">Uploaded</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {g.documents.map((d) => (
                  <tr key={d.id} className="border-t border-border">
                    <td className="td font-semibold">{d.original_name || d.file_name}</td>
                    <td className="td capitalize">{(d.doc_type || "other").replace(/_/g, " ")}</td>
                    <td className="td">{bytes(d.size_bytes || 0)}</td>
                    <td className="td">{fmtDate(d.uploaded_at)}</td>
                    <td className="td text-right whitespace-nowrap">
                      <button className="text-primary text-xs font-semibold mr-3"
                        onClick={() => download(`/api/v1/guarantors/${id}/documents/${d.id}/download`, d.original_name || d.file_name)
                          .catch((ex) => setErr(ex.detail))}>Download</button>
                      {canManage && <button className="text-red-600 text-xs font-semibold" onClick={() => removeDoc(d)}>Remove</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
