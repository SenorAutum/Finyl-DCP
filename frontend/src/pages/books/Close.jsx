// WP14 — month-end close: a checklist, the IFRS 9 loan-loss provision, accrued
// interest, depreciation, then lock the month so nothing changes it afterwards.
import { useState } from "react";
import { Link } from "react-router-dom";
import { Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { BooksTabs, ErrorNote, Field, Note, amt, kes2, useLoad } from "./common";

function lastMonthStr() {
  const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
const LINK = { auto: "/books", approvals: "/books/journals?status=pending", depreciation: "/books/assets", reconciled: "/books/reconciliation",
  trial_balance: "/books/reports?tab=tb" };

export default function Close() {
  const { can } = useAuth();
  const [period, setPeriod] = useState(lastMonthStr());
  const { data: d, error, loading, reload } = useLoad(`/api/v1/books/close?period=${period}`);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [override, setOverride] = useState(null);
  const [reopen, setReopen] = useState(null);
  const post = async (path, body, label) => {
    setErr(""); setBusy(path);
    try { const r = await api(`/api/v1/books/close/${path}`, { method: "POST", body }); notify(label(r), { tone: "success" }); reload(); return true; }
    catch (e) { setErr(e.message); return false; } finally { setBusy(""); }
  };
  return (
    <div>
      <PageHeader title="Month-end close" crumbs={["Accounting", "Close"]} />
      <BooksTabs />
      <div className="flex items-end gap-3 mb-4">
        <Field label="Month"><input type="month" className="input" value={period} onChange={(e) => setPeriod(e.target.value)} /></Field>
        {d && <div className="text-sm text-gray-500 pb-2">{d.lock_date ? `Books closed up to ${fmtDate(d.lock_date)}` : "No month closed yet"}</div>}
      </div>
      <ErrorNote text={error || err} />
      {loading && !d ? <Spinner /> : d && (
        <div className="grid lg:grid-cols-3 gap-4">
          <div className="card p-4 lg:col-span-2">
            <div className="font-bold mb-3">Checklist for {fmtDate(d.period_end)}</div>
            <ul className="space-y-2">
              {d.items.map((i) => (
                <li key={i.key} className="flex items-start gap-3 text-sm">
                  <span className={`mt-0.5 w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold ${i.ok ? "bg-emerald-100 text-emerald-700" : i.optional ? "bg-gray-100 text-gray-400" : "bg-amber-100 text-amber-700"}`}>
                    {i.ok ? "✓" : i.optional ? "–" : "!"}</span>
                  <div className="flex-1"><div className="font-semibold">{i.label}</div>{i.detail && <div className="text-xs text-gray-500">{i.detail}</div>}</div>
                  {!i.ok && LINK[i.key] && <Link className="text-xs font-semibold text-accent" to={LINK[i.key]}>Go there</Link>}
                </li>
              ))}
            </ul>
            <div className="border-t border-border mt-4 pt-4 flex flex-wrap gap-2 items-center">
              {d.closed ? <span className="text-emerald-700 font-semibold">✓ This month is closed.</span> : can("accounting.close") && (
                <button className="btn-primary" disabled={!!busy} onClick={async () => {
                  if (!d.ready) { setOverride(""); return; }
                  await post("lock", { period }, (r) => `Books closed up to ${fmtDate(r.lock_date)}`);
                }}>Close the month</button>
              )}
              {d.lock_date && can("accounting.close") && <button className="btn-ghost" onClick={() => setReopen({ lock_date: "", reason: "" })}>Reopen a closed month</button>}
            </div>
          </div>
          <div className="space-y-4">
            <div className="card p-4">
              <div className="font-bold">Loan loss provision (IFRS 9)</div>
              <div className="text-xs text-gray-500 mb-2">Stage 1 up to 30 days late, stage 2 31–90, stage 3 over 90 or defaulted. Rates are set under DCP settings.</div>
              <table className="w-full text-sm mb-2"><tbody>
                {d.provision.stages.map((s) => <tr key={s.stage}><td className="py-0.5">Stage {s.stage} ({s.loans} loans, {Math.round(s.rate * 100)}%)</td>
                  <td className="text-right tabnums">{amt(s.provision)}</td></tr>)}
                <tr className="border-t border-border font-semibold"><td className="py-1">Needed</td><td className="text-right tabnums">{amt(d.provision.required)}</td></tr>
                <tr><td className="py-0.5">Already in the books</td><td className="text-right tabnums">{amt(d.provision.current)}</td></tr>
                <tr className="font-bold"><td className="py-0.5">To post</td><td className="text-right tabnums">{amt(d.provision.adjustment)}</td></tr>
              </tbody></table>
              {d.provision.already_posted ? <div className="text-xs text-emerald-700 font-semibold">✓ Posted for this month</div>
                : can("accounting.close") && <button className="btn-ghost w-full" disabled={!!busy || d.closed}
                  onClick={() => post("provision", { period }, (r) => (r.posted ? "Provision posted" : "Nothing to change"))}>Post the provision</button>}
            </div>
            <div className="card p-4">
              <div className="font-bold">Accrued interest (optional)</div>
              <div className="text-xs text-gray-500 mb-2">Interest earned on loans up to the month end but not yet paid. It is reversed the next day, so it is only counted once when paid. Loans over 90 days late are left out.</div>
              <div className="text-sm mb-2">{d.accrual.loans} loans · <b>{kes2(d.accrual.total)}</b></div>
              {d.accrual.already_posted ? <div className="text-xs text-emerald-700 font-semibold">✓ Posted for this month</div>
                : can("accounting.close") && <button className="btn-ghost w-full" disabled={!!busy || d.closed || !d.accrual.total}
                  onClick={() => post("accrual", { period }, () => "Accrued interest posted")}>Post accrued interest</button>}
            </div>
            <div className="card p-4 text-sm">
              <div className="font-bold">Depreciation</div>
              <div className="text-xs text-gray-500 mb-2">{d.depreciation.rows} assets to depreciate this month.</div>
              <Link className="btn-ghost inline-block" to="/books/assets">Open fixed assets</Link>
            </div>
          </div>
        </div>
      )}
      {override !== null && (
        <Modal title="Close the month anyway?" onClose={() => setOverride(null)}>
          <Note>Some checklist items are not done. You can still close the month, with a reason that is kept in the audit trail.</Note>
          <Field label="Reason (10 characters or more)"><input className="input" value={override} onChange={(e) => setOverride(e.target.value)} /></Field>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setOverride(null)}>Cancel</button>
            <button className="btn-primary" disabled={override.length < 10} onClick={async () => {
              if (await post("lock", { period, override_reason: override }, (r) => `Books closed up to ${fmtDate(r.lock_date)}`)) setOverride(null);
            }}>Close the month</button></div>
        </Modal>
      )}
      {reopen && (
        <Modal title="Reopen closed months" onClose={() => setReopen(null)}>
          <Note>Entries can then be made in those months again. Close them again when the correction is done.</Note>
          <Field label="Keep closed up to (empty = reopen everything)"><input type="date" className="input" value={reopen.lock_date} onChange={(e) => setReopen({ ...reopen, lock_date: e.target.value })} /></Field>
          <div className="mt-3"><Field label="Why?"><input className="input" value={reopen.reason} onChange={(e) => setReopen({ ...reopen, reason: e.target.value })} /></Field></div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setReopen(null)}>Cancel</button>
            <button className="btn-primary" disabled={reopen.reason.length < 5} onClick={async () => {
              if (await post("reopen", { lock_date: reopen.lock_date || null, reason: reopen.reason }, (r) => (r.lock_date ? `Closed up to ${fmtDate(r.lock_date)}` : "All months open"))) setReopen(null);
            }}>Reopen</button></div>
        </Modal>
      )}
    </div>
  );
}
