// Consumer Protection & Complaints Registry with live 14-day SLA countdown chips
// (emerald = on-track, amber < 3 days left, red = breached).
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Badge, Empty, KpiCard, Modal, PageHeader, Pagination, Spinner } from "../../components/ui";

function SlaChip({ c }) {
  if (["resolved", "closed"].includes(c.status)) {
    const days = c.resolved_at ? Math.round((new Date(c.resolved_at) - new Date(c.created_at)) / 86400000) : null;
    const within = c.resolved_at && new Date(c.resolved_at) <= new Date(c.sla_deadline);
    return (
      <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold ${within ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}`}>
        {within ? `Resolved in ${days}d ✓` : `Resolved late (${days}d)`}
      </span>
    );
  }
  const msLeft = new Date(c.sla_deadline) - Date.now();
  const daysLeft = Math.ceil(msLeft / 86400000);
  const cls = msLeft < 0 ? "bg-red-100 text-red-700"
    : daysLeft <= 3 ? "bg-amber-100 text-amber-700" : "bg-emerald-100 text-emerald-700";
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold ${cls}`}>
      {msLeft < 0 ? `⚠ SLA breached ${Math.abs(daysLeft)}d ago` : `${daysLeft}d left`}
    </span>
  );
}

export default function Complaints() {
  const [stats, setStats] = useState(null);
  const [meta, setMeta] = useState({ categories: [], statuses: [] });
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [detail, setDetail] = useState(null); // complaint being updated
  const [staff, setStaff] = useState([]);
  const [borrowers, setBorrowers] = useState([]);
  const [err, setErr] = useState("");

  const load = () => {
    api(`/api/v1/complaints?status=${status}&category=${category}&page=${page}`).then(setData).catch(() => {});
    api("/api/v1/complaints/stats").then(setStats).catch(() => {});
  };
  useEffect(load, [status, category, page]);
  useEffect(() => {
    api("/api/v1/complaints/meta").then(setMeta).catch(() => {});
    api("/api/v1/lending/org").then((o) => setStaff(o.staff)).catch(() => {});
    api("/api/v1/clients?page_size=100").then((d) => setBorrowers(d.items)).catch(() => {});
  }, []);

  const [form, setForm] = useState({ borrower_id: "", category: "other", description: "", assigned_staff_id: "" });
  const create = async (e) => {
    e.preventDefault(); setErr("");
    try {
      await api("/api/v1/complaints", { method: "POST", body: {
        borrower_id: form.borrower_id ? Number(form.borrower_id) : null,
        category: form.category, description: form.description,
        assigned_staff_id: form.assigned_staff_id ? Number(form.assigned_staff_id) : null,
      }});
      setCreating(false); setForm({ borrower_id: "", category: "other", description: "", assigned_staff_id: "" });
      load();
    } catch (e2) { setErr(e2.detail); }
  };

  const update = async (e) => {
    e.preventDefault(); setErr("");
    try {
      await api(`/api/v1/complaints/${detail.id}`, { method: "PATCH", body: {
        status: detail.status, remedial_action: detail.remedial_action || null,
        assigned_staff_id: detail.assigned_staff_id ? Number(detail.assigned_staff_id) : null,
      }});
      setDetail(null); load();
    } catch (e2) { setErr(e2.detail); }
  };

  return (
    <div>
      <PageHeader title="Complaints Registry" crumbs={["Engagement", "Complaints"]}
        actions={<button className="btn-primary" onClick={() => setCreating(true)}>+ Log Complaint</button>} />

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-5">
          <KpiCard label="Total tickets" value={stats.total} />
          <KpiCard label="Open" value={stats.open} tone={stats.open ? "warn" : "default"} />
          <KpiCard label="Resolved" value={stats.resolved} tone="good" />
          <KpiCard label="SLA breached" value={stats.breached} tone={stats.breached ? "bad" : "good"} />
          <KpiCard label="Avg resolution" value={`${stats.avg_resolution_days}d`} sub="14-day CBK SLA" />
          <KpiCard label="Within SLA" value={`${stats.within_sla_pct}%`} tone={stats.within_sla_pct >= 90 ? "good" : "warn"} />
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="p-3 border-b border-border flex flex-wrap gap-2">
          <select className="input !w-auto" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">All statuses</option>
            {meta.statuses.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
          </select>
          <select className="input !w-auto" value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }}>
            <option value="">All categories</option>
            {meta.categories.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
          </select>
        </div>
        {!data ? <Spinner /> : data.items.length === 0 ? <Empty /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Ticket</th><th className="th">Client</th><th className="th">Category</th>
                <th className="th">Status</th><th className="th">SLA (14 days)</th><th className="th">Assigned</th><th className="th"></th>
              </tr></thead>
              <tbody>{data.items.map((c) => (
                <tr key={c.id} className="hover:bg-canvas/60">
                  <td className="td font-mono text-xs font-semibold">{c.ticket_id}</td>
                  <td className="td">{c.borrower_name || "Anonymous"}</td>
                  <td className="td capitalize">{c.category.replace(/_/g, " ")}</td>
                  <td className="td"><Badge value={c.status} /></td>
                  <td className="td"><SlaChip c={c} /></td>
                  <td className="td">{c.assigned_staff_name || "—"}</td>
                  <td className="td text-right">
                    <button className="btn-ghost !py-1 !px-2.5 text-xs" onClick={() => setDetail({ ...c })}>Manage</button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        {data && <Pagination page={page} total={data.total} onPage={setPage} />}
      </div>

      {creating && (
        <Modal title="Log New Complaint" onClose={() => setCreating(false)}>
          <form onSubmit={create} className="space-y-3">
            {err && <div className="text-sm text-red-600 bg-red-50 rounded-lg p-2">{err}</div>}
            <div><label className="label">Client (optional)</label>
              <select className="input" value={form.borrower_id} onChange={(e) => setForm({ ...form, borrower_id: e.target.value })}>
                <option value="">Anonymous / walk-in</option>
                {borrowers.map((b) => <option key={b.id} value={b.id}>{b.full_name} — {b.phone}</option>)}
              </select></div>
            <div><label className="label">Category</label>
              <select className="input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                {meta.categories.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
              </select></div>
            <div><label className="label">Description</label>
              <textarea className="input" rows={3} required value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
            <div><label className="label">Assign to</label>
              <select className="input" value={form.assigned_staff_id} onChange={(e) => setForm({ ...form, assigned_staff_id: e.target.value })}>
                <option value="">Unassigned</option>
                {staff.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.role.replace("_", " ")})</option>)}
              </select></div>
            <p className="text-xs text-gray-400">A 14-day resolution SLA clock starts as soon as this ticket is created.</p>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn-primary">Create ticket</button>
            </div>
          </form>
        </Modal>
      )}

      {detail && (
        <Modal title={`Manage ${detail.ticket_id}`} onClose={() => setDetail(null)}>
          <form onSubmit={update} className="space-y-3">
            {err && <div className="text-sm text-red-600 bg-red-50 rounded-lg p-2">{err}</div>}
            <div className="text-sm bg-canvas rounded-lg p-3 text-gray-600">{detail.description || "No description"}</div>
            <div className="flex items-center gap-2 text-sm"><span className="text-gray-400">SLA:</span><SlaChip c={detail} /></div>
            <div><label className="label">Status</label>
              <select className="input" value={detail.status} onChange={(e) => setDetail({ ...detail, status: e.target.value })}>
                {meta.statuses.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
              </select>
              <p className="text-xs text-gray-400 mt-1">Marking resolved/closed sends a confirmation SMS to the client.</p></div>
            <div><label className="label">Assigned staff</label>
              <select className="input" value={detail.assigned_staff_id || ""} onChange={(e) => setDetail({ ...detail, assigned_staff_id: e.target.value })}>
                <option value="">Unassigned</option>
                {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select></div>
            <div><label className="label">Remedial action</label>
              <textarea className="input" rows={3} value={detail.remedial_action || ""} onChange={(e) => setDetail({ ...detail, remedial_action: e.target.value })}
                placeholder="What was done to resolve the complaint…" /></div>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setDetail(null)}>Cancel</button>
              <button className="btn-primary">Save</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
