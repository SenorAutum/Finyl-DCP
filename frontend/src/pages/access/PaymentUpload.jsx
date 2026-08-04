// Payment File Upload — bulk reconciliation CSV parser (payments.upload gated).
import { useState } from "react";
import { upload } from "../../lib/api";
import { PageHeader } from "../../components/ui";

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
      <div className="card p-5 max-w-xl">
        <p className="text-sm text-gray-500 mb-4">
          Upload a repayment / reconciliation CSV. Expected columns (case-insensitive):
          <code className="mx-1">account_number</code>, <code className="mx-1">amount</code>,
          <code className="mx-1">mpesa_ref</code>. Each row is matched to a loan by account number
          within your tenant and recorded as a repayment.
        </p>
        <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files[0])} className="input" />
        <button className="btn-primary mt-4" disabled={!file || busy} onClick={submit}>{busy ? "Processing…" : "Upload & reconcile"}</button>
        {err && <div className="mt-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err}</div>}
        {result && (
          <div className="mt-4 text-sm">
            <div className="font-semibold mb-1">{result.filename}</div>
            <div className="flex gap-4">
              <span className="text-accent font-bold">{result.matched} matched</span>
              <span className="text-amber-600 font-bold">{result.unmatched} unmatched</span>
              <span className="text-red-600 font-bold">{(result.errors || []).length} errors</span>
            </div>
            {(result.errors || []).length > 0 && (
              <ul className="mt-2 text-xs text-red-600 list-disc pl-5">
                {result.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
