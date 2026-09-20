// Site visits — planned vs actual field-visit tracking built on the daily task
// plans. Managers pick an officer; each plan lists its planned visit stops and
// how many were actually completed, with a simple completion ratio.
import { useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { Badge, Empty, KpiCard, Modal, PageHeader, Spinner } from "../../components/ui";

const today = () => new Date().toISOString().slice(0, 10);
const visitsList = (v) => (Array.isArray(v) ? v : v ? [v] : []);

export default function SiteVisits() {
  const [staff, setStaff] = useState([]);
  const [userId, setUserId] = useState("");
  const [data, setData] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ task_date: today(), planned_visits: "" });
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api("/api/v1/lending/org").then((org) => setStaff(org.staff || [])).catch(() => {});
  }, []);

  const load = () => {
    setData(null);
    api(`/api/v1/field/daily-tasks${userId ? `?user_id=${userId}` : ""}`).then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(load, [userId]);

  const create = async (e) => {
    e.preventDefault();
    setSaving(true); setErr("");
    const visits = form.planned_visits.split(",").map((v) => v.trim()).filter(Boolean);
    try {
      await api("/api/v1/field/daily-tasks", { method: "POST", body: {
        user_id: userId ? Number(userId) : undefined,
        task_date: form.task_date, planned_visits: visits,
      }});
      setCreating(false); setForm({ task_date: today(), planned_visits: "" }); load();
    } catch (ex) { setErr(ex.detail || "Could not create visit plan"); }
    finally { setSaving(false); }
  };

  const saveEdit = async (e) => {
    e.preventDefault();
    setSaving(true); setErr("");
    try {
      await api(`/api/v1/field/daily-tasks/${editing.id}`, { method: "PUT", body: {
        status: editing.status,
        actual_visits: editing.actual_visits === "" ? undefined : Number(editing.actual_visits),
      }});
      setEditing(null); load();
    } catch (ex) { setErr(ex.detail || "Could not update visit plan"); }
    finally { setSaving(false); }
  };

  const items = data?.items || [];
  const totalPlanned = items.reduce((s, t) => s + visitsList(t.planned_visits).length, 0);
  const totalActual = items.reduce((s, t) => s + (t.actual_visits || 0), 0);
  const rate = totalPlanned ? Math.round((totalActual / totalPlanned) * 100) : 0;

  return (
    <div>
      <PageHeader title="Site Visits" crumbs={["Field & Collections", "Site Visits"]} />
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="min-w-[220px] flex-1">
          <label className="label">Officer (managers only)</label>
          <select className="input" value={userId} onChange={(e) => setUserId(e.target.value)}>
            <option value="">My visit plans</option>
            {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <button className="btn-primary" onClick={() => { setForm({ task_date: today(), planned_visits: "" }); setCreating(true); }}>+ New Visit Plan</button>
      </div>

      {data && items.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-5">
          <KpiCard label="Planned visits" value={totalPlanned} icon="🗺" />
          <KpiCard label="Completed visits" value={totalActual} icon="✅" />
          <KpiCard label="Completion rate" value={`${rate}%`} tone={rate >= 80 ? "good" : rate >= 50 ? "warn" : "bad"} icon="📊" />
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-border font-bold text-base">Visit Plans</div>
        {!data ? <Spinner /> : items.length === 0 ? <Empty text="No site-visit plans found." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Date</th><th className="th">Planned Stops</th><th className="th text-right">Planned</th>
                <th className="th text-right">Actual</th><th className="th text-right">Distance (km)</th><th className="th">Status</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {items.map((t) => {
                  const planned = visitsList(t.planned_visits);
                  return (
                    <tr key={t.id} className="border-t border-border">
                      <td className="td font-semibold whitespace-nowrap">{fmtDate(t.task_date)}</td>
                      <td className="td max-w-[360px]">{planned.length ? planned.join(", ") : <span className="text-gray-400">—</span>}</td>
                      <td className="td text-right tabnums">{planned.length}</td>
                      <td className="td text-right tabnums">{t.actual_visits ?? 0}</td>
                      <td className="td text-right tabnums">{t.distance_km ?? "—"}</td>
                      <td className="td"><Badge value={t.status === "completed" ? "resolved" : t.status === "active" ? "in_progress" : "pending"}>{t.status}</Badge></td>
                      <td className="td text-right">
                        <button className="btn-ghost !py-1 !px-2.5 text-xs"
                          onClick={() => setEditing({ ...t, actual_visits: t.actual_visits ?? "" })}>Update</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating && (
        <Modal title="New Visit Plan" onClose={() => setCreating(false)}>
          <form onSubmit={create} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <div><label className="label">Visit date</label>
              <input className="input" type="date" value={form.task_date} onChange={(e) => setForm((p) => ({ ...p, task_date: e.target.value }))} required /></div>
            <div><label className="label">Planned visit stops</label>
              <textarea className="input" rows={3} value={form.planned_visits}
                onChange={(e) => setForm((p) => ({ ...p, planned_visits: e.target.value }))}
                placeholder="Comma-separated, e.g. Client 12, Client 34, Market run" />
              <p className="text-[11px] text-gray-400 mt-1">Separate each planned visit with a comma.</p></div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn-primary" disabled={saving}>{saving ? "Saving…" : "Create plan"}</button>
            </div>
          </form>
        </Modal>
      )}

      {editing && (
        <Modal title={`Update Visit Plan · ${fmtDate(editing.task_date)}`} onClose={() => setEditing(null)}>
          <form onSubmit={saveEdit} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <div><label className="label">Status</label>
              <select className="input" value={editing.status} onChange={(e) => setEditing((p) => ({ ...p, status: e.target.value }))}>
                {["planned", "active", "completed"].map((s) => <option key={s} value={s} className="capitalize">{s}</option>)}
              </select></div>
            <div><label className="label">Actual visits completed</label>
              <input className="input" type="number" value={editing.actual_visits}
                onChange={(e) => setEditing((p) => ({ ...p, actual_visits: e.target.value }))} /></div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn-primary" disabled={saving}>{saving ? "Saving…" : "Save update"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
