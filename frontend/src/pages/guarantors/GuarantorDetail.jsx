// Guarantor profile — identity summary, KYC/M-Pesa status, linked businesses and
// uploaded documents. Managers can run validation (M-Pesa name + OCR) or remove
// the guarantor. National ID is PII (encrypted at rest) so it is never returned.
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, fmtDate, fmtKES } from "../../lib/api";
import { Badge, Empty, KpiCard, PageHeader, Spinner } from "../../components/ui";
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

export default function GuarantorDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const [g, setG] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api(`/api/v1/guarantors/${id}`).then(setG).catch((e) => setErr(e.detail));
  }, [id]);
  useEffect(load, [load]);

  const validate = async () => {
    setBusy(true); setErr("");
    try { setG(await api(`/api/v1/guarantors/${id}/validate`, { method: "POST", body: {} })); }
    catch (ex) { setErr(ex.detail || "Validation failed"); }
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

  if (err) return <div className="card p-6 text-sm text-red-600">{err}</div>;
  if (!g) return <Spinner />;

  const canManage = can("guarantors.manage");

  return (
    <div>
      <PageHeader title={g.full_name} crumbs={["Registry", "Guarantors", g.full_name]}
        actions={
          <>
            <button className="btn-ghost" onClick={() => nav("/guarantors")}>← Back</button>
            {canManage && <button className="btn-primary" onClick={validate} disabled={busy}>{busy ? "Working…" : "Run validation"}</button>}
            {canManage && <button className="btn-ghost text-red-600" onClick={remove} disabled={busy}>Delete</button>}
          </>
        } />

      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <KpiCard label="KYC status" value={<Badge value={g.kyc_status} />} />
        <KpiCard label="M-Pesa" value={g.mpesa_validated ? "Validated" : "Unvalidated"}
          tone={g.mpesa_validated ? "good" : "warn"} sub={g.mpesa_validation_name || "—"} />
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
                  <td className="td">{b.registration_number || "—"}</td>
                  <td className="td capitalize">{b.sector || "—"}</td>
                  <td className="td">{b.monthly_turnover != null ? fmtKES(b.monthly_turnover) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-border font-bold text-base">Documents</div>
        {(g.documents || []).length === 0 ? <Empty text="No documents uploaded" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">File</th><th className="th">Type</th><th className="th">Size</th>
                <th className="th">OCR</th><th className="th">Uploaded</th>
              </tr></thead>
              <tbody>
                {g.documents.map((d) => (
                  <tr key={d.id} className="border-t border-border">
                    <td className="td font-semibold">{d.original_name || d.file_name}</td>
                    <td className="td capitalize">{(d.doc_type || "other").replace(/_/g, " ")}</td>
                    <td className="td">{bytes(d.size_bytes || 0)}</td>
                    <td className="td">{d.ocr_applied ? "Yes" : "—"}</td>
                    <td className="td">{fmtDate(d.uploaded_at)}</td>
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
