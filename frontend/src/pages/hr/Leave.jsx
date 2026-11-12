// WP15 — leave: requests to decide, everyone's leave, and the leave types.
import { useState } from "react";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { ErrorNote, Field, HrTabs, LEAVE_BADGE, Tabs, exportCsv, useLoad } from "./common";

export default function Leave() {
  const [tab, setTab] = useState("pending");
  return (
    <div>
      <PageHeader title="Leave" crumbs={["HR", "Leave"]} />
      <HrTabs />
      <Tabs tabs={[["pending", "Waiting for a decision"], ["all", "All leave"], ["types", "Leave types"]]} value={tab} onChange={setTab} />
      {tab === "types" ? <Types /> : <Requests status={tab === "pending" ? "pending" : ""} />}
    </div>
  );
}

export function Requests({ status }) {
  const { data, error, loading, reload } = useLoad(`/api/v1/hr/leave/requests${status ? `?status=${status}` : ""}`);
  const [err, setErr] = useState("");
  const decide = async (r, action) => {
    setErr("");
    const note = action === "reject" ? window.prompt("Why is the leave not approved?") : "";
    if (action === "reject" && !note) return;
    try { await api(`/api/v1/hr/leave/requests/${r.id}/${action}`, { method: "POST", body: { note } }); notify(`Leave ${action === "approve" ? "approved" : "rejected"}`, { tone: "success" }); reload(); }
    catch (e) { setErr(e.message); }
  };
  const rows = data?.requests || [];
  return (
    <div className="card overflow-hidden">
      <ErrorNote text={error || err} />
      {!status && rows.length > 0 && <div className="px-4 py-3 border-b border-border text-right"><button className="btn-ghost" onClick={() => exportCsv("leave.csv",
        [["Employee", "employee"], ["Leave", "leave_type"], ["From", "start_date"], ["To", "end_date"], ["Days", "days"], ["Status", "status"], ["Reason", "reason"]], rows)}>Export CSV</button></div>}
      {loading && !data ? <Spinner /> : !rows.length ? <Empty text={status ? "Nothing is waiting." : "No leave recorded yet."} /> : (
        <table className="min-w-full text-sm"><thead><tr><th className="th">Employee</th><th className="th">Leave</th><th className="th">Dates</th><th className="th text-right">Days</th>
          <th className="th">Reason</th><th className="th">Status</th><th className="th" /></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id}><td className="td font-semibold">{r.employee}</td><td className="td">{r.leave_type}</td>
              <td className="td whitespace-nowrap">{fmtDate(r.start_date)} – {fmtDate(r.end_date)}</td><td className="td text-right">{r.days}</td>
              <td className="td whitespace-normal text-gray-500">{r.reason}{r.decision_note && <div className="text-xs">Note: {r.decision_note}</div>}</td>
              <td className="td"><Badge value={LEAVE_BADGE[r.status]}>{r.status}</Badge></td>
              <td className="td whitespace-nowrap">{r.can_decide && <>
                <button className="text-xs font-semibold text-accent mr-3" onClick={() => decide(r, "approve")}>Approve</button>
                <button className="text-xs text-gray-500" onClick={() => decide(r, "reject")}>Reject</button></>}</td></tr>
          ))}</tbody></table>
      )}
    </div>
  );
}

function Types() {
  const { can } = useAuth();
  const { data, reload } = useLoad("/api/v1/hr/leave-types");
  const [edit, setEdit] = useState(null);
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      const body = { ...edit, days_per_year: Number(edit.days_per_year || 0), carry_forward_max: Number(edit.carry_forward_max || 0), gender: edit.gender || null };
      delete body.id;
      await api(edit.id ? `/api/v1/hr/leave-types/${edit.id}` : "/api/v1/hr/leave-types", { method: edit.id ? "PUT" : "POST", body });
      setEdit(null); reload();
    } catch (e) { setErr(e.message); }
  };
  return (
    <div className="card p-4">
      <table className="w-full text-sm mb-3"><thead><tr><th className="th">Leave</th><th className="th text-right">Days a year</th><th className="th">Paid</th><th className="th">Earned monthly</th>
        <th className="th text-right">Carry forward</th><th className="th">Who</th><th className="th" /></tr></thead>
        <tbody>{(data?.types || []).map((t) => (
          <tr key={t.id} className={t.active ? "" : "opacity-40"}><td className="td font-semibold">{t.name}</td><td className="td text-right">{t.days_per_year || "no limit"}{t.calendar_days ? " (calendar)" : ""}</td>
            <td className="td">{t.paid ? "Yes" : "No"}</td><td className="td">{t.accrues ? "Yes" : "No"}</td><td className="td text-right">{t.carry_forward_max || ""}</td>
            <td className="td">{t.gender || "everyone"}</td><td className="td">{can("hr.manage") && <button className="text-xs font-semibold text-accent" onClick={() => setEdit(t)}>Edit</button>}</td></tr>
        ))}</tbody></table>
      <div className="text-xs text-gray-500 mb-3">Kenya's Employment Act: at least 21 working days of annual leave a year, 7 days sick leave on full pay then 7 on half pay, 3 months maternity and 2 weeks paternity leave.</div>
      {can("hr.manage") && !edit && <button className="btn-ghost" onClick={() => setEdit({ name: "", days_per_year: 0, paid: true, accrues: false, carry_forward_max: 0, gender: "", needs_document: false, calendar_days: false, active: true })}>New leave type</button>}
      {edit && (
        <Modal title={edit.id ? `Edit ${edit.name}` : "New leave type"} onClose={() => setEdit(null)}>
          <ErrorNote text={err} />
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2"><Field label="Name"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field></div>
            <Field label="Days a year" hint="0 with 'not paid' = no limit"><input type="number" className="input" value={edit.days_per_year} onChange={(e) => setEdit({ ...edit, days_per_year: e.target.value })} /></Field>
            <Field label="Days that can be carried to next year"><input type="number" className="input" value={edit.carry_forward_max} onChange={(e) => setEdit({ ...edit, carry_forward_max: e.target.value })} /></Field>
            <Field label="For"><select className="input" value={edit.gender || ""} onChange={(e) => setEdit({ ...edit, gender: e.target.value })}><option value="">Everyone</option><option value="female">Women</option><option value="male">Men</option></select></Field>
            <div />
            {[["paid", "Paid"], ["accrues", "Earned month by month"], ["calendar_days", "Count every calendar day (not just working days)"], ["needs_document", "Needs a document (e.g. sick note)"], ["active", "In use"]].map(([k, l]) => (
              <label key={k} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!edit[k]} onChange={(e) => setEdit({ ...edit, [k]: e.target.checked })} /> {l}</label>
            ))}
          </div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setEdit(null)}>Cancel</button><button className="btn-primary" onClick={save}>Save</button></div>
        </Modal>
      )}
    </div>
  );
}
