// WP15 — employees: the list, adding someone, and one person's full record
// (details, pay, leave, documents, appraisals, payslips).
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, blobUrl, fmtDate, notify, upload } from "../../lib/api";
import { AccountSelect } from "../books/common";
import { ErrorNote, Field, HrTabs, LEAVE_BADGE, Note, TYPE, Tabs, amt, exportCsv, kes, kes2, today, useLoad } from "./common";

export default function Employees() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [status, setStatus] = useState("current");
  const [q, setQ] = useState("");
  const { data, error, loading } = useLoad(`/api/v1/hr/employees?status=${status}${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  const [adding, setAdding] = useState(false);
  const rows = data?.employees || [];
  return (
    <div>
      <PageHeader title="Employees" crumbs={["HR", "Employees"]}
        actions={can("hr.manage") && <button className="btn-primary" onClick={() => setAdding(true)}>Add an employee</button>} />
      <HrTabs />
      <div className="card overflow-hidden">
        <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-border">
          <select className="input !w-auto text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="current">Current staff</option><option value="suspended">Suspended</option><option value="terminated">Have left</option><option value="">Everyone</option>
          </select>
          <input className="input !w-64 text-sm" placeholder="Search name, number, job" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="flex-1" />
          <button className="btn-ghost" disabled={!rows.length} onClick={() => exportCsv("employees.csv", [["Number", "employee_no"], ["Name", "name"],
            ["Job", "job_title"], ["Department", "department"], ["Branch", "branch"], ["Type", "employment_type"], ["Hired", "hire_date"],
            ["Basic salary", "basic_salary"], ["Status", "status"], ["Manager", "manager"]], rows)}>Export CSV</button>
        </div>
        <ErrorNote text={error} />
        {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No employees here yet." /> : (
          <table className="min-w-full text-sm">
            <thead><tr><th className="th">Number</th><th className="th">Name</th><th className="th">Job</th><th className="th">Branch</th>
              <th className="th">Type</th><th className="th">Hired</th><th className="th text-right">Basic pay</th><th className="th">Status</th></tr></thead>
            <tbody>{rows.map((e) => (
              <tr key={e.id} className="hover:bg-canvas cursor-pointer" onClick={() => nav(`/hr/employees/${e.id}`)}>
                <td className="td font-mono text-xs">{e.employee_no}</td><td className="td font-semibold">{e.name}</td>
                <td className="td">{e.job_title}<div className="text-xs text-gray-400">{e.department}</div></td><td className="td">{e.branch || "Head office"}</td>
                <td className="td">{TYPE[e.employment_type]}</td><td className="td">{fmtDate(e.hire_date)}</td>
                <td className="td text-right tabnums">{amt(e.basic_salary)}</td>
                <td className="td"><Badge value={e.status === "active" ? "active" : e.status === "terminated" ? "closed" : "overdue"}>{e.status === "terminated" ? "left" : e.status}</Badge></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
      {adding && <EmployeeForm onClose={() => setAdding(false)} onDone={(id) => nav(`/hr/employees/${id}`)} />}
    </div>
  );
}

const BLANK = { first_name: "", last_name: "", other_names: "", national_id: "", kra_pin: "", nssf_no: "", shif_no: "", phone: "", email: "",
  date_of_birth: "", gender: "", branch_id: "", department: "", job_title: "", employment_type: "permanent", hire_date: today(),
  probation_end: "", contract_end: "", basic_salary: "", pay_method: "bank", bank_name: "", bank_branch: "", bank_account: "", mpesa_phone: "",
  manager_employee_id: "", next_of_kin_name: "", next_of_kin_phone: "", disability_exempt: false, user_id: "", staff_id: "" };

export function EmployeeForm({ e, onClose, onDone }) {
  const { data: opts } = useLoad("/api/v1/hr/link-options");
  const [f, setF] = useState(() => {
    const base = { ...BLANK };
    if (e) Object.keys(base).forEach((k) => { if (e[k] != null) base[k] = e[k]; });
    return base;
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (ev) => setF({ ...f, [k]: ev.target.type === "checkbox" ? ev.target.checked : ev.target.value });
  const save = async () => {
    setErr(""); setBusy(true);
    try {
      const body = { ...f, basic_salary: Number(f.basic_salary || 0) };
      ["branch_id", "manager_employee_id", "user_id", "staff_id"].forEach((k) => { body[k] = body[k] ? Number(body[k]) : null; });
      Object.keys(body).forEach((k) => { if (body[k] === "") body[k] = null; });
      body.hire_date = f.hire_date;
      const r = await api(e ? `/api/v1/hr/employees/${e.id}` : "/api/v1/hr/employees", { method: e ? "PUT" : "POST", body });
      notify(e ? "Saved" : `${r.employee_no} added`, { tone: "success" });
      onDone(e ? e.id : r.id);
    } catch (ex) { setErr(ex.message); } finally { setBusy(false); }
  };
  const S = ({ label, k, type = "text", hint }) => <Field label={label} hint={hint}><input type={type} className="input" value={f[k] || ""} onChange={set(k)} /></Field>;
  return (
    <Modal title={e ? `Edit ${e.name}` : "Add an employee"} onClose={onClose} wide>
      <ErrorNote text={err} />
      <div className="font-bold text-sm mb-2">Who they are</div>
      <div className="grid sm:grid-cols-3 gap-3 mb-4">
        {S({ label: "First name", k: "first_name" })}{S({ label: "Last name", k: "last_name" })}{S({ label: "Other names", k: "other_names" })}
        {S({ label: "National ID number", k: "national_id" })}{S({ label: "KRA PIN", k: "kra_pin", hint: "e.g. A123456789B" })}
        {S({ label: "Date of birth", k: "date_of_birth", type: "date" })}
        <Field label="Gender"><select className="input" value={f.gender || ""} onChange={set("gender")}><option value="">—</option><option value="female">Female</option><option value="male">Male</option><option value="other">Other</option></select></Field>
        {S({ label: "NSSF number", k: "nssf_no" })}{S({ label: "SHIF / SHA number", k: "shif_no" })}
        {S({ label: "Phone", k: "phone" })}{S({ label: "Email", k: "email", type: "email" })}
        {S({ label: "Next of kin", k: "next_of_kin_name" })}{S({ label: "Next of kin phone", k: "next_of_kin_phone" })}
      </div>
      <div className="font-bold text-sm mb-2">The job</div>
      <div className="grid sm:grid-cols-3 gap-3 mb-4">
        {S({ label: "Job title", k: "job_title" })}{S({ label: "Department", k: "department" })}
        <Field label="Branch"><select className="input" value={f.branch_id || ""} onChange={set("branch_id")}><option value="">Head office</option>
          {(opts?.branches || []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>
        <Field label="Type"><select className="input" value={f.employment_type} onChange={set("employment_type")}>{Object.entries(TYPE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        {S({ label: "Hired on", k: "hire_date", type: "date" })}{S({ label: "Probation ends", k: "probation_end", type: "date" })}
        {S({ label: "Contract ends", k: "contract_end", type: "date", hint: "Empty for permanent staff" })}
        <Field label="Reports to"><select className="input" value={f.manager_employee_id || ""} onChange={set("manager_employee_id")}><option value="">—</option>
          {(opts?.employees || []).filter((x) => x.id !== e?.id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        <Field label="Their Finyl login" hint="Lets them see payslips and apply for leave"><select className="input" value={f.user_id || ""} onChange={set("user_id")}><option value="">Not linked</option>
          {(opts?.users || []).filter((u) => !u.linked || u.id === e?.user_id).map((u) => <option key={u.id} value={u.id}>{u.name} ({u.email})</option>)}</select></Field>
        <Field label="Field staff record" hint="Keeps the staff margin figures in step"><select className="input" value={f.staff_id || ""} onChange={set("staff_id")}><option value="">Not linked</option>
          {(opts?.staff || []).filter((s) => !s.linked || s.id === e?.staff_id).map((s) => <option key={s.id} value={s.id}>{s.name} ({s.role})</option>)}</select></Field>
      </div>
      <div className="font-bold text-sm mb-2">Pay</div>
      <div className="grid sm:grid-cols-3 gap-3">
        {S({ label: "Basic salary (KES a month)", k: "basic_salary", type: "number" })}
        <Field label="Paid by"><select className="input" value={f.pay_method} onChange={set("pay_method")}><option value="bank">Bank transfer</option><option value="mpesa">M-Pesa</option><option value="cash">Cash</option></select></Field>
        {f.pay_method === "mpesa" ? S({ label: "M-Pesa number", k: "mpesa_phone" }) : f.pay_method === "bank" ? (
          <>{S({ label: "Bank", k: "bank_name" })}{S({ label: "Bank branch", k: "bank_branch" })}{S({ label: "Account number", k: "bank_account" })}</>) : <div />}
        <label className="flex items-center gap-2 text-sm sm:col-span-3"><input type="checkbox" checked={!!f.disability_exempt} onChange={set("disability_exempt")} />
          Has a KRA disability exemption certificate (first KES 150,000 a month tax-free)</label>
      </div>
      <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={busy || !f.first_name || !f.last_name || !f.hire_date} onClick={save}>{busy ? "Saving…" : "Save"}</button></div>
    </Modal>
  );
}

export function EmployeeProfile() {
  const { id } = useParams();
  const { can } = useAuth();
  const { data: e, error, reload } = useLoad(`/api/v1/hr/employees/${id}`);
  const [tab, setTab] = useState("details");
  const [editing, setEditing] = useState(false);
  const [leaving, setLeaving] = useState(null);
  const [err, setErr] = useState("");
  if (!e) return <div><HrTabs /><ErrorNote text={error} />{!error && <Spinner />}</div>;
  const terminate = async () => {
    setErr("");
    try { await api(`/api/v1/hr/employees/${id}/terminate`, { method: "POST", body: leaving }); setLeaving(null); reload(); }
    catch (ex) { setErr(ex.message); }
  };
  const setStatus = async (status) => {
    try { await api(`/api/v1/hr/employees/${id}/status`, { method: "POST", body: { status } }); reload(); } catch (ex) { setErr(ex.message); }
  };
  return (
    <div>
      <PageHeader title={e.name} crumbs={["HR", "Employees", e.employee_no]}
        actions={can("hr.manage") && e.status !== "terminated" && <>
          <button className="btn-ghost" onClick={() => setStatus(e.status === "suspended" ? "active" : "suspended")}>{e.status === "suspended" ? "Lift suspension" : "Suspend"}</button>
          <button className="btn-ghost" onClick={() => setLeaving({ date: today(), reason: "" })}>Record leaving</button>
          <button className="btn-primary" onClick={() => setEditing(true)}>Edit</button></>} />
      <HrTabs />
      <ErrorNote text={err} />
      <div className="text-sm text-gray-500 mb-3">{e.job_title} · {e.department || "—"} · {e.branch || "Head office"} · {TYPE[e.employment_type]} · hired {fmtDate(e.hire_date)}
        {e.status !== "active" && <> · <b className="text-amber-700">{e.status === "terminated" ? `left ${fmtDate(e.termination_date)}` : e.status}</b></>}</div>
      <Tabs tabs={[["details", "Details"], ["pay", "Pay"], ["leave", "Leave"], ["documents", "Documents"], ["appraisals", "Appraisals"], ["payslips", "Payslips"]]} value={tab} onChange={setTab} />
      {tab === "details" && <Details e={e} />}
      {tab === "pay" && <Pay e={e} reload={reload} />}
      {tab === "leave" && <LeaveTab e={e} reload={reload} />}
      {tab === "documents" && <Docs e={e} reload={reload} />}
      {tab === "appraisals" && <AppraisalList rows={e.appraisals} />}
      {tab === "payslips" && <SlipList rows={e.payslips} employeeId={e.id} />}
      {editing && <EmployeeForm e={e} onClose={() => setEditing(false)} onDone={() => { setEditing(false); reload(); }} />}
      {leaving && (
        <Modal title={`${e.name} is leaving`} onClose={() => setLeaving(null)}>
          <Note>They are paid up to their last day in that month's payroll, then dropped from later payrolls. Pending leave is cancelled.</Note>
          <Field label="Last working day"><input type="date" className="input" value={leaving.date} onChange={(ev) => setLeaving({ ...leaving, date: ev.target.value })} /></Field>
          <div className="mt-3"><Field label="Reason"><input className="input" value={leaving.reason} placeholder="Resigned / end of contract / dismissed / retired"
            onChange={(ev) => setLeaving({ ...leaving, reason: ev.target.value })} /></Field></div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setLeaving(null)}>Cancel</button>
            <button className="btn-primary" disabled={leaving.reason.length < 5} onClick={terminate}>Record leaving</button></div>
        </Modal>
      )}
    </div>
  );
}

function Row({ label, value }) {
  return <div><div className="label">{label}</div><div className="text-sm">{value || "—"}</div></div>;
}

function Details({ e }) {
  return (
    <div className="card p-5 grid sm:grid-cols-4 gap-4">
      <Row label="Employee number" value={e.employee_no} /><Row label="National ID" value={e.national_id} /><Row label="KRA PIN" value={e.kra_pin} />
      <Row label="Date of birth" value={e.date_of_birth && fmtDate(e.date_of_birth)} /><Row label="Gender" value={e.gender} />
      <Row label="NSSF number" value={e.nssf_no} /><Row label="SHIF number" value={e.shif_no} /><Row label="Phone" value={e.phone} />
      <Row label="Email" value={e.email} /><Row label="Reports to" value={e.manager} /><Row label="Probation ends" value={e.probation_end && fmtDate(e.probation_end)} />
      <Row label="Contract ends" value={e.contract_end && fmtDate(e.contract_end)} /><Row label="Paid by" value={e.pay_method === "mpesa" ? `M-Pesa ${e.mpesa_phone || ""}` : e.pay_method === "bank" ? `${e.bank_name || ""} ${e.bank_branch || ""} ${e.bank_account || ""}` : "Cash"} />
      <Row label="Next of kin" value={[e.next_of_kin_name, e.next_of_kin_phone].filter(Boolean).join(" · ")} />
      <Row label="Linked login" value={e.user_id ? "Yes — can use My HR" : "No"} />
      <Row label="Team" value={(e.team || []).map((t) => t.name).join(", ")} />
      {e.termination_reason && <div className="sm:col-span-4"><Row label="Reason for leaving" value={e.termination_reason} /></div>}
    </div>
  );
}

function Pay({ e, reload }) {
  const { can } = useAuth();
  const [item, setItem] = useState(null);
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      const body = { ...item, amount: Number(item.amount), account_id: item.account_id || null, start_month: item.start_month || null, end_month: item.end_month || null };
      delete body.id;
      await api(item.id ? `/api/v1/hr/pay-items/${item.id}` : `/api/v1/hr/employees/${e.id}/pay-items`, { method: item.id ? "PUT" : "POST", body });
      setItem(null); reload();
    } catch (ex) { setErr(ex.message); }
  };
  const rows = e.pay_items || [];
  return (
    <div className="card p-4">
      <div className="flex items-center mb-3"><div className="font-bold flex-1">Basic salary {kes2(e.basic_salary)} a month</div>
        {can("hr.manage") && <button className="btn-ghost" onClick={() => setItem({ kind: "allowance", name: "", amount: "", taxable: true, pre_tax: false, insurance: false, active: true })}>Add an allowance or deduction</button>}</div>
      {!rows.length ? <Empty text="No allowances or deductions." /> : (
        <table className="w-full text-sm"><thead><tr><th className="th">Kind</th><th className="th">Name</th><th className="th text-right">Amount</th><th className="th">Treatment</th><th className="th">Months</th><th className="th" /></tr></thead>
          <tbody>{rows.map((i) => (
            <tr key={i.id} className={i.active ? "" : "opacity-40"}><td className="td">{i.kind}</td><td className="td font-semibold">{i.name}</td><td className="td text-right tabnums">{amt(i.amount)}</td>
              <td className="td text-xs">{i.kind === "allowance" ? (i.taxable ? "taxable" : "not taxable") : i.pre_tax ? "pension (before tax)" : i.insurance ? "insurance premium (relief)" : "after tax"}</td>
              <td className="td text-xs">{i.start_month || "always"} → {i.end_month || "no end"}</td>
              <td className="td">{can("hr.manage") && <button className="text-xs font-semibold text-accent" onClick={() => setItem(i)}>Edit</button>}</td></tr>
          ))}</tbody></table>
      )}
      {item && (
        <Modal title={item.id ? `Edit ${item.name}` : "Allowance or deduction"} onClose={() => setItem(null)}>
          <ErrorNote text={err} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Kind"><select className="input" value={item.kind} onChange={(ev) => setItem({ ...item, kind: ev.target.value })}><option value="allowance">Allowance (added)</option><option value="deduction">Deduction (taken off)</option></select></Field>
            <Field label="Name"><input className="input" value={item.name} placeholder={item.kind === "allowance" ? "House allowance" : "SACCO / loan / pension"} onChange={(ev) => setItem({ ...item, name: ev.target.value })} /></Field>
            <Field label="Amount (KES a month)"><input type="number" className="input" value={item.amount} onChange={(ev) => setItem({ ...item, amount: ev.target.value })} /></Field>
            {item.kind === "allowance" ? <label className="flex items-center gap-2 text-sm mt-6"><input type="checkbox" checked={item.taxable} onChange={(ev) => setItem({ ...item, taxable: ev.target.checked })} /> Taxable</label> : (
              <Field label="Treatment"><select className="input" value={item.pre_tax ? "pension" : item.insurance ? "insurance" : "after"}
                onChange={(ev) => setItem({ ...item, pre_tax: ev.target.value === "pension", insurance: ev.target.value === "insurance" })}>
                <option value="after">After tax (SACCO, loan, advance)</option><option value="pension">Registered pension (before tax)</option><option value="insurance">Life / health insurance premium (15% relief)</option></select></Field>)}
            <Field label="From month"><input type="month" className="input" value={item.start_month || ""} onChange={(ev) => setItem({ ...item, start_month: ev.target.value })} /></Field>
            <Field label="Until month"><input type="month" className="input" value={item.end_month || ""} onChange={(ev) => setItem({ ...item, end_month: ev.target.value })} /></Field>
            {item.kind === "deduction" && <div className="col-span-2"><Field label="Goes to account (optional)" hint="e.g. 1200 Staff Advances for an advance being repaid; empty = Other payroll deductions">
              <AccountSelect value={item.account_id} filter={(a) => ["asset", "liability"].includes(a.type) && !a.is_cash} onChange={(v) => setItem({ ...item, account_id: v })} /></Field></div>}
            {item.id && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={item.active} onChange={(ev) => setItem({ ...item, active: ev.target.checked })} /> Active</label>}
          </div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setItem(null)}>Cancel</button><button className="btn-primary" onClick={save}>Save</button></div>
        </Modal>
      )}
    </div>
  );
}

export function LeaveBalances({ rows }) {
  return (
    <table className="w-full text-sm"><thead><tr><th className="th">Leave</th><th className="th text-right">Per year</th><th className="th text-right">Earned so far</th>
      <th className="th text-right">Carried</th><th className="th text-right">Taken</th><th className="th text-right">Waiting</th><th className="th text-right">Available</th></tr></thead>
      <tbody>{(rows || []).map((b) => (
        <tr key={b.leave_type_id}><td className="td font-semibold">{b.name}{!b.paid && <span className="text-xs text-gray-400"> (unpaid)</span>}</td>
          <td className="td text-right">{b.unlimited ? "—" : b.entitlement}</td><td className="td text-right">{b.unlimited ? "—" : b.earned}</td>
          <td className="td text-right">{b.carried + b.adjustment || ""}</td><td className="td text-right">{b.used || ""}</td><td className="td text-right">{b.pending || ""}</td>
          <td className="td text-right font-bold">{b.unlimited ? "as needed" : b.available}</td></tr>
      ))}</tbody></table>
  );
}

function LeaveTab({ e, reload }) {
  const { can } = useAuth();
  const [form, setForm] = useState(null);
  const [adj, setAdj] = useState(null);
  const [err, setErr] = useState("");
  const apply = async () => {
    setErr("");
    try { await api("/api/v1/hr/leave/requests", { method: "POST", body: { ...form, employee_id: e.id, leave_type_id: Number(form.leave_type_id) } }); setForm(null); reload(); }
    catch (ex) { setErr(ex.message); }
  };
  const saveAdj = async () => {
    setErr("");
    try { await api("/api/v1/hr/leave/balances", { method: "PUT", body: { ...adj, employee_id: e.id, leave_type_id: Number(adj.leave_type_id), year: Number(adj.year), carried: Number(adj.carried || 0), adjustment: Number(adj.adjustment || 0) } }); setAdj(null); reload(); }
    catch (ex) { setErr(ex.message); }
  };
  return (
    <div className="card p-4">
      <ErrorNote text={err} />
      <div className="flex gap-2 mb-3"><div className="font-bold flex-1">Leave this year</div>
        {can("hr.manage") && <><button className="btn-ghost" onClick={() => setAdj({ leave_type_id: e.leave[0]?.leave_type_id, year: new Date().getFullYear(), carried: 0, adjustment: 0, note: "" })}>Carry forward / adjust</button>
          <button className="btn-primary" onClick={() => setForm({ leave_type_id: e.leave[0]?.leave_type_id, start_date: today(), end_date: today(), reason: "" })}>Record leave</button></>}</div>
      <LeaveBalances rows={e.leave} />
      <div className="font-bold mt-5 mb-2">Requests</div>
      {!e.leave_requests.length ? <Empty text="None" /> : (
        <table className="w-full text-sm"><tbody>{e.leave_requests.map((r) => (
          <tr key={r.id}><td className="td">{fmtDate(r.start_date)} – {fmtDate(r.end_date)}</td><td className="td">{e.leave.find((b) => b.leave_type_id === r.leave_type_id)?.name}</td>
            <td className="td text-right">{r.days} days</td><td className="td"><Badge value={LEAVE_BADGE[r.status]}>{r.status}</Badge></td><td className="td text-gray-500">{r.reason}</td></tr>
        ))}</tbody></table>
      )}
      {form && (
        <Modal title="Record leave" onClose={() => setForm(null)}>
          <ErrorNote text={err} />
          <Field label="Leave"><select className="input" value={form.leave_type_id} onChange={(ev) => setForm({ ...form, leave_type_id: ev.target.value })}>
            {e.leave.map((b) => <option key={b.leave_type_id} value={b.leave_type_id}>{b.name}</option>)}</select></Field>
          <div className="grid grid-cols-2 gap-3 mt-3">
            <Field label="First day"><input type="date" className="input" value={form.start_date} onChange={(ev) => setForm({ ...form, start_date: ev.target.value })} /></Field>
            <Field label="Last day"><input type="date" className="input" value={form.end_date} onChange={(ev) => setForm({ ...form, end_date: ev.target.value })} /></Field>
          </div>
          <div className="mt-3"><Field label="Reason"><input className="input" value={form.reason} onChange={(ev) => setForm({ ...form, reason: ev.target.value })} /></Field></div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setForm(null)}>Cancel</button><button className="btn-primary" onClick={apply}>Save (waits for approval)</button></div>
        </Modal>
      )}
      {adj && (
        <Modal title="Carry forward or adjust leave" onClose={() => setAdj(null)}>
          <ErrorNote text={err} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Leave"><select className="input" value={adj.leave_type_id} onChange={(ev) => setAdj({ ...adj, leave_type_id: ev.target.value })}>
              {e.leave.map((b) => <option key={b.leave_type_id} value={b.leave_type_id}>{b.name}</option>)}</select></Field>
            <Field label="Year"><input type="number" className="input" value={adj.year} onChange={(ev) => setAdj({ ...adj, year: ev.target.value })} /></Field>
            <Field label="Carried from last year (days)"><input type="number" className="input" value={adj.carried} onChange={(ev) => setAdj({ ...adj, carried: ev.target.value })} /></Field>
            <Field label="Adjustment (+/− days)"><input type="number" className="input" value={adj.adjustment} onChange={(ev) => setAdj({ ...adj, adjustment: ev.target.value })} /></Field>
            <div className="col-span-2"><Field label="Note"><input className="input" value={adj.note} onChange={(ev) => setAdj({ ...adj, note: ev.target.value })} /></Field></div>
          </div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setAdj(null)}>Cancel</button><button className="btn-primary" onClick={saveAdj}>Save</button></div>
        </Modal>
      )}
    </div>
  );
}

const DOCS = { contract: "Contract", offer_letter: "Offer letter", id: "ID copy", kra_pin: "KRA PIN certificate", nssf: "NSSF card", shif: "SHIF / SHA",
  certificate: "Certificate", cv: "CV", warning: "Warning letter", exit: "Exit / clearance", other: "Other" };

function Docs({ e, reload }) {
  const { can } = useAuth();
  const [f, setF] = useState({ doc_type: "contract", expires_on: "", file: null });
  const [err, setErr] = useState("");
  const add = async () => {
    setErr("");
    try {
      const fd = new FormData(); fd.append("file", f.file); fd.append("doc_type", f.doc_type);
      if (f.expires_on) fd.append("expires_on", f.expires_on);
      await upload(`/api/v1/hr/employees/${e.id}/documents`, fd); setF({ ...f, file: null }); reload();
    } catch (ex) { setErr(ex.message); }
  };
  return (
    <div className="card p-4">
      <ErrorNote text={err} />
      {!e.documents.length ? <Empty text="No documents yet." /> : (
        <table className="w-full text-sm mb-4"><tbody>{e.documents.map((d) => (
          <tr key={d.id}><td className="td">{DOCS[d.doc_type] || d.doc_type}</td><td className="td">
            <button className="text-accent hover:underline" onClick={async () => window.open(await blobUrl(`/api/v1/hr/documents/${d.id}`), "_blank", "noopener")}>{d.filename}</button></td>
            <td className="td">{d.expires_on ? `expires ${fmtDate(d.expires_on)}` : ""}</td><td className="td text-gray-400">{fmtDate(d.created_at)}</td></tr>
        ))}</tbody></table>
      )}
      {can("hr.manage") && (
        <div className="grid sm:grid-cols-4 gap-3 items-end border-t border-border pt-3">
          <Field label="Kind"><select className="input" value={f.doc_type} onChange={(ev) => setF({ ...f, doc_type: ev.target.value })}>{Object.entries(DOCS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          <Field label="Expires (optional)"><input type="date" className="input" value={f.expires_on} onChange={(ev) => setF({ ...f, expires_on: ev.target.value })} /></Field>
          <Field label="File"><input type="file" className="input" accept=".pdf,.jpg,.jpeg,.png,.webp,.docx" onChange={(ev) => setF({ ...f, file: ev.target.files?.[0] })} /></Field>
          <button className="btn-primary" disabled={!f.file} onClick={add}>Upload</button>
        </div>
      )}
    </div>
  );
}

export function AppraisalList({ rows }) {
  if (!rows?.length) return <div className="card p-4"><Empty text="No appraisals yet." /></div>;
  return (
    <div className="space-y-3">{rows.map((a) => (
      <div key={a.id} className="card p-4">
        <div className="flex gap-3 items-center mb-2"><div className="font-bold flex-1">{a.period}</div><div className="text-lg font-extrabold">{a.score ?? "—"} / 5</div>
          <Badge value={a.status === "acknowledged" ? "success" : a.status === "shared" ? "approved" : "draft"}>{a.status}</Badge></div>
        <table className="w-full text-sm"><tbody>{a.goals.map((g, i) => <tr key={i}><td className="py-0.5">{g.goal}</td><td className="text-right text-gray-500">{g.weight}%</td><td className="text-right font-semibold w-12">{g.score ?? "—"}</td></tr>)}</tbody></table>
        {a.strengths && <div className="text-sm mt-2"><b>Strengths:</b> {a.strengths}</div>}
        {a.improvements && <div className="text-sm"><b>To improve:</b> {a.improvements}</div>}
        {a.employee_comment && <div className="text-sm mt-1 text-gray-600"><b>Employee's comment:</b> {a.employee_comment}</div>}
      </div>
    ))}</div>
  );
}

export function SlipList({ rows }) {
  if (!rows?.length) return <div className="card p-4"><Empty text="No payslips yet." /></div>;
  return (
    <div className="card overflow-hidden"><table className="w-full text-sm"><thead><tr><th className="th">Month</th><th className="th text-right">Gross</th><th className="th text-right">Net pay</th><th className="th">Payroll</th><th className="th" /></tr></thead>
      <tbody>{rows.map((p) => <tr key={p.id}><td className="td font-semibold">{p.label}</td><td className="td text-right tabnums">{amt(p.gross)}</td><td className="td text-right tabnums">{amt(p.net)}</td>
        <td className="td">{p.status}</td><td className="td"><Link className="text-xs font-semibold text-accent" to={`/hr/payslips/${p.id}`}>Open payslip</Link></td></tr>)}</tbody></table></div>
  );
}
