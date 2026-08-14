// Client profile — ID details, mobile wallets, next of kin, documents,
// loan history and impact surveys. "Edit client" reopens the full KYC screen.
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, download, fmtDate, fmtKES } from "../../lib/api";
import { Badge, Empty, KpiCard, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import ClientForm from "./ClientForm";
import CreditAnalysis from "./CreditAnalysis";

const bytes = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

function Row({ label, value }) {
  return (
    <div>
      <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
      <div className="text-sm font-medium mt-0.5">{value || "—"}</div>
    </div>
  );
}

export default function ClientDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const [c, setC] = useState(null);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState(false);

  const load = useCallback(() => {
    api(`/api/v1/clients/${id}`).then(setC).catch((e) => setErr(e.detail));
  }, [id]);
  useEffect(load, [load]);

  if (err) return <div className="card p-6 text-sm text-red-600">{err}</div>;
  if (!c) return <Spinner />;

  const outstanding = (c.loans || []).reduce((s, l) => s + (l.outstanding_balance || 0), 0);

  return (
    <div>
      <PageHeader title={c.full_name} crumbs={["Lending", "Clients", c.full_name]}
        actions={
          <>
            <button className="btn-ghost" onClick={() => nav("/clients")}>← Back to Clients</button>
            {can("clients.edit") && <button className="btn-primary" onClick={() => setEditing(true)}>Edit client</button>}
          </>
        } />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <KpiCard label="KYC status" value={<Badge value={c.kyc_status} />} />
        <KpiCard label="M-Pesa" value={c.mpesa_validated ? "Validated" : "Unvalidated"}
          tone={c.mpesa_validated ? "good" : "warn"} sub={c.mpesa_validation_name || "—"} />
        <KpiCard label="Loans" value={(c.loans || []).length} sub={`${fmtKES(outstanding)} outstanding`} />
        <KpiCard label="Credit score" value={c.credit_score ?? "—"} sub={`Rating ${c.current_credit_rating || "—"}`} />
      </div>

      <div className="card p-5 mb-5">
        <h2 className="font-bold text-base mb-4">Client ID Details</h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
          <Row label="Serial Number" value={c.serial_number} />
          <Row label="National ID" value={c.national_id} />
          <Row label="Mobile Number" value={c.phone} />
          <Row label="Gender" value={c.gender} />
          <Row label="Date of Birth" value={fmtDate(c.date_of_birth)} />
          <Row label="District of Birth" value={c.district_of_birth} />
          <Row label="Place of Issue" value={c.place_of_issue} />
          <Row label="Date of Issue" value={fmtDate(c.date_of_issue)} />
          <Row label="District" value={c.district} />
          <Row label="Division" value={c.division} />
          <Row label="Location" value={c.location} />
          <Row label="Sub Location" value={c.sub_location} />
          <Row label="Onboarded By" value={c.onboarded_by} />
          <Row label="Profile Status" value={
            c.profile_status === "pending_approval"
              ? <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-amber-100 text-amber-700">pending approval</span>
              : c.profile_status === "rejected"
              ? <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-red-100 text-red-700">rejected</span>
              : <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-100 text-emerald-700">approved</span>
          } />
          <Row label="eKYC" value={c.ekyc_status ? `${c.ekyc_status.replace(/_/g, " ")}${c.ekyc_reference ? ` · ${c.ekyc_reference}` : ""}` : "—"} />
          <Row label="Business Sector" value={c.business_sector} />
          <Row label="Baseline Monthly Sales" value={fmtKES(c.baseline_monthly_sales)} />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b border-border font-bold text-base">Mobile Wallets</div>
          {(c.wallets || []).length === 0 ? <Empty text="No mobile wallets" /> : (
            <table className="w-full text-sm">
              <thead><tr><th className="th">Mobile</th><th className="th">Wallet</th><th className="th">Operator</th><th className="th">Active</th></tr></thead>
              <tbody>
                {c.wallets.map((w) => (
                  <tr key={w.id} className="border-t border-border">
                    <td className="td">{w.mobile_number || "—"}</td>
                    <td className="td">{w.wallet_number || "—"}</td>
                    <td className="td">{w.operator || "—"}</td>
                    <td className="td">{w.active ? "Yes" : "No"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b border-border font-bold text-base">Next of Kin</div>
          {(c.next_of_kin || []).length === 0 ? <Empty text="No next of kin" /> : (
            <table className="w-full text-sm">
              <thead><tr><th className="th">Name</th><th className="th">Relationship</th><th className="th">Mobile</th><th className="th">National ID</th></tr></thead>
              <tbody>
                {c.next_of_kin.map((n) => (
                  <tr key={n.id} className="border-t border-border">
                    <td className="td font-semibold">{n.full_name || "—"}</td>
                    <td className="td">{n.relationship || "—"}</td>
                    <td className="td">{n.mobile_number || "—"}</td>
                    <td className="td">{n.national_id || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="card overflow-hidden mb-5">
        <div className="px-5 py-3 border-b border-border font-bold text-base">Documents</div>
        {(c.documents || []).length === 0 ? <Empty text="No documents uploaded" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">File</th><th className="th">Type</th><th className="th">Size</th>
                <th className="th">OCR</th><th className="th">Uploaded by</th><th className="th">Uploaded</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {c.documents.map((d) => (
                  <tr key={d.id} className="border-t border-border">
                    <td className="td font-semibold">{d.original_name}</td>
                    <td className="td capitalize">{(d.doc_type || "other").replace(/_/g, " ")}</td>
                    <td className="td">{bytes(d.size_bytes || 0)}</td>
                    <td className="td">{d.ocr_applied ? "Yes" : "—"}</td>
                    <td className="td">{d.uploaded_by || "—"}</td>
                    <td className="td">{fmtDate(d.uploaded_at)}</td>
                    <td className="td text-right">
                      <button className="btn-ghost !py-1 !px-2.5 text-xs"
                        onClick={() => download(`/api/v1/clients/${c.id}/documents/${d.id}/download`, d.original_name)}>
                        Download
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <CreditAnalysis clientId={c.id} onScore={load} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b border-border font-bold text-base">Loans</div>
          {(c.loans || []).length === 0 ? <Empty text="No loans yet" /> : (
            <table className="w-full text-sm">
              <thead><tr><th className="th">Account</th><th className="th">Principal</th><th className="th">Status</th><th className="th">Outstanding</th></tr></thead>
              <tbody>
                {c.loans.map((l) => (
                  <tr key={l.id} className="border-t border-border hover:bg-canvas/60 cursor-pointer" onClick={() => nav(`/loans/${l.id}`)}>
                    <td className="td font-semibold">{l.account_number}</td>
                    <td className="td">{fmtKES(l.principal)}</td>
                    <td className="td"><Badge value={l.status} /></td>
                    <td className="td">{fmtKES(l.outstanding_balance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b border-border font-bold text-base">Impact Surveys</div>
          {(c.impact_surveys || []).length === 0 ? <Empty text="No impact surveys" /> : (
            <table className="w-full text-sm">
              <thead><tr><th className="th">Cycle</th><th className="th">Sales pre</th><th className="th">Sales post</th><th className="th">Jobs</th><th className="th">Date</th></tr></thead>
              <tbody>
                {c.impact_surveys.map((s) => (
                  <tr key={s.id} className="border-t border-border">
                    <td className="td">#{s.loan_cycle_number}</td>
                    <td className="td">{fmtKES(s.monthly_sales_pre)}</td>
                    <td className="td">{fmtKES(s.monthly_sales_post)}</td>
                    <td className="td">{s.jobs_created}</td>
                    <td className="td">{fmtDate(s.survey_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {editing && (
        <ClientForm clientId={c.id} onClose={() => setEditing(false)}
          onSaved={() => { setEditing(false); load(); }} />
      )}
    </div>
  );
}
