// WP16 — group lending: groups (chamas), their members and roles, meetings and
// attendance, and the group's loans and arrears.
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Empty, KpiCard, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { ErrorNote, Field, amt, kes, today, useLoad } from "../books/common";
import { ClientPicker } from "./Collateral";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export default function Groups() {
  const { can } = useAuth();
  const nav = useNavigate();
  const { data, error, loading, reload } = useLoad("/api/v1/lending-plus/groups");
  const [form, setForm] = useState(null);
  const rows = data?.groups || [];
  return (
    <div>
      <PageHeader title="Groups" crumbs={["Lending", "Groups"]}
        actions={can("groups.manage") && <button className="btn-primary" onClick={() => setForm({ name: "", branch_id: "", officer_staff_id: "", registration_no: "", meeting_day: "Tuesday", meeting_frequency: "weekly", meeting_place: "", formed_on: "", status: "active" })}>New group</button>} />
      <div className="card overflow-hidden">
        <ErrorNote text={error} />
        {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No groups yet." /> : (
          <table className="min-w-full text-sm"><thead><tr><th className="th">Group</th><th className="th">Branch</th><th className="th">Officer</th><th className="th">Meets</th>
            <th className="th text-right">Members</th><th className="th text-right">Owed</th><th className="th text-right">In arrears</th><th className="th">Status</th></tr></thead>
            <tbody>{rows.map((g) => (
              <tr key={g.id} className="hover:bg-canvas cursor-pointer" onClick={() => nav(`/groups/${g.id}`)}>
                <td className="td font-semibold">{g.name}<div className="text-xs text-gray-400 font-mono">{g.code}</div></td><td className="td">{g.branch || "—"}</td><td className="td">{g.officer || "—"}</td>
                <td className="td">{g.meeting_day ? `${g.meeting_day}, ${g.meeting_frequency}` : "—"}</td><td className="td text-right">{g.members}</td>
                <td className="td text-right tabnums">{amt(g.outstanding)}</td><td className={`td text-right tabnums ${g.in_arrears ? "text-red-600 font-semibold" : ""}`}>{amt(g.in_arrears)}{g.par_pct ? ` (${g.par_pct}%)` : ""}</td>
                <td className="td"><Badge value={g.status === "active" ? "active" : "closed"}>{g.status}</Badge></td></tr>
            ))}</tbody></table>
        )}
      </div>
      {form && <GroupForm g={form} meta={data} onClose={() => setForm(null)} onDone={(id) => { setForm(null); reload(); if (id) nav(`/groups/${id}`); }} />}
    </div>
  );
}

function GroupForm({ g, meta, onClose, onDone }) {
  const [f, setF] = useState(g);
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      const body = { ...f, branch_id: f.branch_id ? Number(f.branch_id) : null, officer_staff_id: f.officer_staff_id ? Number(f.officer_staff_id) : null, formed_on: f.formed_on || null };
      ["id", "code", "branch", "officer", "members", "open_loans", "outstanding", "in_arrears", "par_pct", "late_members", "members_list", "meetings"].forEach((k) => delete body[k]);
      const r = await api(g.id ? `/api/v1/lending-plus/groups/${g.id}` : "/api/v1/lending-plus/groups", { method: g.id ? "PUT" : "POST", body });
      notify("Group saved", { tone: "success" }); onDone(g.id ? null : r.id);
    } catch (e) { setErr(e.message); }
  };
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal title={g.id ? `Edit ${g.name}` : "New group"} onClose={onClose}>
      <ErrorNote text={err} />
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2"><Field label="Name"><input className="input" value={f.name} onChange={set("name")} /></Field></div>
        <Field label="Branch"><select className="input" value={f.branch_id || ""} onChange={set("branch_id")}><option value="">—</option>{(meta?.branches || []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>
        <Field label="Loan officer"><select className="input" value={f.officer_staff_id || ""} onChange={set("officer_staff_id")}><option value="">—</option>{(meta?.officers || []).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select></Field>
        <Field label="Meeting day"><select className="input" value={f.meeting_day || ""} onChange={set("meeting_day")}><option value="">—</option>{DAYS.map((d) => <option key={d}>{d}</option>)}</select></Field>
        <Field label="How often"><select className="input" value={f.meeting_frequency} onChange={set("meeting_frequency")}><option value="weekly">Weekly</option><option value="fortnightly">Every two weeks</option><option value="monthly">Monthly</option></select></Field>
        <Field label="Meeting place"><input className="input" value={f.meeting_place || ""} onChange={set("meeting_place")} /></Field>
        <Field label="Registration no." hint="Social services / self-help group certificate"><input className="input" value={f.registration_no || ""} onChange={set("registration_no")} /></Field>
        <Field label="Formed on"><input type="date" className="input" value={f.formed_on || ""} onChange={set("formed_on")} /></Field>
        <Field label="Status"><select className="input" value={f.status} onChange={set("status")}><option value="active">Active</option><option value="dormant">Dormant</option><option value="closed">Closed</option></select></Field>
      </div>
      <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={f.name.length < 3} onClick={save}>Save</button></div>
    </Modal>
  );
}

export function GroupDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const { data: g, error, reload } = useLoad(`/api/v1/lending-plus/groups/${id}`);
  const meta = useLoad("/api/v1/lending-plus/groups");
  const [edit, setEdit] = useState(false);
  const [add, setAdd] = useState(null);
  const [meet, setMeet] = useState(null);
  const [err, setErr] = useState("");
  if (!g) return <div><ErrorNote text={error} />{!error && <Spinner />}</div>;
  const active = g.members_list.filter((m) => m.active);
  const call = async (path, body, ok) => {
    setErr("");
    try { await api(path, { method: "POST", body }); notify(ok, { tone: "success" }); reload(); return true; }
    catch (e) { setErr(e.message); return false; }
  };
  return (
    <div>
      <PageHeader title={g.name} crumbs={["Lending", "Groups", g.code]}
        actions={can("groups.manage") && <>
          <button className="btn-ghost" onClick={() => setEdit(true)}>Edit</button>
          <button className="btn-ghost" onClick={() => setMeet({ held_on: today(), present: active.map((m) => m.borrower_id), collected: "", notes: "" })}>Record a meeting</button>
          <button className="btn-primary" onClick={() => setAdd({ client: null, role: "member" })}>Add a member</button></>} />
      <ErrorNote text={err} />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <KpiCard label="Members" value={g.members} sub={g.meeting_day ? `Meets ${g.meeting_day}, ${g.meeting_frequency}` : ""} />
        <KpiCard label="Owed by members" value={kes(g.outstanding)} sub={`${g.open_loans} open loans`} />
        <KpiCard label="In arrears" value={kes(g.in_arrears)} sub={`${g.late_members} members late`} tone={g.in_arrears ? "danger" : "default"} />
        <KpiCard label="Last meeting" value={g.meetings[0] ? `${g.meetings[0].attendance_pct}%` : "—"} sub={g.meetings[0] ? `attended ${fmtDate(g.meetings[0].held_on)}` : "none recorded"} />
      </div>
      <div className="card overflow-hidden mb-4">
        <table className="min-w-full text-sm"><thead><tr><th className="th">Member</th><th className="th">Role</th><th className="th">Joined</th><th className="th">Loans</th><th className="th text-right">Owed</th><th className="th" /></tr></thead>
          <tbody>{g.members_list.map((m) => (
            <tr key={m.id} className={m.active ? "" : "opacity-40"}><td className="td font-semibold">{m.name}<div className="text-xs text-gray-400">{m.phone}</div></td>
              <td className="td">{m.role}</td><td className="td">{fmtDate(m.joined_on)}{m.left_on && ` – ${fmtDate(m.left_on)}`}</td>
              <td className="td">{m.loans.map((l) => <div key={l.id}><Link className="text-accent font-mono text-xs" to={`/loans/${l.id}`}>{l.account_number}</Link> {l.status}{!l.in_group && can("groups.manage") && m.active &&
                <button className="text-xs text-accent ml-2" onClick={() => call(`/api/v1/lending-plus/loans/${l.id}/group`, { group_id: g.id }, "Loan now counts as a group loan")}>make it a group loan</button>}</div>)}</td>
              <td className={`td text-right tabnums ${m.late ? "text-red-600 font-semibold" : ""}`}>{amt(m.outstanding)}</td>
              <td className="td">{m.active && can("groups.manage") && <button className="text-xs text-gray-500" onClick={() => call(`/api/v1/lending-plus/groups/${g.id}/members/${m.id}/leave`, {}, "Member has left")}>Leaves the group</button>}</td></tr>
          ))}</tbody></table>
        {!g.members_list.length && <Empty text="No members yet." />}
      </div>
      <div className="card p-4">
        <div className="font-bold mb-2">Meetings</div>
        {!g.meetings.length ? <Empty text="None recorded." /> : (
          <table className="w-full text-sm"><tbody>{g.meetings.map((mt) => <tr key={mt.id}><td className="td">{fmtDate(mt.held_on)}</td><td className="td">{mt.present.length} present ({mt.attendance_pct}%)</td>
            <td className="td text-right tabnums">{mt.collected ? `KES ${amt(mt.collected)} collected` : ""}</td><td className="td text-gray-500 whitespace-normal">{mt.notes}</td></tr>)}</tbody></table>
        )}
      </div>
      {edit && <GroupForm g={g} meta={meta.data} onClose={() => setEdit(false)} onDone={() => { setEdit(false); reload(); }} />}
      {add && (
        <Modal title="Add a member" onClose={() => setAdd(null)}>
          <ErrorNote text={err} />
          <Field label="Client"><ClientPicker value={add.client} onPick={(c) => setAdd({ ...add, client: c })} /></Field>
          <div className="mt-3"><Field label="Role"><select className="input" value={add.role} onChange={(e) => setAdd({ ...add, role: e.target.value })}>
            <option value="member">Member</option><option value="chair">Chair</option><option value="secretary">Secretary</option><option value="treasurer">Treasurer</option></select></Field></div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setAdd(null)}>Cancel</button>
            <button className="btn-primary" disabled={!add.client} onClick={async () => { if (await call(`/api/v1/lending-plus/groups/${g.id}/members`, { borrower_id: add.client.id, role: add.role }, "Member added")) setAdd(null); }}>Add</button></div>
        </Modal>
      )}
      {meet && (
        <Modal title="Record a meeting" onClose={() => setMeet(null)}>
          <ErrorNote text={err} />
          <div className="grid grid-cols-2 gap-3 mb-3">
            <Field label="Date"><input type="date" className="input" max={today()} value={meet.held_on} onChange={(e) => setMeet({ ...meet, held_on: e.target.value })} /></Field>
            <Field label="Collected at the meeting (KES)"><input type="number" className="input" value={meet.collected} onChange={(e) => setMeet({ ...meet, collected: e.target.value })} /></Field>
          </div>
          <div className="label">Present</div>
          <div className="grid grid-cols-2 gap-1 mb-3">{active.map((m) => (
            <label key={m.borrower_id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={meet.present.includes(m.borrower_id)}
              onChange={(e) => setMeet({ ...meet, present: e.target.checked ? [...meet.present, m.borrower_id] : meet.present.filter((x) => x !== m.borrower_id) })} /> {m.name}</label>
          ))}</div>
          <Field label="Notes"><textarea className="input" rows={2} value={meet.notes} onChange={(e) => setMeet({ ...meet, notes: e.target.value })} /></Field>
          <div className="text-xs text-gray-400 mt-2">Money collected is recorded here for the group's records; post the payments to each loan as usual.</div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setMeet(null)}>Cancel</button>
            <button className="btn-primary" onClick={async () => { if (await call(`/api/v1/lending-plus/groups/${g.id}/meetings`, { ...meet, collected: Number(meet.collected || 0) }, "Meeting recorded")) setMeet(null); }}>Save</button></div>
        </Modal>
      )}
    </div>
  );
}
