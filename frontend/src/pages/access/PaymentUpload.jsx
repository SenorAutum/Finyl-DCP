// Payment File Upload — bulk payments from a CSV (payments.upload gated).
// Every row follows the manual-payment rules: matched within this DCP only,
// each reference recorded once, and a second approver above the DCP's
// manual-payment threshold (Approvals → Maker-Checker).
import { useState } from "react";
import { upload } from "../../lib/api";
import { PageHeader } from "../../components/ui";

const fmtKES = (n) => `KES ${Number(n || 0).toLocaleString("en-KE", { maximumFractionDigits: 2 })}`;

export default function PaymentUpload() {
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    if (!file) return;
    setBusy(true); setErr(""); setResult(null);
    try {
      const fd = new FormData(); fd.append("file", file);
      setResult(await upload("/api/v1/access/payment-upload", fd));
    } catch (e) { setErr(e.detail); } finally { setBusy(false); }
  };

  return (
    <div>
      <PageHeader title="Payment File Upload" crumbs={["Administration", "Payment Upload"]} />
      <div className="card p-5 max-w-3xl">
        <p className="text-sm text-gray-500 mb-2">
          Upload a CSV of payments, for example a paybill or bank statement export. Columns
          (case-insensitive): <code>account_number</code> or <code>customer_number</code>,{" "}
          <code>amount</code>, <code>mpesa_ref</code>, and optionally <code>date</code>{" "}
          (YYYY-MM-DD) and <code>channel</code>.
        </p>
        <p className="text-sm text-gray-500 mb-4">
          Each reference can only be recorded once. Rows above your manual-payment limit wait for a
          second person under Approvals → Maker-Checker before any balance changes.
        </p>
        <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files[0])} className="input" />
        <button className="btn-primary mt-4" disabled={!file || busy} onClick={submit}>{busy ? "Processing…" : "Upload payments"}</button>
        {err && <div className="mt-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err}</div>}
        {result && (
          <div className="mt-4 text-sm space-y-3">
            <div className="font-semibold">{result.filename}</div>
            <div className="flex flex-wrap gap-4">
              <span className="text-amber-700 font-bold">{result.pending_approval} awaiting approval</span>
              <span className="text-accent font-bold">{result.applied} applied</span>
              <span className="text-gray-600 font-bold">{result.unmatched} not matched</span>
              <span className="text-gray-600 font-bold">{(result.duplicates || []).length} duplicates skipped</span>
              <span className="text-red-600 font-bold">{(result.errors || []).length} errors</span>
            </div>
            {(result.unmatched_rows || []).length > 0 && (
              <div>
                <div className="font-semibold mb-1">Not matched — record these from the Suspense screen once you know the loan</div>
                <table className="w-full text-xs">
                  <thead><tr><th className="th">Row</th><th className="th">Account entered</th><th className="th">Reference</th><th className="th">Amount</th><th className="th">Reason</th></tr></thead>
                  <tbody>{result.unmatched_rows.map((u) => (
                    <tr key={u.row}><td className="td">{u.row}</td><td className="td font-mono">{u.account}</td><td className="td font-mono">{u.reference}</td><td className="td">{fmtKES(u.amount)}</td><td className="td">{u.reason}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            {(result.duplicates || []).length > 0 && (
              <ul className="text-xs text-gray-600 list-disc pl-5">
                {result.duplicates.map((d) => <li key={`${d.row}-${d.reference}`}>Row {d.row} ({d.reference}): {d.reason}</li>)}
              </ul>
            )}
            {(result.errors || []).length > 0 && (
              <ul className="text-xs text-red-600 list-disc pl-5">
                {result.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
