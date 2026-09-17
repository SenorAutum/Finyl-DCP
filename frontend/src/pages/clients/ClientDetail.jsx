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

// Data-protection consent status (read-only summary; full capture/update is on the
// Edit Client screen). Renders whatever the consent endpoint returns.
function ConsentPanel({ clientId }) {
  const [data, setData] = useState(undefined); // undefined=loading, null=none
  useEffect(() => {
    let ok = true;
    api(`/api/v1/clients/${clientId}/consent`)
      .then((r) => ok && setData(r?.consent ?? null))
      .catch(() => ok && setData(null));
    return () => { ok = false; };
  }, [clientId]);

  const Flag = ({ label, on }) => (
    <div className="flex items-center gap-2 text-sm">
      <span className={on ? "text-accent" : "text-gray-400"}>{on ? "✓" : "—"}</span>
      <span className={on ? "font-medium" : "text-gray-400"}>{label}</span>
    </div>
  );

  return (
    <div className="card p-5 mb-5">
      <h2 className="font-bold text-base mb-3">Data Protection Consent</h2>
      {data === undefined ? <Spinner /> : data === null ? (
        <p className="text-sm text-gray-400">No consent recorded yet. Capture it via <span className="font-medium">Edit client</span>.</p>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Flag label="Data processing" on={data.consent_data_processing} />
            <Flag label="Credit reference (CRB) check" on={data.consent_credit_check} />
            <Flag label="Marketing communications" on={data.consent_marketing} />
          </div>
          <div className="text-[11px] text-gray-400 mt-3">
            {data.consent_version && <>Version {data.consent_version} · </>}
            {data.consented_at && <>Recorded {fmtDate(data.consented_at)}</>}
          </div>
        </>
      )}
    </div>
  );
}

// Biometric face-validation status (ID photo vs selfie). Read-only summary;
// renders whatever the face-validation-status endpoint returns.
function FaceValidationPanel({ clientId }) {
  const [data, setData] = useState(undefined); // undefined=loading, null=unavailable
  useEffect(() => {
    let ok = true;
    api(`/api/v1/clients/${clientId}/face-validation-status`)
      .then((r) => ok && setData(r ?? null))
      .catch(() => ok && setData(null));
    return () => { ok = false; };
  }, [clientId]);

  if (data === undefined || data === null) return null;

  const tone = data.result === "pass" ? { badge: "bg-emerald-100 text-emerald-700", label: "Passed" }
    : data.result === "fail" ? { badge: "bg-red-100 text-red-700", label: "Failed" }
    : { badge: "bg-gray-100 text-gray-500", label: "Not run" };

  return (
    <div className="card p-5 mb-5">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <h2 className="font-bold text-base">Face Validation</h2>
        <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold ${tone.badge}`}>{tone.label}</span>
      </div>
      {data.result === "not_run" ? (
        <p className="text-sm text-gray-400">
          Biometric face validation has not been run for this client.
          {data.mandatory && <span className="text-amber-600 font-medium"> It is mandatory for your organisation.</span>}
        </p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Row label="Match score" value={data.match_score != null ? `${(data.match_score * 100).toFixed(1)}%` : "—"} />
          <Row label="Liveness" value={data.liveness_pass == null ? "—" : data.liveness_pass ? "Pass" : "Fail"} />
          <Row label="Provider" value={data.provider} />
          <Row label="Validated" value={fmtDate(data.validated_at)} />
        </div>
      )}
    </div>
  );
}

// Pending/approved change-requests raised against this client's locked fields.
// The list endpoint is permission-gated, so a 403 simply hides the panel.
function EditRequestsPanel({ clientId }) {
  const [items, setItems] = useState(undefined); // undefined=loading, null=unavailable
  useEffect(() => {
    let ok = true;
    api(`/api/v1/clients/edit-requests?client_id=${clientId}`)
      .then((r) => ok && setItems(r.items || []))
      .catch(() => ok && setItems(null));
    return () => { ok = false; };
  }, [clientId]);

  if (items === undefined || items === null || items.length === 0) return null;

  return (
    <div className="card overflow-hidden mt-5">
      <div className="px-5 py-3 border-b border-border font-bold text-base">Field Edit Requests</div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr>
            <th className="th">Tier</th><th className="th">Fields</th><th className="th">Requested by</th>
            <th className="th">Status</th><th className="th">Requested</th>
          </tr></thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id} className="border-t border-border align-top">
                <td className="td capitalize">{(r.edit_tier || "").replace(/_/g, " ")}</td>
                <td className="td">
                  {Object.keys(r.field_changes || {}).length === 0 ? "—" : (
                    <div className="space-y-0.5">
                      {Object.entries(r.field_changes).map(([field, ch]) => (
                        <div key={field} className="text-[12px]">
                          <span className="font-semibold">{field.replace(/_/g, " ")}:</span>{" "}
                          <span className="text-gray-400 line-through">{String(ch?.old_value ?? "—")}</span>{" → "}
                          <span className="text-charcoal">{String(ch?.new_value ?? "—")}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </td>
                <td className="td">{r.requested_by || "—"}</td>
                <td className="td"><Badge value={r.status} /></td>
                <td className="td">{fmtDate(r.requested_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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

      {c.edit_locked && (
        <div className="mb-5 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-start gap-2">
          <span>🔒</span>
          <span>
            <span className="font-semibold">Primary fields are edit-locked.</span> Changes to protected
            fields (phone, National ID, date of birth) require an approved edit request.
            {c.edit_locked_reason && <span className="block text-amber-700 mt-0.5">Reason: {c.edit_locked_reason}</span>}
          </span>
        </div>
      )}

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

      <ConsentPanel clientId={c.id} />

      <FaceValidationPanel clientId={c.id} />

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

      <EditRequestsPanel clientId={c.id} />

      {editing && (
        <ClientForm clientId={c.id} onClose={() => setEditing(false)}
          onSaved={() => { setEditing(false); load(); }} />
      )}
    </div>
  );
}
