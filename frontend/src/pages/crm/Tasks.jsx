// WP17 — follow-ups: what each member of staff has promised to do next, with
// overdue ones in red.
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { ClientSearch, CrmTabs, ErrorNote, Field, closeTask, exportCsv, today, useCrmOptions, useLoad } from "./common";

const PRI = { high: "overdue", normal: "pending", low: "closed" };

export default function Tasks() {
  const { can } = useAuth();
  const opts = useCrmOptions();
  // Managers start on everyone's follow-ups; officers on their own.
  const [who, setWho] = useState(can("crm.campaigns_approve") ? "all" : "mine");
  const [status, setStatus] = useState("open");
  const q = who === "mine" ? "mine=true" : `mine=false${who !== "all" ? `&assigned_user_id=${who}` : ""}`;
  const { data, error, loading, reload } = useLoad(`/api/v1/crm-plus/tasks?${q}&status=${status}`);
  const [form, setForm] = useState(null);
  const [closing, setClosing] = useState(null);
  const [err, setErr] = useState("");
  const rows = data?.tasks || [];
  const save = async () => {
    setErr("");
    try {
      await api("/api/v1/crm-plus/tasks", { method: "POST", body: { title: form.title, notes: form.notes || null, due_date: form.due_date, priority: form.priority,
        assigned_user_id: form.assigned_user_id ? Number(form.assigned_user_id) : null, borrower_id: form.client?.id || null } });
      notify("Follow-up added", { tone: "success" }); setForm(null); reload();
    } catch (e) { setErr(e.message); }
  };
  return (
    <div>
      <PageHeader title="Follow-ups" crumbs={["Engagement", "CRM", "Follow-ups"]}
        actions={can("crm.log") && <button className="btn-primary" onClick={() => setForm({ title: "", notes: "", due_date: today(), priority: "normal", assigned_user_id: "", client: null })}>New follow-up</button>} />
      <CrmTabs />
      <div className="grid grid-cols-3 gap-3 mb-4 max-w-xl">
        <div className="card p-3"><div className="text-xs text-gray-500">Open</div><div className="text-xl font-bold">{data?.counts.open ?? "—"}</div></div>
        <div className="card p-3"><div className="text-xs text-gray-500">Due today</div><div className="text-xl font-bold">{data?.counts.today ?? "—"}</div></div>
        <div className="card p-3"><div className="text-xs text-gray-500">Overdue</div><div className={`text-xl font-bold ${data?.counts.overdue ? "text-red-600" : ""}`}>{data?.counts.overdue ?? "—"}</div></div>
      </div>
      <div className="card overflow-hidden">
        <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-border items-center">
          <select className="input !w-auto text-sm" value={who} onChange={(e) => setWho(e.target.value)} aria-label="Whose follow-ups">
            <option value="mine">Mine</option><option value="all">Everyone's</option>
            {(opts?.users || []).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
          <select className="input !w-auto text-sm" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="open">Open</option><option value="done">Done</option><option value="cancelled">Cancelled</option><option value="">All</option></select>
          <div className="flex-1" />
          <button className="btn-ghost" disabled={!rows.length} onClick={() => exportCsv("follow-ups.csv", [["Due", "due_date"], ["Follow-up", "title"], ["Client", "client"],
            ["Lead", "lead"], ["Assigned to", "assigned_to"], ["Priority", "priority"], ["Status", "status"]], rows)}>Export CSV</button>
        </div>
        <ErrorNote text={error} />
        {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No follow-ups here." /> : (
          <table className="min-w-full text-sm"><thead><tr><th className="th">Due</th><th className="th">Follow-up</th><th className="th">About</th>
            <th className="th">Assigned to</th><th className="th">Priority</th><th className="th" /></tr></thead>
            <tbody>{rows.map((t) => (
              <tr key={t.id}>
                <td className={`td whitespace-nowrap ${t.overdue ? "text-red-600 font-semibold" : ""}`}>{fmtDate(t.due_date)}{t.overdue && <div className="text-xs">overdue</div>}</td>
                <td className="td whitespace-normal"><div className="font-semibold">{t.title}</div>{t.notes && <div className="text-xs text-gray-500">{t.notes}</div>}
                  {t.completion_note && <div className="text-xs text-emerald-700">{t.completion_note}</div>}</td>
                <td className="td">{t.borrower_id ? <Link className="text-accent" to={`/clients/${t.borrower_id}`}>{t.client}</Link> : t.lead ? <span>Lead: {t.lead}</span> : "—"}
                  {t.account_number && <div><Link className="text-xs font-mono text-accent" to={`/loans/${t.loan_id}`}>{t.account_number}</Link></div>}</td>
                <td className="td">{t.assigned_to}</td>
                <td className="td"><Badge value={PRI[t.priority]}>{t.priority}</Badge></td>
                <td className="td whitespace-nowrap">{can("crm.log") && (t.status === "open" ? <>
                  <button className="text-xs font-semibold text-accent mr-2" onClick={() => setClosing({ id: t.id, status: "done", note: "" })}>Done</button>
                  <button className="text-xs text-gray-500" onClick={() => setClosing({ id: t.id, status: "cancelled", note: "" })}>Cancel</button></>
                  : <button className="text-xs text-gray-500" onClick={() => closeTask(t.id, "open", null, reload)}>Reopen</button>)}</td>
              </tr>))}</tbody></table>
        )}
      </div>
      {form && (
        <Modal title="New follow-up" onClose={() => setForm(null)}>
          <ErrorNote text={err} />
          <div className="grid sm:grid-cols-2 gap-3">
            <div className="sm:col-span-2"><Field label="What to do"><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g. Call to remind about Friday's instalment" /></Field></div>
            <div className="sm:col-span-2"><Field label="Client (optional)"><ClientSearch value={form.client} onPick={(c) => setForm({ ...form, client: c })} /></Field></div>
            <Field label="By"><input type="date" className="input" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} /></Field>
            <Field label="Priority"><select className="input" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option></select></Field>
            <div className="sm:col-span-2"><Field label="Who does it"><select className="input" value={form.assigned_user_id} onChange={(e) => setForm({ ...form, assigned_user_id: e.target.value })}>
              <option value="">Me</option>{(opts?.users || []).filter((u) => u.id !== opts?.me).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></Field></div>
            <div className="sm:col-span-2"><Field label="Notes"><textarea className="input" rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field></div>
          </div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn-primary" disabled={form.title.length < 3} onClick={save}>Save</button></div>
        </Modal>
      )}
      {closing && (
        <Modal title={closing.status === "done" ? "Mark done" : "Cancel follow-up"} onClose={() => setClosing(null)}>
          <Field label="What happened (optional)"><input className="input" value={closing.note} onChange={(e) => setClosing({ ...closing, note: e.target.value })} placeholder="e.g. Paid KES 2,000 on M-Pesa" /></Field>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setClosing(null)}>Back</button>
            <button className="btn-primary" onClick={() => closeTask(closing.id, closing.status, closing.note || null, () => { setClosing(null); reload(); })}>Save</button></div>
        </Modal>
      )}
    </div>
  );
}
