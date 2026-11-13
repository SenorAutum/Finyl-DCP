// WP17 — pieces shared by the CRM screens.
import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { Modal } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, notify } from "../../lib/api";
import { ErrorNote, Field } from "../platform/common";
import { today } from "../books/common";

export { ErrorNote, Field, exportCsv, kes, num, useLoad } from "../platform/common";
export { today };

const LINKS = [
  { to: "/crm", label: "Pipeline", end: true },
  { to: "/crm/tasks", label: "Follow-ups", perm: "crm.view" },
  { to: "/crm/segments", label: "Segments", perm: "crm.view" },
  { to: "/crm/campaigns", label: "SMS campaigns", perm: "crm.view" },
  { to: "/crm/report", label: "Pipeline report", perm: "crm.view" },
];

export function CrmTabs() {
  const { can } = useAuth();
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-border mb-5 -mt-1">
      {LINKS.filter((l) => !l.perm || can(l.perm)).map((l) => (
        <NavLink key={l.to} to={l.to} end={l.end}
          className={({ isActive }) => `px-3 py-2 text-sm font-semibold whitespace-nowrap border-b-2 -mb-px ${
            isActive ? "border-accent text-charcoal" : "border-transparent text-gray-500 hover:text-charcoal"}`}>
          {l.label}
        </NavLink>
      ))}
    </div>
  );
}

export const KIND_LABEL = { call: "Phone call", visit: "Visit", sms: "SMS", whatsapp: "WhatsApp", meeting: "Meeting", email: "Email", note: "Note" };
export const OUTCOME_LABEL = { reached: "Spoke to them", no_answer: "No answer", interested: "Interested", not_interested: "Not interested",
  promise: "Promised to pay", complaint: "Raised a complaint", other: "Other" };
export const LOAN_STATE_LABEL = { any: "Any", open: "Has a loan running", no_open: "No loan running", in_arrears: "Behind on a loan",
  never_borrowed: "Never borrowed", repaid_no_open: "Repaid before, no loan now" };

let _opts = null;
export function useCrmOptions() {
  const [o, setO] = useState(_opts);
  useEffect(() => {
    let live = true;
    api("/api/v1/crm-plus/options").then((d) => { _opts = d; if (live) setO(d); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return o;
}

export function ClientSearch({ value, onPick }) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState([]);
  useEffect(() => {
    if (q.length < 2) { setRows([]); return undefined; }
    const t = setTimeout(() => api(`/api/v1/crm-plus/clients/search?q=${encodeURIComponent(q)}`).then((d) => setRows(d.clients)).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <div>
      <input className="input" placeholder={value ? value.name : "Type a client's name or phone"} value={q} onChange={(e) => setQ(e.target.value)} />
      {rows.length > 0 && <div className="border border-border rounded-lg mt-1 max-h-40 overflow-y-auto">{rows.map((c) => (
        <button key={c.id} type="button" className="block w-full text-left px-3 py-1.5 text-sm hover:bg-canvas" onClick={() => { onPick(c); setQ(""); setRows([]); }}>{c.name} · {c.phone}</button>
      ))}</div>}
      {value && <div className="text-xs text-gray-500 mt-1">Chosen: {value.name}</div>}
    </div>
  );
}

function nowLocal() {
  const d = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
  return d.toISOString().slice(0, 16);
}

// Log a call, visit, SMS… with a client or a lead, and optionally set the next step.
export function LogContact({ borrowerId, leadId, name, onClose, onDone }) {
  const opts = useCrmOptions();
  const [f, setF] = useState({ kind: "call", direction: "out", subject: "", notes: "", outcome: "reached", happened_at: nowLocal(),
    follow: false, title: "", due_date: today(), assigned_user_id: "", priority: "normal" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const save = async () => {
    setErr(""); setBusy(true);
    try {
      const body = { borrower_id: borrowerId || null, lead_id: leadId || null, kind: f.kind, direction: f.direction, subject: f.subject || null,
        notes: f.notes || null, outcome: f.outcome || null, happened_at: new Date(f.happened_at).toISOString() };
      if (f.follow) body.next_step = { title: f.title, due_date: f.due_date, priority: f.priority, assigned_user_id: f.assigned_user_id ? Number(f.assigned_user_id) : null };
      await api("/api/v1/crm-plus/interactions", { method: "POST", body });
      notify(f.follow ? "Contact logged and follow-up set" : "Contact logged", { tone: "success" });
      onDone();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal title={`Log contact with ${name}`} onClose={onClose} wide>
      <ErrorNote text={err} />
      <div className="grid sm:grid-cols-3 gap-3">
        <Field label="How"><select className="input" value={f.kind} onChange={set("kind")}>{Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="Who started it"><select className="input" value={f.direction} onChange={set("direction")}><option value="out">We did</option><option value="in">They did</option></select></Field>
        <Field label="When"><input type="datetime-local" className="input" value={f.happened_at} onChange={set("happened_at")} /></Field>
        <div className="sm:col-span-2"><Field label="Subject"><input className="input" value={f.subject} onChange={set("subject")} placeholder="e.g. Asked about a bigger loan" /></Field></div>
        <Field label="Outcome"><select className="input" value={f.outcome} onChange={set("outcome")}><option value="">—</option>{Object.entries(OUTCOME_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <div className="sm:col-span-3"><Field label="Notes"><textarea className="input" rows={3} value={f.notes} onChange={set("notes")} placeholder="What was said, what they need" /></Field></div>
      </div>
      <label className="flex items-center gap-2 text-sm mt-3"><input type="checkbox" checked={f.follow} onChange={set("follow")} /> Set a follow-up</label>
      {f.follow && (
        <div className="grid sm:grid-cols-4 gap-3 mt-2">
          <div className="sm:col-span-2"><Field label="What to do next"><input className="input" value={f.title} onChange={set("title")} placeholder="e.g. Visit the shop with the application form" /></Field></div>
          <Field label="By"><input type="date" className="input" value={f.due_date} onChange={set("due_date")} /></Field>
          <Field label="Priority"><select className="input" value={f.priority} onChange={set("priority")}><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option></select></Field>
          <div className="sm:col-span-2"><Field label="Who does it"><select className="input" value={f.assigned_user_id} onChange={set("assigned_user_id")}>
            <option value="">Me</option>{(opts?.users || []).filter((u) => u.id !== opts?.me).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></Field></div>
        </div>
      )}
      <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={busy || !(f.subject || f.notes) || (f.follow && f.title.length < 3)} onClick={save}>Save</button></div>
    </Modal>
  );
}

const TYPE_ICON = { interaction: "💬", call: "☎", complaint: "⚠", sms: "✉", loan: "📋", payment: "₿", promise: "🤝", task: "☑" };

export function Timeline({ items }) {
  if (!items?.length) return <div className="text-sm text-gray-400 p-4">Nothing yet.</div>;
  return (
    <ol className="divide-y divide-border">
      {items.map((e, i) => (
        <li key={i} className="flex gap-3 px-4 py-2.5 text-sm">
          <span className="w-6 text-center shrink-0" aria-hidden>{TYPE_ICON[e.type] || "•"}</span>
          <div className="min-w-0 flex-1">
            <div className="font-semibold">{e.type === "interaction" ? `${KIND_LABEL[e.kind] || e.kind}${e.direction === "in" ? " from them" : ""}: ${e.title}` : e.title}
              {e.outcome && <span className="ml-2 text-xs font-normal text-gray-500">({OUTCOME_LABEL[e.outcome] || e.outcome})</span>}
              {e.loan_id && <Link className="ml-2 text-xs text-accent font-normal" to={`/loans/${e.loan_id}`}>loan →</Link>}</div>
            {e.detail && <div className="text-gray-600 whitespace-pre-line break-words">{e.detail}</div>}
          </div>
          <div className="text-right text-xs text-gray-400 shrink-0">{e.date_only
              ? new Date(`${e.at.slice(0, 10)}T12:00:00`).toLocaleDateString("en-KE", { dateStyle: "medium" })
              : new Date(e.at.endsWith("Z") || e.at.includes("+") ? e.at : `${e.at}Z`).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" })}
            {e.by && <div>{e.by}</div>}</div>
        </li>
      ))}
    </ol>
  );
}

export function closeTask(id, status, note, done) {
  return api(`/api/v1/crm-plus/tasks/${id}/close`, { method: "POST", body: { status, note } })
    .then(() => { notify(status === "done" ? "Marked done" : status === "open" ? "Reopened" : "Cancelled", { tone: "success" }); done(); })
    .catch((e) => notify(e.message, { tone: "error" }));
}
