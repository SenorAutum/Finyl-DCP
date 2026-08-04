// Audit Trail — filterable system log. audit.view gated (System Admin + HQ read).
import { useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { PageHeader, Spinner, Empty } from "../../components/ui";

export default function AuditLog() {
  const [rows, setRows] = useState(null);
  const [f, setF] = useState({ action: "", entity_type: "", user_email: "" });
  const [msg, setMsg] = useState("");

  const load = () => {
    setRows(null);
    const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    api(`/api/v1/access/audit${q ? "?" + q : ""}`).then(setRows).catch((e) => { setMsg(e.detail); setRows([]); });
  };
  useEffect(load, []); // eslint-disable-line

  const ts = (s) => (s ? new Date(s).toLocaleString("en-KE") : "—");

  return (
    <div>
      <PageHeader title="Audit Trail" crumbs={["Administration", "Audit"]} />
      {msg && <div className="mb-3 text-sm px-3 py-2 rounded-lg bg-red-50 text-red-600 border border-red-200">{msg}</div>}
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div><label className="label">Action</label><input className="input !w-40" value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })} placeholder="loan.approve" /></div>
        <div><label className="label">Entity type</label><input className="input !w-40" value={f.entity_type} onChange={(e) => setF({ ...f, entity_type: e.target.value })} placeholder="loan" /></div>
        <div><label className="label">User email</label><input className="input !w-52" value={f.user_email} onChange={(e) => setF({ ...f, user_email: e.target.value })} placeholder="@mularcredit" /></div>
        <button className="btn-primary" onClick={load}>Filter</button>
        <button className="btn-ghost" onClick={() => { setF({ action: "", entity_type: "", user_email: "" }); setTimeout(load, 0); }}>Clear</button>
      </div>
      {!rows ? <Spinner /> : (
        <div className="card overflow-x-auto">
          {rows.length === 0 ? <Empty text="No audit records match" /> : (
            <table className="w-full text-xs">
              <thead><tr className="text-left border-b border-border bg-canvas">
                <th className="th">When</th><th className="th">User</th><th className="th">Action</th>
                <th className="th">Entity</th><th className="th">Details</th><th className="th">IP</th>
              </tr></thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id} className="border-b border-border/60 align-top">
                    <td className="td whitespace-nowrap">{ts(a.created_at)}</td>
                    <td className="td">{a.user_email || "—"}</td>
                    <td className="td font-mono text-teal">{a.action}</td>
                    <td className="td">{a.entity_type}{a.entity_id != null && <span className="text-gray-400"> #{a.entity_id}</span>}</td>
                    <td className="td max-w-xs"><pre className="whitespace-pre-wrap break-words text-[10px] text-gray-500">{a.details ? JSON.stringify(a.details) : ""}</pre></td>
                    <td className="td text-gray-400">{a.ip || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
