// WP14 — journals: every entry in the books. Automatic ones come from loans,
// payments, bills, assets and payroll; manual ones are typed here and approved
// by a second person (above the DCP's limit). Nothing is ever deleted: a mistake
// is reversed, and both entries stay.
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { AccountSelect, Attachments, BooksTabs, ErrorNote, Field, Note, amt, forgetAccounts, kes2, today, useBranches, useLoad } from "./common";

const SOURCE = {
  loan_disbursement: "Loan paid out", repayment: "Repayment", suspense_in: "Unallocated payment",
  suspense_out: "Unallocated refunded", bill: "Supplier bill", bill_void: "Bill voided", payment_out: "Payment out",
  expense_claim: "Expense claim", depreciation: "Depreciation", asset_acquired: "Asset bought",
  asset_disposed: "Asset disposed", provision: "Loan loss provision", interest_accrual: "Accrued interest",
  interest_accrual_reversal: "Accrual reversed", reversal: "Reversal", bank_line: "From bank statement",
  platform_invoice: "Invoice to DCP", platform_payment: "Payment from DCP", platform_drawdown: "Loan to DCP",
  payroll: "Payroll",
};
export const sourceLabel = (j) => (j.kind === "manual" && !j.source_type ? "Manual" : SOURCE[j.source_type] || j.source_type || j.kind);

export default function Journals() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState(params.get("status") || "");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const qs = new URLSearchParams({ limit: 50, offset: page * 50, ...(status && { status }), ...(kind && { kind }), ...(q && { q }) });
  const { data, error, loading, reload } = useLoad(`/api/v1/books/journals?${qs}`);
  const [creating, setCreating] = useState(false);
  const open = params.get("id");
  return (
    <div>
      <PageHeader title="Journals" crumbs={["Accounting", "Journals"]}
        actions={can("accounting.post") && <button className="btn-primary" onClick={() => setCreating(true)}>New journal</button>} />
      <BooksTabs />
      <div className="card overflow-hidden">
        <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-border">
          <select className="input !w-auto text-sm" value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }}>
            <option value="">Every status</option><option value="pending">Waiting for approval</option>
            <option value="posted">Posted</option><option value="reversed">Reversed</option><option value="rejected">Rejected</option>
          </select>
          <select className="input !w-auto text-sm" value={kind} onChange={(e) => { setKind(e.target.value); setPage(0); }}>
            <option value="">Every kind</option><option value="manual">Manual</option><option value="auto">Automatic</option><option value="system">Month-end</option>
          </select>
          <input className="input !w-56 text-sm" placeholder="Search number, description, reference" value={q}
            onChange={(e) => { setQ(e.target.value); setPage(0); }} />
        </div>
        <ErrorNote text={error} />
        {loading && !data ? <Spinner /> : !data?.journals?.length ? <Empty text="No journals match." /> : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead><tr><th className="th">Date</th><th className="th">Number</th><th className="th">Description</th><th className="th">From</th>
                <th className="th text-right">Amount</th><th className="th">Status</th></tr></thead>
              <tbody>
                {data.journals.map((j) => (
                  <tr key={j.id} className="hover:bg-canvas cursor-pointer" onClick={() => setParams({ id: j.id })}>
                    <td className="td whitespace-nowrap">{fmtDate(j.date)}</td><td className="td font-mono text-xs">{j.number}</td>
                    <td className="td">{j.description}{j.reference && <span className="text-gray-400"> · {j.reference}</span>}</td>
                    <td className="td text-xs text-gray-500">{sourceLabel(j)}</td>
                    <td className="td text-right tabnums">{amt(j.total)}</td>
                    <td className="td"><Badge value={j.status === "posted" ? "success" : j.status}>{j.status === "pending" ? "waiting" : j.status}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.total > 50 && (
          <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border text-sm">
            <span className="text-gray-500">{page * 50 + 1}–{Math.min(data.total, (page + 1) * 50)} of {data.total}</span>
            <button className="btn-ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
            <button className="btn-ghost" disabled={(page + 1) * 50 >= data.total} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        )}
      </div>
      {creating && <NewJournal onClose={() => setCreating(false)} onDone={() => { setCreating(false); reload(); }} />}
      {open && <JournalModal id={open} onClose={() => { setParams({}); reload(); }} />}
    </div>
  );
}

function NewJournal({ onClose, onDone }) {
  const branches = useBranches();
  const [f, setF] = useState({ date: today(), description: "", reference: "", branch_id: "" });
  const [lines, setLines] = useState([{ account_id: null, debit: "", credit: "", description: "" },
    { account_id: null, debit: "", credit: "", description: "" }]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const dr = lines.reduce((s, l) => s + Number(l.debit || 0), 0);
  const cr = lines.reduce((s, l) => s + Number(l.credit || 0), 0);
  const diff = Math.round((dr - cr) * 100) / 100;
  const set = (i, k, v) => setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v, ...(k === "debit" && v ? { credit: "" } : {}), ...(k === "credit" && v ? { debit: "" } : {}) } : l)));
  const save = async () => {
    setErr(""); setBusy(true);
    try {
      const body = { ...f, branch_id: f.branch_id ? Number(f.branch_id) : null,
        lines: lines.filter((l) => l.account_id && (Number(l.debit) || Number(l.credit)))
          .map((l) => ({ account_id: l.account_id, debit: Number(l.debit || 0), credit: Number(l.credit || 0), description: l.description || null })) };
      const r = await api("/api/v1/books/journals", { method: "POST", body });
      notify(r.status === "pending" ? `${r.number} saved: it waits for a second person to approve it` : `${r.number} posted`, { tone: "success" });
      onDone();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal title="New journal" onClose={onClose} wide>
      <Note>Each line is a debit or a credit. Debits must equal credits. Example — rent paid from the bank:
        debit 6100 Rent, credit 1010 Bank. Opening balances: use 3900 Opening Balance Equity for the other side.</Note>
      <ErrorNote text={err} />
      <div className="grid sm:grid-cols-4 gap-3 mb-3">
        <Field label="Date"><input type="date" className="input" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
        <div className="sm:col-span-2"><Field label="Description"><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} placeholder="What is this entry for?" /></Field></div>
        <Field label="Reference"><input className="input" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
      </div>
      {branches.length > 1 && (
        <div className="mb-3 max-w-xs"><Field label="Branch (optional)">
          <select className="input" value={f.branch_id} onChange={(e) => setF({ ...f, branch_id: e.target.value })}>
            <option value="">Head office / no branch</option>{branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select></Field></div>
      )}
      <table className="w-full text-sm">
        <thead><tr><th className="th">Account</th><th className="th">Line note</th><th className="th text-right w-32">Debit</th><th className="th text-right w-32">Credit</th><th className="th w-8" /></tr></thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td className="td"><AccountSelect value={l.account_id} onChange={(v) => set(i, "account_id", v)} /></td>
              <td className="td"><input className="input" value={l.description} onChange={(e) => set(i, "description", e.target.value)} /></td>
              <td className="td"><input type="number" min="0" step="0.01" className="input text-right" value={l.debit} onChange={(e) => set(i, "debit", e.target.value)} /></td>
              <td className="td"><input type="number" min="0" step="0.01" className="input text-right" value={l.credit} onChange={(e) => set(i, "credit", e.target.value)} /></td>
              <td className="td">{lines.length > 2 && <button className="text-gray-400 hover:text-red-600" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button>}</td>
            </tr>
          ))}
          <tr>
            <td className="td"><button className="text-sm text-accent font-semibold" onClick={() => setLines([...lines, { account_id: null, debit: "", credit: "", description: "" }])}>+ Add a line</button></td>
            <td className="td text-right font-semibold">Totals</td>
            <td className="td text-right tabnums font-semibold">{amt(dr)}</td><td className="td text-right tabnums font-semibold">{amt(cr)}</td><td />
          </tr>
        </tbody>
      </table>
      <div className={`text-sm mt-2 ${diff ? "text-red-600" : "text-emerald-600"}`}>{diff ? `Out of balance by ${amt(Math.abs(diff))}` : dr ? "Balanced" : ""}</div>
      <div className="flex justify-end gap-2 mt-4">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={busy || diff !== 0 || !dr || f.description.length < 3} onClick={save}>{busy ? "Saving…" : "Save journal"}</button>
      </div>
    </Modal>
  );
}

export function JournalModal({ id, onClose }) {
  const { can, user } = useAuth();
  const { data: j, error, reload } = useLoad(`/api/v1/books/journals/${id}`);
  const [reason, setReason] = useState("");
  const [mode, setMode] = useState(null);
  const [err, setErr] = useState("");
  const act = async (path, body) => {
    setErr("");
    try { await api(`/api/v1/books/journals/${id}/${path}`, { method: "POST", body }); notify("Done", { tone: "success" }); setMode(null); forgetAccounts(); reload(); }
    catch (e) { setErr(e.message); }
  };
  const reversible = j && j.status === "posted" && (j.kind === "manual" || ["provision", "bank_line"].includes(j.source_type));
  return (
    <Modal title={j ? `Journal ${j.number}` : "Journal"} onClose={onClose} wide>
      <ErrorNote text={error || err} />
      {!j ? <Spinner /> : (
        <>
          <div className="grid sm:grid-cols-4 gap-3 text-sm mb-4">
            <div><div className="label">Date</div>{fmtDate(j.date)}</div>
            <div className="sm:col-span-2"><div className="label">Description</div>{j.description}</div>
            <div><div className="label">Status</div><Badge value={j.status === "posted" ? "success" : j.status}>{j.status}</Badge></div>
            <div><div className="label">From</div>{sourceLabel(j)}</div>
            <div><div className="label">Reference</div>{j.reference || "—"}</div>
            <div><div className="label">Entered by</div>{j.created_by || "the system"}</div>
            <div><div className="label">Approved by</div>{j.approved_by || "—"}</div>
          </div>
          {j.reject_reason && <div className="text-sm text-red-600 mb-3">Rejected: {j.reject_reason}</div>}
          <table className="w-full text-sm">
            <thead><tr><th className="th">Account</th><th className="th">Note</th><th className="th text-right">Debit</th><th className="th text-right">Credit</th></tr></thead>
            <tbody>
              {j.lines.map((l) => (
                <tr key={l.id}><td className="td">{l.code} {l.name}</td><td className="td text-gray-500 whitespace-normal">{l.description}</td>
                  <td className="td text-right tabnums">{l.debit ? amt(l.debit) : ""}</td><td className="td text-right tabnums">{l.credit ? amt(l.credit) : ""}</td></tr>
              ))}
              <tr><td className="td font-semibold" colSpan={2}>Total</td><td className="td text-right tabnums font-semibold">{kes2(j.total)}</td><td className="td text-right tabnums font-semibold">{kes2(j.total)}</td></tr>
            </tbody>
          </table>
          {j.reversal_of_id && <div className="text-xs text-gray-500 mt-2">This entry reverses journal #{j.reversal_of_id}.</div>}
          {j.reversed_by_id && <div className="text-xs text-gray-500 mt-2">Reversed by journal #{j.reversed_by_id}.</div>}
          <Attachments entity="journal" id={j.id} items={j.attachments} onDone={reload} canAdd={can("accounting.post")} />
          {mode && (
            <div className="mt-4">
              <Field label={mode === "reject" ? "Why is it rejected?" : "Why is it reversed?"}>
                <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
              </Field>
              <div className="flex justify-end gap-2 mt-2">
                <button className="btn-ghost" onClick={() => setMode(null)}>Back</button>
                <button className="btn-primary" disabled={reason.length < 5} onClick={() => act(mode, { reason })}>{mode === "reject" ? "Reject" : "Reverse it"}</button>
              </div>
            </div>
          )}
          {!mode && (
            <div className="flex justify-end gap-2 mt-4">
              {j.status === "pending" && can("accounting.approve") && (
                <>
                  <button className="btn-ghost" onClick={() => setMode("reject")}>Reject</button>
                  <button className="btn-primary" disabled={j.created_by_user_id === user?.id && user?.role !== "super_admin"}
                    title={j.created_by_user_id === user?.id ? "Someone else must approve your journal" : ""} onClick={() => act("approve")}>Approve and post</button>
                </>
              )}
              {reversible && can("accounting.approve") && <button className="btn-ghost" onClick={() => setMode("reverse")}>Reverse</button>}
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
