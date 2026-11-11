// WP11/WP12: Statement import — bank statements and M-Pesa paybill/till statements
// (not the per-client affordability statements under
// Collections). Upload the DCP's bank statement (CSV or .xlsx),
// check what will happen to every money-in line, then import:
//   * lines naming a loan account number or customer number are recorded as
//     payments (straight away below the DCP's manual-payment threshold,
//     otherwise they wait for a second person in Approvals);
//   * the rest go to the Suspense Account for a person to allocate or refund;
//   * lines whose bank reference is already recorded are skipped.
// The columns are found from the headings; they can be corrected and the
// choice saved as a layout for next time (one per bank account).
import { useEffect, useMemo, useState } from "react";
import { api, upload } from "../../lib/api";
import { Badge, Empty, PageHeader } from "../../components/ui";

const kes = (n) => (n == null ? "—" : `KES ${Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const FIELDS = [
  ["date", "Date", true], ["description", "Description / narrative", false],
  ["reference", "Reference (bank reference or M-Pesa receipt)", false], ["credit", "Credit (money in)", false],
  ["debit", "Debit (money out)", false], ["amount", "Amount (if no credit column)", false],
  ["balance", "Balance", false],
  ["account", "Account number the payer typed (M-Pesa: A/C No.)", false],
  ["payer", "Payer (M-Pesa: Other Party Info)", false],
  ["status", "Transaction status", false],
];
const STATUS = {
  matched: ["approved", "Matched"], review: ["overdue", "To review"],
  duplicate: ["closed", "Already recorded"], skipped: ["draft", "Not money in"], error: ["failed", "Problem"],
};
const REASONS = {
  bank_unmatched: "no account or customer number found",
  bank_several: "names more than one account",
  no_open_loan: "customer has no open loan",
  ambiguous_customer: "customer has several open loans",
  closed_loan: "loan is not open",
};

export default function BankImport() {
  const [layouts, setLayouts] = useState({});
  const [layoutName, setLayoutName] = useState("");
  const [file, setFile] = useState(null);
  const [layout, setLayout] = useState(null);
  const [prev, setPrev] = useState(null);
  const [leaveOut, setLeaveOut] = useState(new Set());
  const [bankAccount, setBankAccount] = useState("");
  const [saveAs, setSaveAs] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState(null);

  const loadLayouts = () => api("/api/v1/bank-import/layouts").then((d) => setLayouts(d.layouts || {})).catch(() => {});
  useEffect(() => { loadLayouts(); }, []);

  const form = (useLayout) => {
    const fd = new FormData();
    fd.append("file", file);
    if (useLayout) fd.append("layout", JSON.stringify(useLayout));
    else if (layoutName) fd.append("layout_name", layoutName);
    return fd;
  };

  async function preview(useLayout = null) {
    if (!file) { setErr("Choose the statement file first."); return; }
    setBusy(true); setErr(""); setDone(null);
    try {
      const d = await upload("/api/v1/bank-import/preview", form(useLayout));
      setPrev(d);
      setLayout(d.layout || { header_row: 1, date_order: "dmy" });
      setLeaveOut(new Set());
      if (layoutName && !saveAs) setSaveAs(layoutName);
    } catch (e) { setErr(e.detail || "Could not read the file"); setPrev(null); }
    finally { setBusy(false); }
  }

  async function runImport() {
    setBusy(true); setErr("");
    try {
      const fd = form(layout);
      fd.append("bank_account", bankAccount);
      if (saveAs.trim()) fd.append("save_as", saveAs.trim());
      if (leaveOut.size) fd.append("leave_out", [...leaveOut].join(","));
      const d = await upload("/api/v1/bank-import/import", fd);
      setDone(d); setPrev(null); setFile(null); loadLayouts();
    } catch (e) { setErr(e.detail || "Import failed"); }
    finally { setBusy(false); }
  }

  async function removeLayout(name) {
    if (!window.confirm(`Delete the saved layout "${name}"?`)) return;
    try { const d = await api(`/api/v1/bank-import/layouts/${encodeURIComponent(name)}`, { method: "DELETE" }); setLayouts(d.layouts || {}); setLayoutName(""); }
    catch (e) { setErr(e.detail || "Could not delete"); }
  }

  // Heading cells to choose columns from: the detected columns, or the row
  // the person says holds the headings.
  const columns = useMemo(() => {
    if (!prev) return [];
    if (prev.columns?.length && prev.layout) return prev.columns;
    const r = (prev.first_rows || [])[(Number(layout?.header_row) || 1) - 1] || [];
    return r.filter(Boolean);
  }, [prev, layout]);

  const toggle = (line) => setLeaveOut((s) => { const n = new Set(s); if (n.has(line)) n.delete(line); else n.add(line); return n; });
  const lines = prev?.lines || [];
  const willRecord = lines.filter((l) => ["matched", "review"].includes(l.status) && !leaveOut.has(l.line));

  return (
    <div>
      <PageHeader title="Statement Import" crumbs={["Payments", "Statement Import"]} />
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        Upload a bank statement, or the statement of your paybill or till from the M-Pesa business portal,
        as CSV or Excel (.xlsx). Money-in lines that mention a loan account number (e.g. FL/FY2026/12/34)
        or a customer number are recorded against that loan. An M-Pesa statement catches paybill payments
        whose notification never reached Finyl: its receipt numbers are the same, so nothing is counted twice. Everything else
        goes to the Suspense Account for someone to allocate. Importing the same statement twice records
        nothing twice. PDF and old .xls files cannot be read: download CSV or Excel from your bank, or
        open the file in Excel and save it as .xlsx.
      </p>
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      {done && (
        <div className="mb-4 text-sm bg-emerald-50 text-emerald-800 border border-emerald-200 rounded-lg p-3">
          Imported {done.filename}: {done.applied} applied, {done.pending_approval} waiting for approval
          (see Approvals), {done.suspense} sent to the Suspense Account, {done.duplicates} already recorded
          {done.left_out ? `, ${done.left_out} left out` : ""}{done.errors ? `, ${done.errors} with problems` : ""}.
        </div>
      )}

      <div className="card p-4 mb-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="text-xs font-semibold text-gray-600 block">Statement file</label>
          <input type="file" accept=".csv,.xlsx,.txt" className="text-sm"
            onChange={(e) => { setFile(e.target.files?.[0] || null); setPrev(null); setDone(null); }} />
        </div>
        <div>
          <label className="text-xs font-semibold text-gray-600 block">Columns</label>
          <select className="input !w-64" value={layoutName} onChange={(e) => setLayoutName(e.target.value)}>
            <option value="">Find automatically</option>
            {Object.keys(layouts).map((n) => <option key={n} value={n}>Saved: {n}</option>)}
          </select>
        </div>
        <button className="btn-primary" disabled={busy || !file} onClick={() => preview()}>{busy ? "Reading…" : "Preview"}</button>
        {layoutName && <button className="btn-ghost text-red-600 text-xs" onClick={() => removeLayout(layoutName)}>Delete saved layout</button>}
      </div>

      {prev && (
        <div className="card p-4 mb-4">
          <div className="font-semibold mb-1">Columns used</div>
          {prev.message && <div className="text-sm text-amber-800 bg-amber-50 rounded-lg p-2 mb-2">{prev.message}</div>}
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div>
              <label className="text-xs font-semibold text-gray-600">Headings are on line</label>
              <input className="input" type="number" min="1" value={layout?.header_row || ""}
                onChange={(e) => setLayout((l) => ({ ...l, header_row: Number(e.target.value) || null }))} />
            </div>
            <div>
              <label className="text-xs font-semibold text-gray-600">This is</label>
              <select className="input" value={layout?.kind || "bank"} onChange={(e) => setLayout((l) => ({ ...l, kind: e.target.value }))}>
                <option value="bank">A bank statement</option>
                <option value="mpesa">An M-Pesa paybill / till statement</option>
              </select>
            </div>
            <div>
              <label className="text-xs font-semibold text-gray-600">Dates are written</label>
              <select className="input" value={layout?.date_order || "dmy"} onChange={(e) => setLayout((l) => ({ ...l, date_order: e.target.value }))}>
                <option value="dmy">Day first (31/12/2026)</option>
                <option value="mdy">Month first (12/31/2026)</option>
                <option value="ymd">Year first (2026-12-31)</option>
              </select>
            </div>
            {FIELDS.map(([k, label, req]) => (
              <div key={k}>
                <label className="text-xs font-semibold text-gray-600">{label}{req ? " *" : ""}</label>
                <select className="input" value={layout?.[k] || ""} onChange={(e) => setLayout((l) => ({ ...l, [k]: e.target.value || null }))}>
                  <option value="">— none —</option>
                  {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            ))}
          </div>
          <button className="btn-ghost mt-3" disabled={busy} onClick={() => preview(layout)}>Preview again with these columns</button>
          {!prev.layout && (prev.first_rows || []).length > 0 && (
            <div className="overflow-x-auto mt-3">
              <table className="text-xs">
                <tbody>{prev.first_rows.map((r, i) => (
                  <tr key={i} className="border-t border-border"><td className="td text-gray-400">{i + 1}</td>{r.map((c, j) => <td key={j} className="td">{c}</td>)}</tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {prev?.layout && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
            {Object.entries(STATUS).map(([k, [, label]]) => (
              <div key={k} className="card p-3">
                <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
                <div className="text-lg font-bold">{prev.summary?.[k]?.count ?? 0}</div>
                <div className="text-xs text-gray-500">{kes(prev.summary?.[k]?.amount)}</div>
              </div>
            ))}
          </div>
          <div className="card overflow-hidden mb-4">
            {lines.length === 0 ? <Empty text="No lines after the headings" /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr>
                    <th className="th">Record</th><th className="th">Line</th><th className="th">Date</th>
                    <th className="th">Description</th><th className="th">Reference</th>
                    <th className="th">Amount</th><th className="th">What happens</th>
                  </tr></thead>
                  <tbody>
                    {lines.map((l) => {
                      const can = ["matched", "review"].includes(l.status);
                      const [tone, label] = STATUS[l.status] || ["draft", l.status];
                      return (
                        <tr key={l.line} className="border-t border-border">
                          <td className="td">{can && <input type="checkbox" checked={!leaveOut.has(l.line)} onChange={() => toggle(l.line)} />}</td>
                          <td className="td text-gray-400">{l.line}</td>
                          <td className="td whitespace-nowrap">{l.date || "—"}</td>
                          <td className="td max-w-xs truncate" title={l.description}>{l.description || "—"}</td>
                          <td className="td font-mono text-xs">{l.reference || "—"}</td>
                          <td className="td font-semibold whitespace-nowrap">{kes(l.amount)}</td>
                          <td className="td">
                            <Badge value={tone}>{label}</Badge>
                            {l.status === "matched" && <div className="text-xs mt-1">{l.account_number} · {l.client} <span className="text-gray-400">(by {l.matched_by === "customer_number" ? "customer number" : "account number"})</span></div>}
                            {l.status === "review" && <div className="text-xs mt-1 text-amber-800">Suspense: {REASONS[l.reason] || l.reason}{l.suggested_loan_id ? ` · suggested loan #${l.suggested_loan_id}` : ""}</div>}
                            {(l.account_entered || l.payer) && <div className="text-[11px] text-gray-400">{l.account_entered ? `account typed: ${l.account_entered}` : ""}{l.account_entered && l.payer ? " · " : ""}{l.payer || ""}</div>}
                            {["duplicate", "skipped", "error"].includes(l.status) && <div className="text-xs mt-1 text-gray-500">{l.reason}</div>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <div className="card p-4 flex flex-wrap items-end gap-3">
            <div>
              <label className="text-xs font-semibold text-gray-600 block">Bank account (for the records)</label>
              <input className="input !w-64" placeholder="e.g. Equity collections 0123…" value={bankAccount} onChange={(e) => setBankAccount(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-semibold text-gray-600 block">Save these columns as (optional)</label>
              <input className="input !w-56" placeholder="e.g. Equity collections" value={saveAs} onChange={(e) => setSaveAs(e.target.value)} />
            </div>
            <button className="btn-primary" disabled={busy || willRecord.length === 0} onClick={runImport}>
              {busy ? "Importing…" : `Import ${willRecord.length} line${willRecord.length === 1 ? "" : "s"}`}
            </button>
            <span className="text-xs text-gray-500">Unticked lines are not recorded.</span>
          </div>
        </>
      )}
    </div>
  );
}
