// HQ Operations reporting hub — export reports, schedule recurring runs,
// save custom templates, flag anomalies. Read-only company-wide role.
import { useEffect, useState } from "react";
import { api, download } from "../../lib/api";
import { useAuth } from "../../hooks/useAuth";
import { PageHeader, Empty, Spinner, Modal, Badge } from "../../components/ui";

const REPORTS = [
  ["loan_book", "Loan Book", "Every loan with principal, status & outstanding"],
  ["par", "Portfolio at Risk", "PAR% by branch"],
  ["disbursement", "Disbursements", "Disbursed loans & dates"],
  ["collections", "Collections", "Recent repayments"],
  ["productivity", "Officer Productivity", "Loans & principal per officer"],
];

export default function Reporting() {
  const { can } = useAuth();
  const [tab, setTab] = useState("export");
  const tabs = [["export", "Export"], ["schedules", "Schedules"], ["templates", "Templates"], ["anomalies", "Anomalies"]];
  return (
    <div>
      <PageHeader title="HQ Reporting & Operations" crumbs={["Reporting"]} />
      <div className="flex gap-2 mb-4 flex-wrap">
        {tabs.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-3 py-1.5 rounded-lg text-sm font-semibold ${tab === k ? "bg-accent text-white" : "bg-surface border border-border text-gray-600"}`}>{l}</button>
        ))}
      </div>
      {tab === "export" && <ExportTab />}
      {tab === "schedules" && <SchedulesTab canManage={can("reports.schedule")} />}
      {tab === "templates" && <TemplatesTab canManage={can("reports.template")} />}
      {tab === "anomalies" && <AnomaliesTab canFlag={can("reports.flag")} />}
    </div>
  );
}

function ExportTab() {
  const [busy, setBusy] = useState("");
  const dl = async (t) => { setBusy(t); try { await download(`/api/v1/reporting/export/${t}`, `${t}.csv`); } catch (e) { alert(e.detail); } finally { setBusy(""); } };
  return (
    <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {REPORTS.map(([k, label, desc]) => (
        <div key={k} className="card p-4 flex flex-col">
          <div className="font-semibold">{label}</div>
          <div className="text-xs text-gray-400 flex-1 mt-1">{desc}</div>
          <button className="btn-primary mt-3 !py-1.5" disabled={busy === k} onClick={() => dl(k)}>{busy === k ? "Preparing…" : "Download CSV"}</button>
        </div>
      ))}
    </div>
  );
}

function SchedulesTab({ canManage }) {
  const [rows, setRows] = useState(null); const [modal, setModal] = useState(false); const [msg, setMsg] = useState("");
  const load = () => api("/api/v1/reporting/schedules").then(setRows).catch((e) => { setMsg(e.detail); setRows([]); });
  useEffect(load, []);
  const del = async (id) => { try { await api(`/api/v1/reporting/schedules/${id}`, { method: "DELETE" }); load(); } catch (e) { setMsg(e.detail); } };
  if (!rows) return <Spinner />;
  return (
    <div>
      {msg && <div className="mb-3 text-sm px-3 py-2 rounded-lg bg-teal-50 text-teal-700 border border-teal-200">{msg}</div>}
      {canManage && <button className="btn-primary mb-3" onClick={() => setModal(true)}>+ Schedule report</button>}
      <div className="card overflow-hidden">
        {rows.length === 0 ? <Empty text="No scheduled reports" /> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left border-b border-border bg-canvas">
              <th className="th">Name</th><th className="th">Report</th><th className="th">Frequency</th>
              <th className="th">Recipients</th>{canManage && <th className="th text-right">Actions</th>}
            </tr></thead>
            <tbody>{rows.map((s) => (
              <tr key={s.id} className="border-b border-border/60">
                <td className="td font-medium">{s.name}</td><td className="td">{s.report_type}</td>
                <td className="td capitalize">{s.frequency}</td><td className="td text-xs">{s.recipients || "—"}</td>
                {canManage && <td className="td text-right"><button className="btn-ghost !py-1 !px-2 text-xs text-red-600" onClick={() => del(s.id)}>Delete</button></td>}
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
      {modal && <ScheduleModal onClose={() => setModal(false)} onDone={() => { setModal(false); load(); }} />}
    </div>
  );
}

function ScheduleModal({ onClose, onDone }) {
  const [f, setF] = useState({ name: "", report_type: "loan_book", frequency: "weekly", recipients: "" });
  const [err, setErr] = useState("");
  const up = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async () => { try { await api("/api/v1/reporting/schedules", { method: "POST", body: f }); onDone(); } catch (e) { setErr(e.detail); } };
  return (
    <Modal title="Schedule recurring report" onClose={onClose}>
      <div className="space-y-3">
        <div><label className="label">Name</label><input className="input" value={f.name} onChange={up("name")} /></div>
        <div><label className="label">Report</label><select className="input" value={f.report_type} onChange={up("report_type")}>{REPORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div><label className="label">Frequency</label><select className="input" value={f.frequency} onChange={up("frequency")}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></div>
        <div><label className="label">Recipients (comma-separated emails)</label><input className="input" value={f.recipients} onChange={up("recipients")} /></div>
        {err && <div className="text-sm text-red-600">{err}</div>}
        <div className="flex justify-end gap-2 pt-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={save} disabled={!f.name}>Save</button></div>
      </div>
    </Modal>
  );
}

function TemplatesTab({ canManage }) {
  const [rows, setRows] = useState(null); const [modal, setModal] = useState(false); const [msg, setMsg] = useState("");
  const load = () => api("/api/v1/reporting/templates").then(setRows).catch((e) => { setMsg(e.detail); setRows([]); });
  useEffect(load, []);
  if (!rows) return <Spinner />;
  return (
    <div>
      {msg && <div className="mb-3 text-sm px-3 py-2 rounded-lg bg-teal-50 text-teal-700 border border-teal-200">{msg}</div>}
      {canManage && <button className="btn-primary mb-3" onClick={() => setModal(true)}>+ New template</button>}
      <div className="card overflow-hidden">
        {rows.length === 0 ? <Empty text="No custom templates" /> :
          rows.map((t) => (
            <div key={t.id} className="px-4 py-3 border-b border-border/60">
              <div className="font-medium">{t.name}</div>
              <pre className="text-[10px] text-gray-400 mt-1 whitespace-pre-wrap">{JSON.stringify(t.definition)}</pre>
            </div>
          ))}
      </div>
      {modal && <TemplateModal onClose={() => setModal(false)} onDone={() => { setModal(false); load(); }} />}
    </div>
  );
}

function TemplateModal({ onClose, onDone }) {
  const [name, setName] = useState(""); const [cols, setCols] = useState(""); const [err, setErr] = useState("");
  const save = async () => {
    try {
      const definition = { columns: cols.split(",").map((c) => c.trim()).filter(Boolean) };
      await api("/api/v1/reporting/templates", { method: "POST", body: { name, definition } }); onDone();
    } catch (e) { setErr(e.detail); }
  };
  return (
    <Modal title="Custom report template" onClose={onClose}>
      <div className="space-y-3">
        <div><label className="label">Template name</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div><label className="label">Columns (comma-separated)</label><input className="input" value={cols} onChange={(e) => setCols(e.target.value)} placeholder="account_number, principal, status" /></div>
        {err && <div className="text-sm text-red-600">{err}</div>}
        <div className="flex justify-end gap-2 pt-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={save} disabled={!name}>Save</button></div>
      </div>
    </Modal>
  );
}

function AnomaliesTab({ canFlag }) {
  const [rows, setRows] = useState(null); const [modal, setModal] = useState(false); const [msg, setMsg] = useState("");
  const load = () => api("/api/v1/reporting/anomalies").then(setRows).catch((e) => { setMsg(e.detail); setRows([]); });
  useEffect(load, []);
  if (!rows) return <Spinner />;
  return (
    <div>
      {msg && <div className="mb-3 text-sm px-3 py-2 rounded-lg bg-teal-50 text-teal-700 border border-teal-200">{msg}</div>}
      {canFlag && <button className="btn-primary mb-3" onClick={() => setModal(true)}>+ Flag anomaly</button>}
      <div className="card overflow-hidden">
        {rows.length === 0 ? <Empty text="No anomalies flagged" /> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left border-b border-border bg-canvas">
              <th className="th">Entity</th><th className="th">Note</th><th className="th">Status</th><th className="th">By</th>
            </tr></thead>
            <tbody>{rows.map((a) => (
              <tr key={a.id} className="border-b border-border/60">
                <td className="td">{a.entity_type}{a.entity_id && ` #${a.entity_id}`}</td>
                <td className="td">{a.note}</td><td className="td"><Badge value="pending">{a.status}</Badge></td>
                <td className="td text-xs">{a.flagged_by}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
      {modal && <AnomalyModal onClose={() => setModal(false)} onDone={() => { setModal(false); load(); }} />}
    </div>
  );
}

function AnomalyModal({ onClose, onDone }) {
  const [f, setF] = useState({ entity_type: "loan", entity_id: "", note: "" }); const [err, setErr] = useState("");
  const up = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async () => { try { await api("/api/v1/reporting/anomalies", { method: "POST", body: f }); onDone(); } catch (e) { setErr(e.detail); } };
  return (
    <Modal title="Flag data anomaly" onClose={onClose}>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <div><label className="label">Entity type</label><input className="input" value={f.entity_type} onChange={up("entity_type")} /></div>
          <div><label className="label">Entity id</label><input className="input" value={f.entity_id} onChange={up("entity_id")} /></div>
        </div>
        <div><label className="label">Note</label><textarea className="input" rows={3} value={f.note} onChange={up("note")} /></div>
        {err && <div className="text-sm text-red-600">{err}</div>}
        <div className="flex justify-end gap-2 pt-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={save} disabled={!f.note}>Flag</button></div>
      </div>
    </Modal>
  );
}
