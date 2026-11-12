// WP14 — staff expense claims. Anyone can claim (with receipts); finance approves
// claims from other people, then pays them like a bill.
import { useState } from "react";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { Attachments, BooksTabs, ErrorNote, Field, MoneyAccountSelect, Note, amt, kes2, today, useLoad } from "./common";

const BADGE = { submitted: ["pending", "waiting approval"], approved: ["approved", "approved, unpaid"], paid: ["paid", "paid"], rejected: ["rejected", "rejected"] };

export default function Claims() {
  const { can } = useAuth();
  const [mine, setMine] = useState(!can("accounting.view"));
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(`/api/v1/books/claims?mine=${mine}${status ? `&status=${status}` : ""}`);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState(null);
  const rows = data?.claims || [];
  return (
    <div>
      <PageHeader title="Expense claims" crumbs={["Accounting", "Expense claims"]}
        actions={<button className="btn-primary" onClick={() => setCreating(true)}>New claim</button>} />
      {can("accounting.view") && <BooksTabs />}
      <div className="card overflow-hidden">
        <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-border items-center">
          {data?.can_view_all && (
            <select className="input !w-auto text-sm" value={mine ? "mine" : "all"} onChange={(e) => setMine(e.target.value === "mine")}>
              <option value="all">Everyone's claims</option><option value="mine">My claims</option></select>
          )}
          <select className="input !w-auto text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option><option value="submitted">Waiting approval</option><option value="approved">Approved, unpaid</option>
            <option value="paid">Paid</option><option value="rejected">Rejected</option></select>
        </div>
        <ErrorNote text={error} />
        {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No expense claims yet." /> : (
          <table className="min-w-full text-sm">
            <thead><tr><th className="th">Number</th><th className="th">Date</th><th className="th">By</th><th className="th">What for</th>
              <th className="th text-right">Amount</th><th className="th">Status</th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="hover:bg-canvas cursor-pointer" onClick={() => setOpen(c.id)}>
                  <td className="td font-mono text-xs">{c.number}</td><td className="td">{fmtDate(c.claim_date)}</td><td className="td">{c.claimant}</td>
                  <td className="td">{c.description}</td><td className="td text-right tabnums">{amt(c.total)}</td>
                  <td className="td"><Badge value={BADGE[c.status]?.[0]}>{BADGE[c.status]?.[1] || c.status}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {creating && <ClaimForm onClose={() => setCreating(false)} onDone={(id) => { setCreating(false); reload(); setOpen(id); }} />}
      {open && <ClaimModal id={open} canApprove={data?.can_approve} onClose={() => { setOpen(null); reload(); }} />}
    </div>
  );
}

function ClaimForm({ onClose, onDone }) {
  const { data } = useLoad("/api/v1/books/expense-accounts");
  const [f, setF] = useState({ claim_date: today(), description: "" });
  const [lines, setLines] = useState([{ account_id: "", description: "", amount: "", receipt_ref: "" }]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const total = lines.reduce((s, l) => s + Number(l.amount || 0), 0);
  const set = (i, k, v) => setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)));
  const save = async () => {
    setErr(""); setBusy(true);
    try {
      const r = await api("/api/v1/books/claims", { method: "POST", body: { ...f, lines: lines.filter((l) => l.account_id && Number(l.amount) > 0)
        .map((l) => ({ account_id: Number(l.account_id), description: l.description || null, amount: Number(l.amount), receipt_ref: l.receipt_ref || null })) } });
      notify(`${r.number} submitted. Attach the receipts next.`, { tone: "success" });
      onDone(r.id);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal title="New expense claim" onClose={onClose} wide>
      <Note>Claim money you spent for work from your own pocket. Add each receipt as a line, then attach photos of the receipts.</Note>
      <ErrorNote text={err} />
      <div className="grid sm:grid-cols-3 gap-3 mb-3">
        <Field label="Date"><input type="date" className="input" value={f.claim_date} max={today()} onChange={(e) => setF({ ...f, claim_date: e.target.value })} /></Field>
        <div className="sm:col-span-2"><Field label="What was it for?"><input className="input" value={f.description} placeholder="e.g. Field visits in Kawangware"
          onChange={(e) => setF({ ...f, description: e.target.value })} /></Field></div>
      </div>
      <table className="w-full text-sm">
        <thead><tr><th className="th">Kind of expense</th><th className="th">Details</th><th className="th">Receipt no.</th><th className="th text-right w-32">Amount</th><th className="th w-8" /></tr></thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td className="td"><select className="input" value={l.account_id} onChange={(e) => set(i, "account_id", e.target.value)}>
                <option value="">Choose…</option>{(data?.accounts || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></td>
              <td className="td"><input className="input" value={l.description} onChange={(e) => set(i, "description", e.target.value)} /></td>
              <td className="td"><input className="input" value={l.receipt_ref} onChange={(e) => set(i, "receipt_ref", e.target.value)} /></td>
              <td className="td"><input type="number" min="0" className="input text-right" value={l.amount} onChange={(e) => set(i, "amount", e.target.value)} /></td>
              <td className="td">{lines.length > 1 && <button className="text-gray-400 hover:text-red-600" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex justify-between items-center mt-2">
        <button className="text-sm text-accent font-semibold" onClick={() => setLines([...lines, { account_id: "", description: "", amount: "", receipt_ref: "" }])}>+ Another receipt</button>
        <div className="font-bold">Total {kes2(total)}</div>
      </div>
      <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={busy || !total || f.description.length < 3} onClick={save}>Submit claim</button></div>
    </Modal>
  );
}

function ClaimModal({ id, canApprove, onClose }) {
  const { can, user } = useAuth();
  const { data: c, error, reload } = useLoad(`/api/v1/books/claims/${id}`);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState(null);
  const [reason, setReason] = useState("");
  const [p, setP] = useState({ paid_on: today(), account_id: null, method: "mpesa", reference: "" });
  const act = async (path, body) => {
    setErr("");
    try { await api(`/api/v1/books/claims/${id}/${path}`, { method: "POST", body }); setMode(null); reload(); notify("Done", { tone: "success" }); }
    catch (e) { setErr(e.message); }
  };
  const pay = async () => {
    setErr("");
    try {
      const r = await api("/api/v1/books/payments", { method: "POST", body: { kind: "claim", target_id: Number(id), amount: c.outstanding,
        paid_on: p.paid_on, account_id: p.account_id, method: p.method, reference: p.reference || null } });
      notify(r.status === "pending" ? `${r.number} waits for approval` : `${r.number} paid`, { tone: "success" });
      setMode(null); reload();
    } catch (e) { setErr(e.message); }
  };
  const own = c && c.claimant_user_id === user?.id;
  return (
    <Modal title={c ? `Claim ${c.number}` : "Claim"} onClose={onClose} wide>
      <ErrorNote text={error || err} />
      {!c ? <Spinner /> : (
        <>
          <div className="grid sm:grid-cols-4 gap-3 text-sm mb-3">
            <div><div className="label">By</div>{c.claimant}</div><div><div className="label">Date</div>{fmtDate(c.claim_date)}</div>
            <div><div className="label">Status</div><Badge value={BADGE[c.status]?.[0]}>{BADGE[c.status]?.[1]}</Badge></div>
            <div><div className="label">Decided by</div>{c.approved_by || "—"}</div>
          </div>
          <div className="text-sm mb-2">{c.description}</div>
          {c.reject_reason && <div className="text-sm text-red-600 mb-2">Rejected: {c.reject_reason}</div>}
          <table className="w-full text-sm"><tbody>
            {c.lines.map((l, i) => <tr key={i}><td className="td">{l.account}</td><td className="td whitespace-normal">{l.description}</td><td className="td">{l.receipt_ref}</td>
              <td className="td text-right tabnums">{amt(l.amount)}</td></tr>)}
            <tr className="font-bold"><td className="td" colSpan={3}>Total</td><td className="td text-right tabnums">{kes2(c.total)}</td></tr>
          </tbody></table>
          <Attachments entity="claim" id={c.id} items={c.attachments} onDone={reload} canAdd={own || can("accounting.post")} />
          {mode === "reject" && (
            <div className="mt-3"><Field label="Why is it rejected?"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
              <div className="flex justify-end gap-2 mt-2"><button className="btn-ghost" onClick={() => setMode(null)}>Back</button>
                <button className="btn-primary" disabled={reason.length < 5} onClick={() => act("reject", { reason })}>Reject</button></div></div>
          )}
          {mode === "pay" && (
            <div className="mt-3 grid sm:grid-cols-4 gap-3 items-end">
              <Field label="Paid on"><input type="date" className="input" value={p.paid_on} onChange={(e) => setP({ ...p, paid_on: e.target.value })} /></Field>
              <div className="sm:col-span-2"><Field label="Paid from"><MoneyAccountSelect value={p.account_id} onChange={(v) => setP({ ...p, account_id: v })} /></Field></div>
              <Field label="Reference"><input className="input" value={p.reference} onChange={(e) => setP({ ...p, reference: e.target.value })} /></Field>
              <div className="sm:col-span-4 flex justify-end gap-2"><button className="btn-ghost" onClick={() => setMode(null)}>Back</button>
                <button className="btn-primary" disabled={!p.account_id} onClick={pay}>Pay {kes2(c.outstanding)}</button></div>
            </div>
          )}
          {!mode && (
            <div className="flex justify-end gap-2 mt-4">
              {c.status === "submitted" && canApprove && !own && <>
                <button className="btn-ghost" onClick={() => setMode("reject")}>Reject</button>
                <button className="btn-primary" onClick={() => act("approve")}>Approve</button></>}
              {c.status === "approved" && can("accounting.post") && c.outstanding > 0 && <button className="btn-primary" onClick={() => setMode("pay")}>Pay the claim</button>}
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
