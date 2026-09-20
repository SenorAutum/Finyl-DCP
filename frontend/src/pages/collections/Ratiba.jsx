// M-Pesa Ratiba — standing-order (auto-deduction) mandate management. Look up a
// loan's Ratiba status, capture borrower consent for a scheduled deduction, and
// initiate the mandate once consent is on file.
import { useState } from "react";
import { api, fmtKES } from "../../lib/api";
import { Badge, PageHeader } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

function Metric({ label, value }) {
  return (
    <div className="rounded-xl bg-surface2 border border-border px-3 py-2">
      <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
      <div className="text-sm font-extrabold tabnums mt-0.5">{value}</div>
    </div>
  );
}

const emptyConsent = { phone: "", deduction_amount: "", deduction_day: "" };

export default function Ratiba() {
  const { can } = useAuth();
  const [loanId, setLoanId] = useState("");
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [consent, setConsent] = useState(emptyConsent);

  const canManage = can("collections.ptp_manage");

  const check = async (e) => {
    e?.preventDefault();
    if (!loanId) return;
    setBusy(true); setErr(""); setOk(""); setStatus(null);
    try { setStatus(await api(`/api/v1/loans/${loanId}/ratiba-status`)); }
    catch (ex) { setErr(ex.detail || "Could not load status"); }
    finally { setBusy(false); }
  };

  const submitConsent = async (e) => {
    e.preventDefault();
    setBusy(true); setErr(""); setOk("");
    try {
      await api("/api/v1/collections/ratiba/consent", { method: "POST", body: {
        loan_id: Number(loanId),
        phone: consent.phone,
        consent_given: true,
        deduction_amount: consent.deduction_amount === "" ? undefined : Number(consent.deduction_amount),
        deduction_day: consent.deduction_day === "" ? undefined : Number(consent.deduction_day),
      }});
      setOk("Consent captured.");
      setConsent(emptyConsent);
      setStatus(await api(`/api/v1/loans/${loanId}/ratiba-status`));
    } catch (ex) { setErr(ex.detail || "Could not capture consent"); }
    finally { setBusy(false); }
  };

  const initiate = async () => {
    setBusy(true); setErr(""); setOk("");
    try {
      setStatus(await api("/api/v1/collections/ratiba/initiate", { method: "POST", body: { loan_id: Number(loanId) } }));
      setOk("Ratiba mandate initiated.");
    } catch (ex) { setErr(ex.detail || "Could not initiate Ratiba"); }
    finally { setBusy(false); }
  };

  return (
    <div>
      <PageHeader title="M-Pesa Ratiba" crumbs={["Field & Collections", "M-Pesa Ratiba"]} />
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      {ok && <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg p-3">{ok}</div>}

      <form onSubmit={check} className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="min-w-[200px]">
          <label className="label">Loan ID</label>
          <input className="input" type="number" value={loanId} onChange={(e) => setLoanId(e.target.value)} placeholder="e.g. 1024" />
        </div>
        <button className="btn-primary" disabled={busy || !loanId}>{busy ? "Loading…" : "Check status"}</button>
      </form>

      {status && (
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="card p-5">
            <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
              <div>
                <h3 className="font-bold text-base">Mandate · Loan #{status.loan_id}</h3>
                <p className="text-xs text-gray-400">Standing-order deduction mandate</p>
              </div>
              <Badge value={status.status === "none" ? "pending" : status.status} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Metric label="Consent given" value={status.consent_given ? "Yes" : "No"} />
              <Metric label="Ratiba ref" value={status.ratiba_ref || "—"} />
              <Metric label="Deduction amount" value={status.deduction_amount != null ? fmtKES(status.deduction_amount) : "—"} />
              <Metric label="Deduction day" value={status.deduction_day || "—"} />
            </div>
            {canManage && (
              <div className="mt-4 flex justify-end">
                <button className="btn-primary" onClick={initiate} disabled={busy || !status.consent_given}>
                  {busy ? "Working…" : "Initiate Ratiba"}
                </button>
              </div>
            )}
            {!status.consent_given && (
              <p className="mt-2 text-xs text-amber-600 text-right">Client consent must be captured before initiating.</p>
            )}
          </div>

          {canManage && (
            <div className="card p-5">
              <h3 className="font-bold text-base mb-4">Capture Consent</h3>
              <form onSubmit={submitConsent} className="space-y-4">
                <div><label className="label">Borrower phone</label>
                  <input className="input" value={consent.phone} onChange={(e) => setConsent((p) => ({ ...p, phone: e.target.value }))} placeholder="2547XXXXXXXX" required /></div>
                <div className="grid grid-cols-2 gap-3">
                  <div><label className="label">Deduction amount (KES)</label>
                    <input className="input" type="number" value={consent.deduction_amount} onChange={(e) => setConsent((p) => ({ ...p, deduction_amount: e.target.value }))} /></div>
                  <div><label className="label">Deduction day (1–28)</label>
                    <input className="input" type="number" min="1" max="28" value={consent.deduction_day} onChange={(e) => setConsent((p) => ({ ...p, deduction_day: e.target.value }))} /></div>
                </div>
                <div className="flex justify-end">
                  <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Record consent"}</button>
                </div>
              </form>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
