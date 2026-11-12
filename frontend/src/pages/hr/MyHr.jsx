// WP15 — My HR (every member of staff): my leave and requests, my payslips and
// P9, my appraisals, and — for managers — my team's leave to decide.
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { LeaveBalances, SlipList } from "./Employees";
import { ErrorNote, Field, HrTabs, LEAVE_BADGE, Note, Tabs, today, useLoad } from "./common";

export default function MyHr() {
  const { can } = useAuth();
  const { data: d, error, loading, reload } = useLoad("/api/v1/hr/me");
  const [tab, setTab] = useState("leave");
  const [apply, setApply] = useState(null);
  const [err, setErr] = useState("");
  const [ack, setAck] = useState(null);
  if (loading && !d) return <Spinner />;
  if (!d) return <ErrorNote text={error} />;
  if (!d.linked) return (
    <div><PageHeader title="My HR" crumbs={["My HR"]} />{can("hr.view") && <HrTabs />}
      <div className="card p-8 text-center max-w-lg mx-auto"><div className="text-3xl mb-2">🪪</div>
        <div className="font-bold">Your login is not linked to an employee record yet</div>
        <div className="text-sm text-gray-500 mt-1">Ask HR to open your employee record and choose your login under "Their Finyl login".</div></div></div>
  );
  const e = d.employee;
  const send = async () => {
    setErr("");
    try { const r = await api("/api/v1/hr/leave/requests", { method: "POST", body: { ...apply, leave_type_id: Number(apply.leave_type_id) } });
      notify(`Requested ${r.days} days. Your manager will decide.`, { tone: "success" }); setApply(null); reload(); }
    catch (ex) { setErr(ex.message); }
  };
  const act = async (path, body) => {
    setErr("");
    try { await api(path, { method: "POST", body }); reload(); notify("Done", { tone: "success" }); }
    catch (ex) { setErr(ex.message); }
  };
  const tabs = [["leave", "My leave"], ["payslips", "My payslips"], ["appraisals", "My appraisals"], ...(d.team.length ? [["team", `My team (${d.team_pending.length})`]] : [])];
  return (
    <div>
      <PageHeader title="My HR" crumbs={["My HR"]} actions={<button className="btn-primary" onClick={() => setApply({ leave_type_id: d.leave[0]?.leave_type_id, start_date: today(), end_date: today(), reason: "" })}>Apply for leave</button>} />
      {can("hr.view") && <HrTabs />}
      <div className="text-sm text-gray-500 mb-3">{e.name} · {e.employee_no} · {e.job_title} · {e.branch || "Head office"}{e.manager && ` · reports to ${e.manager}`}</div>
      <ErrorNote text={err} />
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === "leave" && (
        <div className="card p-4">
          <LeaveBalances rows={d.leave} />
          <div className="font-bold mt-5 mb-2">My requests</div>
          {!d.leave_requests.length ? <Empty text="None yet" /> : (
            <table className="w-full text-sm"><tbody>{d.leave_requests.map((r) => (
              <tr key={r.id}><td className="td">{r.leave_type}</td><td className="td">{fmtDate(r.start_date)} – {fmtDate(r.end_date)}</td><td className="td">{r.days} days</td>
                <td className="td"><Badge value={LEAVE_BADGE[r.status]}>{r.status}</Badge>{r.decision_note && <span className="text-xs text-gray-500"> {r.decision_note}</span>}</td>
                <td className="td">{(r.status === "pending" || (r.status === "approved" && r.start_date > today())) &&
                  <button className="text-xs text-gray-500" onClick={() => act(`/api/v1/hr/leave/requests/${r.id}/cancel`, {})}>Cancel</button>}</td></tr>
            ))}</tbody></table>
          )}
        </div>
      )}
      {tab === "payslips" && (<>
        <SlipList rows={d.payslips} />
        <div className="mt-3"><Link className="btn-ghost inline-block" to={`/hr/p9/${e.id}`}>My P9 (yearly tax card)</Link></div>
      </>)}
      {tab === "appraisals" && (!d.appraisals.length ? <div className="card p-4"><Empty text="No appraisals shared with you yet." /></div> : (
        <div className="space-y-3">{d.appraisals.map((a) => (
          <div key={a.id} className="card p-4">
            <div className="flex items-center gap-3 mb-2"><div className="font-bold flex-1">{a.period}</div><div className="text-lg font-extrabold">{a.score} / 5</div></div>
            <table className="w-full text-sm"><tbody>{a.goals.map((g, i) => <tr key={i}><td className="py-0.5">{g.goal}{g.comment && <div className="text-xs text-gray-500">{g.comment}</div>}</td><td className="text-right">{g.score ?? "—"}</td></tr>)}</tbody></table>
            {a.strengths && <div className="text-sm mt-2"><b>Strengths:</b> {a.strengths}</div>}
            {a.improvements && <div className="text-sm"><b>To improve:</b> {a.improvements}</div>}
            {a.status === "shared" ? <button className="btn-primary mt-3" onClick={() => setAck({ id: a.id, comment: "" })}>Acknowledge</button>
              : <div className="text-xs text-emerald-700 mt-2">✓ Acknowledged{a.employee_comment && `: ${a.employee_comment}`}</div>}
          </div>
        ))}</div>
      ))}
      {tab === "team" && (
        <div className="card p-4">
          <div className="font-bold mb-2">Leave waiting for you</div>
          {!d.team_pending.length ? <Empty text="Nothing waiting." /> : (
            <table className="w-full text-sm"><tbody>{d.team_pending.map((r) => (
              <tr key={r.id}><td className="td font-semibold">{r.employee}</td><td className="td">{r.leave_type}</td><td className="td">{fmtDate(r.start_date)} – {fmtDate(r.end_date)} ({r.days} days)</td>
                <td className="td text-gray-500 whitespace-normal">{r.reason}</td>
                <td className="td whitespace-nowrap"><button className="text-xs font-semibold text-accent mr-3" onClick={() => act(`/api/v1/hr/leave/requests/${r.id}/approve`, {})}>Approve</button>
                  <button className="text-xs text-gray-500" onClick={() => { const note = window.prompt("Why not?"); if (note) act(`/api/v1/hr/leave/requests/${r.id}/reject`, { note }); }}>Reject</button></td></tr>
            ))}</tbody></table>
          )}
          <div className="text-xs text-gray-500 mt-3">Your team: {d.team.map((t) => t.name).join(", ")}</div>
        </div>
      )}
      {apply && (
        <Modal title="Apply for leave" onClose={() => setApply(null)}>
          <ErrorNote text={err} />
          <Field label="Leave"><select className="input" value={apply.leave_type_id} onChange={(ev) => setApply({ ...apply, leave_type_id: ev.target.value })}>
            {d.leave.map((b) => <option key={b.leave_type_id} value={b.leave_type_id}>{b.name}{b.unlimited ? "" : ` (${b.available} available)`}</option>)}</select></Field>
          <div className="grid grid-cols-2 gap-3 mt-3">
            <Field label="First day"><input type="date" className="input" value={apply.start_date} onChange={(ev) => setApply({ ...apply, start_date: ev.target.value })} /></Field>
            <Field label="Last day"><input type="date" className="input" value={apply.end_date} onChange={(ev) => setApply({ ...apply, end_date: ev.target.value })} /></Field>
          </div>
          <div className="mt-3"><Field label="Reason (optional)"><input className="input" value={apply.reason} onChange={(ev) => setApply({ ...apply, reason: ev.target.value })} /></Field></div>
          {d.leave.find((b) => String(b.leave_type_id) === String(apply.leave_type_id))?.needs_document && <Note>This leave needs a document (for example a sick note): give it to HR.</Note>}
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setApply(null)}>Cancel</button><button className="btn-primary" onClick={send}>Send request</button></div>
        </Modal>
      )}
      {ack && (
        <Modal title="Acknowledge appraisal" onClose={() => setAck(null)}>
          <Field label="Your comment (optional)"><textarea className="input" rows={3} value={ack.comment} onChange={(ev) => setAck({ ...ack, comment: ev.target.value })} /></Field>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setAck(null)}>Cancel</button>
            <button className="btn-primary" onClick={async () => { await act(`/api/v1/hr/appraisals/${ack.id}/acknowledge`, { comment: ack.comment || null }); setAck(null); }}>Acknowledge</button></div>
        </Modal>
      )}
    </div>
  );
}
