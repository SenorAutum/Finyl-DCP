// CBK Compliance: two views.
//  • AML & Exports — AML transaction-monitoring flags + simulated regulatory
//    export downloads (Asset Quality CSV, Capital Adequacy CSV, CRB daily TXT).
//  • GDI Submissions — Central Bank of Kenya Granular Data Interface monthly
//    submission of the five mandatory datasets, submission history, status
//    checks, nil submissions and the compliance filing calendar.
import { useEffect, useState } from "react";
import { api, download } from "../../lib/api";
import { Badge, Empty, PageHeader, Spinner } from "../../components/ui";

const FLAG_LABELS = {
  structuring: "Structuring (split deposits)",
  rapid_small_transactions: "Rapid small transactions",
  velocity: "High-velocity account",
};
const fmtDT = (d) => (d ? new Date(d).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }) : "—");
const fmtMonth = (d) =>
  d ? new Date(d + (d.length === 7 ? "-01" : "")).toLocaleDateString("en-KE", { month: "long", year: "numeric" }) : "—";

// Map a submission status to a Badge colour value.
const STATUS_BADGE = { pending: "pending", submitted: "approved", error: "failed", accepted: "success", rejected: "rejected" };

const EXPORTS = [
  { key: "asset-quality", title: "Asset Quality Return", desc: "Portfolio classification: normal / watch / substandard / doubtful / loss with provisioning.", file: () => `asset_quality_${new Date().toISOString().slice(0, 10)}.csv`, fmt: "CSV" },
  { key: "capital-adequacy", title: "Capital Adequacy Return", desc: "Core capital vs risk-weighted assets with computed CAR ratios.", file: () => `capital_adequacy_${new Date().toISOString().slice(0, 10)}.csv`, fmt: "CSV" },
  { key: "crb-daily", title: "CRB Daily Submission", desc: "Pipe-delimited client/loan performance file for Credit Reference Bureau upload.", file: () => `crb_daily_${new Date().toISOString().slice(0, 10)}.txt`, fmt: "TXT" },
];

// The five mandatory datasets, in submission sequence.
const GDI_DATASETS = [
  { key: "customer", label: "Customer Data" },
  { key: "loan", label: "Digital Loan Accounts" },
  { key: "repayment", label: "Loan Repayments" },
  { key: "overdue", label: "Overdue / Non-Performing Loans" },
  { key: "complaint", label: "Customer Complaints" },
];

// Previous calendar month as "YYYY-MM".
function prevMonthStr() {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

// Filing deadline = 5 working days after the end of the reporting month.
function filingDeadline(monthStr) {
  if (!monthStr) return null;
  const [y, m] = monthStr.split("-").map(Number);
  let d = new Date(y, m, 0); // last day of reporting month
  let added = 0;
  while (added < 5) {
    d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);
    const wd = d.getDay();
    if (wd !== 0 && wd !== 6) added += 1;
  }
  return d;
}

export default function Cbk() {
  const [tab, setTab] = useState("aml");
  return (
    <div>
      <PageHeader title="CBK Compliance & Reporting" crumbs={["Compliance", "CBK"]} />
      <div className="flex gap-1 mb-5 border-b border-border">
        {[
          ["aml", "AML & Exports"],
          ["gdi", "GDI Submissions"],
        ].map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-4 py-2 text-sm font-semibold -mb-px border-b-2 transition ${
              tab === k ? "border-accent text-accent" : "border-transparent text-gray-400 hover:text-charcoal"}`}>
            {label}
          </button>
        ))}
      </div>
      {tab === "aml" ? <AmlExports /> : <GdiSubmissions />}
    </div>
  );
}

// ---------- AML & Exports view ----------------------------------------------
function AmlExports() {
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
      <div className="flex justify-end mb-3">
        <button className="btn-primary" disabled={busy} onClick={runScan}>🔍 Run AML Scan</button>
      </div>
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
                <th className="th">Flagged</th><th className="th">Client</th><th className="th">Type</th>
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

// ---------- GDI Submissions view --------------------------------------------
function GdiSubmissions() {
  const [month, setMonth] = useState(prevMonthStr());
  const [subs, setSubs] = useState(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState("");
  const [nilDs, setNilDs] = useState("customer");

  const load = () => api("/api/v1/cbk/gdi/submissions").then(setSubs).catch(() => setSubs([]));
  useEffect(load, []);

  // Latest log row for each dataset within the selected month (progress view).
  const progress = GDI_DATASETS.map((ds) => {
    const row = (subs || []).find(
      (s) => s.reporting_month === `${month}-01` && (s.dataset_name || "").startsWith(ds.label));
    return { ...ds, row };
  });

  const submit = async () => {
    setBusy(true); setMsg("");
    try {
      const res = await api("/api/v1/cbk/gdi/submit", {
        method: "POST", body: { reporting_month: month },
      });
      setMsg(`${res.message} for ${fmtMonth(month)} — ${res.datasets.length} datasets queued. Refresh in a moment to see progress.`);
      setTimeout(load, 1500);
    } catch (e) { setMsg(`Submission failed: ${e.detail || e.message}`); }
    finally { setBusy(false); }
  };

  const checkStatus = async () => {
    setChecking("all"); setMsg("");
    try {
      const res = await api("/api/v1/cbk/gdi/check-status", {
        method: "POST", body: { reporting_month: month },
      });
      setMsg(res.ok
        ? `Status checked with CBK for ${fmtMonth(month)}.`
        : `Status check returned HTTP ${res.status_code}.`);
      load();
    } catch (e) { setMsg(`Status check failed: ${e.detail || e.message}`); }
    finally { setChecking(""); }
  };

  const nilSubmit = async () => {
    setBusy(true); setMsg("");
    try {
      const res = await api("/api/v1/cbk/gdi/nil-submission", {
        method: "POST", body: { reporting_month: month, dataset_name: nilDs },
      });
      setMsg(`Nil submission ${res.status} for ${GDI_DATASETS.find((d) => d.key === nilDs)?.label}.`);
      load();
    } catch (e) { setMsg(`Nil submission failed: ${e.detail || e.message}`); }
    finally { setBusy(false); }
  };

  const deadline = filingDeadline(month);
  const daysLeft = deadline ? Math.ceil((deadline - new Date()) / 86400000) : null;

  return (
    <div>
      {msg && <div className="mb-3 text-sm text-teal bg-teal-50 rounded-lg p-3">{msg}</div>}

      {/* Controls + compliance calendar */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-5">
        <div className="card p-5 lg:col-span-2">
          <h3 className="font-bold text-sm mb-3">Monthly GDI Submission</h3>
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-gray-500 font-semibold">
              Reporting month
              <input type="month" className="input !w-auto mt-1 block" value={month}
                max={prevMonthStr()} onChange={(e) => setMonth(e.target.value)} />
            </label>
            <button className="btn-primary" disabled={busy} onClick={submit}>📤 Submit to CBK</button>
            <button className="btn-ghost" disabled={checking === "all"} onClick={checkStatus}>🔄 Check Status</button>
          </div>
          <div className="mt-4 flex flex-wrap items-end gap-2 border-t border-border pt-4">
            <label className="text-xs text-gray-500 font-semibold">
              Nil submission
              <select className="input !w-auto mt-1 block" value={nilDs} onChange={(e) => setNilDs(e.target.value)}>
                {GDI_DATASETS.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
              </select>
            </label>
            <button className="btn-ghost" disabled={busy} onClick={nilSubmit}>Submit Nil</button>
            <p className="text-[11px] text-gray-400 flex-1 min-w-[12rem]">Send an empty (nil) return for a dataset with no records for the selected month.</p>
          </div>
        </div>
        <div className="card p-5">
          <h3 className="font-bold text-sm mb-2">Compliance Calendar</h3>
          <p className="text-xs text-gray-400">Reporting month</p>
          <p className="font-bold">{fmtMonth(month)}</p>
          <p className="text-xs text-gray-400 mt-3">Filing deadline (5 working days after month end)</p>
          <p className="font-bold">{deadline ? deadline.toLocaleDateString("en-KE", { dateStyle: "full" }) : "—"}</p>
          {daysLeft != null && (
            <div className="mt-3">
              <Badge value={daysLeft < 0 ? "failed" : daysLeft <= 2 ? "overdue" : "approved"}>
                {daysLeft < 0 ? `${Math.abs(daysLeft)} day(s) overdue` : `${daysLeft} day(s) remaining`}
              </Badge>
            </div>
          )}
        </div>
      </div>

      {/* Submission progress for selected month */}
      <div className="card overflow-hidden mb-5">
        <div className="px-5 py-3.5 border-b border-border">
          <h3 className="font-bold">Submission Progress — {fmtMonth(month)}</h3>
          <p className="text-xs text-gray-400">Datasets are submitted in the mandatory CBK sequence.</p>
        </div>
        <div className="divide-y divide-border">
          {progress.map((p, i) => (
            <div key={p.key} className="px-5 py-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="text-xs font-bold text-gray-300 w-5">{i + 1}</span>
                <span className="font-semibold text-sm">{p.label}</span>
              </div>
              <div className="flex items-center gap-3">
                {p.row && <span className="text-xs text-gray-400">{p.row.rows_submitted} row(s)</span>}
                {p.row
                  ? <Badge value={STATUS_BADGE[p.row.status] || "pending"}>{p.row.status}</Badge>
                  : <Badge value="pending">not submitted</Badge>}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Submission history */}
      <div className="card overflow-hidden">
        <div className="px-5 py-3.5 border-b border-border">
          <h3 className="font-bold">Submission History</h3>
        </div>
        {!subs ? <Spinner /> : subs.length === 0 ? <Empty text="No GDI submissions yet" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Submitted</th><th className="th">Month</th><th className="th">Dataset</th>
                <th className="th">Rows</th><th className="th">Status</th><th className="th">Detail</th><th className="th"></th>
              </tr></thead>
              <tbody>{subs.map((s) => (
                <tr key={s.id} className="hover:bg-canvas/60">
                  <td className="td whitespace-nowrap">{fmtDT(s.submitted_at)}</td>
                  <td className="td whitespace-nowrap">{fmtMonth(s.reporting_month)}</td>
                  <td className="td font-semibold">{s.dataset_name}</td>
                  <td className="td">{s.rows_submitted}</td>
                  <td className="td"><Badge value={STATUS_BADGE[s.status] || "pending"}>{s.status}</Badge></td>
                  <td className="td max-w-xs"><span className="line-clamp-2 text-gray-500 text-xs">{s.error_detail || (s.cbk_request_id ? `CBK ref: ${s.cbk_request_id}` : "—")}</span></td>
                  <td className="td text-right">
                    <button className="btn-ghost !py-1 !px-2.5 text-xs" disabled={checking === s.id}
                      onClick={async () => {
                        setChecking(s.id); setMsg("");
                        try {
                          const res = await api("/api/v1/cbk/gdi/check-status", {
                            method: "POST", body: { reporting_month: (s.reporting_month || "").slice(0, 7) },
                          });
                          setMsg(res.ok ? "Status checked with CBK." : `Status check returned HTTP ${res.status_code}.`);
                          load();
                        } catch (e) { setMsg(`Status check failed: ${e.detail || e.message}`); }
                        finally { setChecking(""); }
                      }}>Check Status</button>
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
