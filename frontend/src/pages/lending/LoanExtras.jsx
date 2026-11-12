// WP16 — on a loan's page: what is owed by part, write-off / waiver / reschedule
// requests (a second person approves), top-up, collateral and group.
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Modal, Spinner } from "../../components/ui";
import { api, fmtDate, notify } from "../../lib/api";
import { ErrorNote, Field, Note, amt, kes2, today, useLoad } from "../books/common";

export const ACTION_LABEL = { writeoff: "Write-off", waiver: "Waiver", reschedule: "Reschedule" };
export const ACTION_BADGE = { pending: "pending", done: "success", rejected: "rejected", cancelled: "closed" };

export function actionSummary(a) {
  const d = a.details || {};
  if (a.kind === "writeoff") return `Write off KES ${amt(d.amount)}`;
  if (a.kind === "waiver") return `Waive KES ${amt(d.amount)} of ${d.component}`;
  if (a.kind === "reschedule") return `Due ${fmtDate(d.old_due_date)} → ${fmtDate(d.new_due_date)}${Number(d.fee) ? `, fee KES ${amt(d.fee)}` : ""}`;
  return a.kind;
}

export default function LoanExtras({ loanId, onChange }) {
  const nav = useNavigate();
  const { data: x, error, reload } = useLoad(`/api/v1/lending-plus/loans/${loanId}`);
  const [form, setForm] = useState(null);
  const [topup, setTopup] = useState(null);
  const [err, setErr] = useState("");
  if (!x) return error ? null : <Spinner />;
  const refresh = () => { reload(); onChange && onChange(); };
  const ask = async () => {
    setErr("");
    try {
      const body = { kind: form.kind, reason: form.reason };
      if (form.kind === "waiver") Object.assign(body, { component: form.component, amount: Number(form.amount) });
      if (form.kind === "reschedule") Object.assign(body, { new_due_date: form.new_due_date, fee: Number(form.fee || 0) });
      await api(`/api/v1/lending-plus/loans/${loanId}/actions`, { method: "POST", body });
      notify("Sent for approval: someone else with the right must approve it", { tone: "success" }); setForm(null); refresh();
    } catch (e) { setErr(e.message); }
  };
  const doTopup = async () => {
    setErr("");
    try {
      const r = await api(`/api/v1/lending-plus/loans/${loanId}/topup`, { method: "POST", body: { principal: Number(topup.principal) } });
      notify(`Top-up ${r.account_number} created. It goes through approval and payout like any loan.`, { tone: "success" });
      nav(`/loans/${r.id}`);
    } catch (e) { setErr(e.message); }
  };
  const live = ["active", "overdue", "defaulted", "written_off"].includes(x.status);
  return (
    <div className="card p-5 mt-5">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="font-bold flex-1">Loan management</div>
        {live && x.can.waiver && x.status !== "written_off" && <button className="btn-ghost" onClick={() => setForm({ kind: "waiver", component: "fees", amount: "", reason: "" })}>Waive interest or fees</button>}
        {live && x.can.reschedule && x.status !== "written_off" && <button className="btn-ghost" onClick={() => setForm({ kind: "reschedule", new_due_date: "", fee: "0", reason: "" })}>Reschedule</button>}
        {live && x.can.writeoff && x.status !== "written_off" && <button className="btn-ghost !text-red-600" onClick={() => setForm({ kind: "writeoff", reason: "" })}>Write off</button>}
        {x.status === "active" && x.can.topup && <button className="btn-primary" onClick={() => setTopup({ principal: "" })}>Top up</button>}
      </div>
      <ErrorNote text={err} />
      <div className="grid sm:grid-cols-4 gap-3 text-sm mb-4">
        <div><div className="label">Principal owed</div>{kes2(x.owed.principal)}</div>
        <div><div className="label">Interest owed</div>{kes2(x.owed.interest)}{x.interest_waived > 0 && <div className="text-xs text-gray-400">{kes2(x.interest_waived)} waived</div>}</div>
        <div><div className="label">Fees and penalties owed</div>{kes2(x.owed.fees)}{x.fees_waived > 0 && <div className="text-xs text-gray-400">{kes2(x.fees_waived)} waived</div>}</div>
        <div><div className="label">Total owed</div><b>{kes2(x.owed.total)}</b></div>
        {x.written_off_at && <div className="sm:col-span-2 text-red-700"><b>Written off</b> {fmtDate(x.written_off_at)} ({kes2(x.written_off_amount)}). Payments now count as recoveries.</div>}
        {x.restructure_count > 0 && <div><div className="label">Rescheduled</div>{x.restructure_count} time(s)</div>}
        {x.topup_of_loan_id && <div><div className="label">Top-up of</div><Link className="text-accent" to={`/loans/${x.topup_of_loan_id}`}>loan #{x.topup_of_loan_id}</Link></div>}
        {x.topups.map((t) => <div key={t.id}><div className="label">Topped up by</div><Link className="text-accent" to={`/loans/${t.id}`}>{t.account_number}</Link> ({t.status})</div>)}
        {x.group && <div><div className="label">Group</div><Link className="text-accent" to={`/groups/${x.group.id}`}>{x.group.name}</Link></div>}
      </div>
      <div className="grid lg:grid-cols-2 gap-4">
        <div>
          <div className="label">Requests</div>
          {!x.actions.length ? <div className="text-sm text-gray-400">None</div> : (
            <table className="w-full text-sm"><tbody>{x.actions.map((a) => (
              <tr key={a.id}><td className="td">{ACTION_LABEL[a.kind]}</td><td className="td whitespace-normal">{actionSummary(a)}<div className="text-xs text-gray-400">{a.reason}</div></td>
                <td className="td"><Badge value={ACTION_BADGE[a.status]}>{a.status}</Badge><div className="text-xs text-gray-400">{a.requested_by}{a.decided_by && ` → ${a.decided_by}`}</div></td></tr>
            ))}</tbody></table>
          )}
          {x.actions.some((a) => a.status === "pending") && <Link className="text-xs font-semibold text-accent" to="/loan-requests">Open the requests inbox</Link>}
        </div>
        <div>
          <div className="label">Collateral {x.collateral_cover_pct != null && `· forced-sale cover ${x.collateral_cover_pct}%`}</div>
          {!x.collateral.length ? <div className="text-sm text-gray-400">None recorded</div> : (
            <table className="w-full text-sm"><tbody>{x.collateral.map((c) => (
              <tr key={c.id}><td className="td">{c.description}<div className="text-xs text-gray-400">{c.identifier}</div></td><td className="td text-right tabnums">{amt(c.estimated_value)}</td><td className="td">{c.status}</td></tr>
            ))}</tbody></table>
          )}
          {x.can.collateral && <Link className="text-xs font-semibold text-accent" to={`/collateral?loan=${loanId}`}>Add or manage collateral</Link>}
        </div>
      </div>
      {form && (
        <Modal title={{ writeoff: "Ask to write off this loan", waiver: "Ask for a waiver", reschedule: "Ask to reschedule" }[form.kind]} onClose={() => setForm(null)}>
          <ErrorNote text={err} />
          {form.kind === "writeoff" && <Note>The loan leaves the loan book (an expense in the books). The client still owes {kes2(x.owed.total)}: anything paid later is a recovery. A second person approves.</Note>}
          {form.kind === "waiver" && (
            <div className="grid grid-cols-2 gap-3 mb-3">
              <Field label="Waive"><select className="input" value={form.component} onChange={(e) => setForm({ ...form, component: e.target.value })}>
                <option value="fees">Fees and penalties ({kes2(x.owed.fees)} owed)</option><option value="interest">Interest ({kes2(x.owed.interest)} owed)</option></select></Field>
              <Field label="Amount"><input type="number" className="input" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field>
            </div>
          )}
          {form.kind === "reschedule" && (
            <div className="grid grid-cols-2 gap-3 mb-3">
              <Field label="New due date"><input type="date" className="input" min={today()} value={form.new_due_date} onChange={(e) => setForm({ ...form, new_due_date: e.target.value })} /></Field>
              <Field label="Rescheduling fee (added to the balance)"><input type="number" className="input" value={form.fee} onChange={(e) => setForm({ ...form, fee: e.target.value })} /></Field>
            </div>
          )}
          <Field label="Reason (10 characters or more)"><textarea className="input" rows={3} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></Field>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn-primary" disabled={form.reason.length < 10} onClick={ask}>Send for approval</button></div>
        </Modal>
      )}
      {topup && (
        <Modal title="Top up this loan" onClose={() => setTopup(null)}>
          <ErrorNote text={err} />
          <Note>A new loan is created for the client. When it is paid out, {kes2(x.owed.total)} (or the early-settlement amount on that day) is kept back to clear this loan, and the rest goes to the client's M-Pesa. At least half of this loan must have been repaid.</Note>
          <Field label="New loan amount (KES)"><input type="number" className="input" value={topup.principal} onChange={(e) => setTopup({ principal: e.target.value })} /></Field>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setTopup(null)}>Cancel</button>
            <button className="btn-primary" disabled={!Number(topup.principal)} onClick={doTopup}>Create the top-up</button></div>
        </Modal>
      )}
    </div>
  );
}
