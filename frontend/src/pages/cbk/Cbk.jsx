// CBK Compliance: AML transaction-monitoring flags + simulated regulatory
// export downloads (Asset Quality CSV, Capital Adequacy CSV, CRB daily TXT).
import { useEffect, useState } from "react";
import { api, download } from "../../lib/api";
import { Badge, Empty, PageHeader, Spinner } from "../../components/ui";

const FLAG_LABELS = {
  structuring: "Structuring (split deposits)",
  rapid_small_transactions: "Rapid small transactions",
  velocity: "High-velocity account",
};
const fmtDT = (d) => (d ? new Date(d).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }) : "—");

const EXPORTS = [
  { key: "asset-quality", title: "Asset Quality Return", desc: "Portfolio classification: normal / watch / substandard / doubtful / loss with provisioning.", file: () => `asset_quality_${new Date().toISOString().slice(0, 10)}.csv`, fmt: "CSV" },
  { key: "capital-adequacy", title: "Capital Adequacy Return", desc: "Core capital vs risk-weighted assets with computed CAR ratios.", file: () => `capital_adequacy_${new Date().toISOString().slice(0, 10)}.csv`, fmt: "CSV" },
  { key: "crb-daily", title: "CRB Daily Submission", desc: "Pipe-delimited borrower/loan performance file for Credit Reference Bureau upload.", file: () => `crb_daily_${new Date().toISOString().slice(0, 10)}.txt`, fmt: "TXT" },
];

export default function Cbk() {
  const [flags, setFlags] = useState(null);
  const [reviewed, setReviewed] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api(`/api/v1/cbk/aml/flags?reviewed=${reviewed}`).then(setFlags).catch(() => {});
  useEffect(load, [reviewed]);

  const runScan = async () => {
    setBusy(true); setMsg("");
    try {
      const res = await api("/api/v1/cbk/aml/scan", { method: "POST" });
      setMsg(`AML scan complete — ${res.new_flags} new flag(s) raised.`);
      load();
    } catch (e) { setMsg(`Scan failed: ${e.detail}`); }
    finally { setBusy(false); }
  };

  const review = async (id) => {
    await api(`/api/v1/cbk/aml/flags/${id}/review`, { method: "POST" }).catch(() => {});
    load();
  };

  const doDownload = async (exp) => {
    setMsg("");
    try { await download(`/api/v1/cbk/exports/${exp.key}`, exp.file()); }
    catch (e) { setMsg(`Export failed: ${e.detail || e.message}`); }
  };

  return (
    <div>
      <PageHeader title="CBK Compliance & Reporting" crumbs={["Compliance", "CBK"]}
        actions={<button className="btn-primary" disabled={busy} onClick={runScan}>🔍 Run AML Scan</button>} />

      {msg && <div className="mb-3 text-sm text-teal bg-teal-50 rounded-lg p-3">{msg}</div>}

      {/* Regulatory exports */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-5">
        {EXPORTS.map((exp) => (
          <div key={exp.key} className="card p-5 flex flex-col">
            <div className="flex items-center justify-between">
              <h3 className="font-bold text-sm">{exp.title}</h3>
              <span className="text-[10px] font-bold bg-canvas px-2 py-0.5 rounded-full text-gray-500">{exp.fmt}</span>
            </div>
            <p className="text-xs text-gray-400 mt-1.5 flex-1">{exp.desc}</p>
            <button className="btn-ghost mt-4 !py-1.5 text-sm" onClick={() => doDownload(exp)}>⬇ Download {exp.fmt}</button>
          </div>
        ))}
      </div>

      {/* AML flags */}
      <div className="card overflow-hidden">
        <div className="px-5 py-3.5 border-b border-border flex items-center justify-between flex-wrap gap-2">
          <div>
            <h3 className="font-bold">AML Transaction Monitoring</h3>
            <p className="text-xs text-gray-400">Detects structuring (repeated deposits just under KES 1M reporting threshold), rapid small transactions and abnormal velocity.</p>
          </div>
          <select className="input !w-auto" value={reviewed} onChange={(e) => setReviewed(e.target.value)}>
            <option value="">All flags</option>
            <option value="false">Unreviewed</option>
            <option value="true">Reviewed</option>
          </select>
        </div>
        {!flags ? <Spinner /> : flags.length === 0 ? <Empty text="No AML flags — ledger looks clean" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Flagged</th><th className="th">Borrower</th><th className="th">Type</th>
                <th className="th">Severity</th><th className="th">Details</th><th className="th">Status</th><th className="th"></th>
              </tr></thead>
              <tbody>{flags.map((f) => (
                <tr key={f.id} className="hover:bg-canvas/60">
                  <td className="td whitespace-nowrap">{fmtDT(f.flagged_at)}</td>
                  <td className="td font-semibold">{f.borrower_name || "—"}</td>
                  <td className="td">{FLAG_LABELS[f.flag_type] || f.flag_type}</td>
                  <td className="td"><Badge value={f.severity} /></td>
                  <td className="td max-w-md"><span className="line-clamp-2 text-gray-500 text-xs">{f.details}</span></td>
                  <td className="td">{f.reviewed ? <Badge value="resolved">reviewed</Badge> : <Badge value="pending">unreviewed</Badge>}</td>
                  <td className="td text-right">
                    {!f.reviewed && <button className="btn-ghost !py-1 !px-2.5 text-xs" onClick={() => review(f.id)}>Mark reviewed</button>}
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
