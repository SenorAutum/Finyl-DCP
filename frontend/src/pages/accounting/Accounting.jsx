// Accounting: Chart of Accounts + a date-ranged, balanced General Ledger export.
// The GL preview shows every posting with a debits==credits check; the same range
// can be downloaded as CSV. Gated on accounting.export.
import { useEffect, useState } from "react";
import { api, download } from "../../lib/api";
import { Empty, PageHeader, Spinner } from "../../components/ui";

const kes2 = (n) =>
  n == null ? "—" : Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const today = () => new Date().toISOString().slice(0, 10);
const janFirst = () => `${new Date().getFullYear()}-01-01`;

function ChartOfAccounts() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api("/api/v1/accounting/chart-of-accounts").then(setData).catch((e) => setErr(e.detail || "Failed to load."));
  }, []);
  if (err) return <div className="card p-4 text-sm text-red-600">{err}</div>;
  if (!data) return <div className="card p-4"><Spinner /></div>;
  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3 border-b border-border font-bold text-base">Chart of Accounts</div>
      {(data.accounts || []).length === 0 ? <Empty text="No accounts" /> : (
        <table className="w-full text-sm">
          <thead><tr><th className="th">Code</th><th className="th">Name</th><th className="th">Type</th><th className="th">Active</th></tr></thead>
          <tbody>
            {data.accounts.map((a) => (
              <tr key={a.code} className="border-t border-border">
                <td className="td font-mono text-xs">{a.code}</td>
                <td className="td font-medium">{a.name}</td>
                <td className="td capitalize">{a.type}</td>
                <td className="td">{a.active ? "Yes" : "No"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function GlExport() {
  const [from, setFrom] = useState(janFirst());
  const [to, setTo] = useState(today());
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const preview = async () => {
    setLoading(true); setErr(""); setData(null);
    try {
      const r = await api(`/api/v1/accounting/gl-export?from=${from}&to=${to}&format=json`);
      setData(r);
    } catch (e) { setErr(e.detail || "Failed to build the export."); }
    finally { setLoading(false); }
  };
  useEffect(() => { preview(); /* initial load */ }, []); // eslint-disable-line

  const downloadCsv = () =>
    download(`/api/v1/accounting/gl-export?from=${from}&to=${to}&format=csv`, `gl-export_${from}_${to}.csv`);

  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3 border-b border-border flex flex-wrap items-end gap-3">
        <div className="font-bold text-base mr-auto">General Ledger Export</div>
        <div>
          <label className="label">From</label>
          <input type="date" className="input !w-auto" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div>
          <label className="label">To</label>
          <input type="date" className="input !w-auto" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <button className="btn-ghost" disabled={loading} onClick={preview}>Preview</button>
        <button className="btn-primary" disabled={loading} onClick={downloadCsv}>⬇ Download CSV</button>
      </div>

      {err && <div className="px-5 py-2 text-sm text-red-600">{err}</div>}

      {loading ? <Spinner /> : !data ? null : (
        <>
          <div className="px-5 py-2.5 border-b border-border flex flex-wrap items-center gap-4 text-sm bg-canvas/60">
            <span>Lines: <span className="font-semibold">{data.line_count}</span></span>
            <span>Total debit: <span className="font-semibold">KES {kes2(data.total_debit)}</span></span>
            <span>Total credit: <span className="font-semibold">KES {kes2(data.total_credit)}</span></span>
            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold ${
              data.balanced ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"}`}>
              {data.balanced ? "✓ Balanced" : "✕ Not balanced"}
            </span>
          </div>
          {(data.lines || []).length === 0 ? <Empty text="No ledger entries in this range" /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr>
                  <th className="th">Date</th><th className="th">Account</th><th className="th">Description</th>
                  <th className="th text-right">Debit</th><th className="th text-right">Credit</th><th className="th">Reference</th>
                </tr></thead>
                <tbody>
                  {data.lines.map((l, i) => (
                    <tr key={i} className="border-t border-border hover:bg-canvas/60">
                      <td className="td whitespace-nowrap">{l.date}</td>
                      <td className="td"><span className="font-mono text-xs">{l.account_code}</span> {l.account_name}</td>
                      <td className="td">{l.description || "—"}</td>
                      <td className="td text-right">{Number(l.debit) ? kes2(l.debit) : "—"}</td>
                      <td className="td text-right">{Number(l.credit) ? kes2(l.credit) : "—"}</td>
                      <td className="td font-mono text-xs">{l.reference || "—"}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 border-border font-bold bg-canvas/60">
                    <td className="td" colSpan={3}>Totals</td>
                    <td className="td text-right">KES {kes2(data.total_debit)}</td>
                    <td className="td text-right">KES {kes2(data.total_credit)}</td>
                    <td className="td"></td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function Accounting() {
  return (
    <div>
      <PageHeader title="Accounting" crumbs={["Accounting"]} />
      <div className="space-y-5">
        <GlExport />
        <ChartOfAccounts />
      </div>
    </div>
  );
}
