// Field operations — officer GPS trails (coordinate table, no map dependency),
// daily visit-plan tasks and geo-fence location alerts. Managers pick an officer
// from the org directory; officers see their own tasks by default.
import { useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { Badge, Empty, KpiCard, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

const today = () => new Date().toISOString().slice(0, 10);
const fmtTime = (d) => (d ? new Date(d).toLocaleString("en-KE", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" }) : "—");
const visitsList = (v) => (Array.isArray(v) ? v : v ? [v] : []);

function Tabs({ tab, setTab, tabs }) {
  return (
    <div className="flex gap-1 border-b border-border mb-5">
      {tabs.map(([key, label]) => (
        <button key={key} onClick={() => setTab(key)}
          className={`px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition-colors ${
            tab === key ? "border-accent text-accent" : "border-transparent text-gray-500 hover:text-charcoal"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

// -------------------------------------------------------------- GPS trail tab
function GpsTrailTab({ staff }) {
  const [userId, setUserId] = useState("");
  const [day, setDay] = useState(today());
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!userId) { setData(null); return; }
    setData(null);
    api(`/api/v1/field/gps-trail?user_id=${userId}&day=${day}`).then(setData).catch((e) => setErr(e.detail));
  }, [userId, day]);

  return (
    <div>
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      <div className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="min-w-[220px]">
          <label className="label">Officer</label>
          <select className="input" value={userId} onChange={(e) => setUserId(e.target.value)}>
            <option value="">Select an officer…</option>
            {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <div className="min-w-[160px]">
          <label className="label">Day</label>
          <input className="input" type="date" value={day} onChange={(e) => setDay(e.target.value)} />
        </div>
      </div>

      {!userId ? (
        <div className="card"><Empty text="Select an officer to view their GPS trail." /></div>
      ) : !data ? <Spinner /> : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-5">
            <KpiCard label="Distance covered" value={`${data.distance_km ?? 0} km`} icon="🛣" />
            <KpiCard label="Ping points" value={(data.points || []).length} icon="📍" />
            <KpiCard label="Date" value={fmtDate(data.date)} icon="📅" />
          </div>
          <div className="card overflow-hidden">
            <div className="px-5 py-3 border-b border-border font-bold text-base">Coordinate Trail</div>
            {(data.points || []).length === 0 ? <Empty text="No GPS pings recorded for this day." /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr>
                    <th className="th">#</th><th className="th">Time</th><th className="th">Latitude</th>
                    <th className="th">Longitude</th><th className="th">Accuracy (m)</th><th className="th">Task</th>
                  </tr></thead>
                  <tbody>
                    {data.points.map((p, i) => (
                      <tr key={i} className="border-t border-border">
                        <td className="td tabnums">{i + 1}</td>
                        <td className="td">{fmtTime(p.timestamp)}</td>
                        <td className="td tabnums">{p.lat?.toFixed(6)}</td>
                        <td className="td tabnums">{p.lng?.toFixed(6)}</td>
                        <td className="td tabnums">{p.accuracy_meters != null ? p.accuracy_meters : "—"}</td>
                        <td className="td capitalize">{(p.task_type || "—").replace(/_/g, " ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------- Daily tasks tab
function DailyTasksTab({ staff }) {
  const [userId, setUserId] = useState("");
  const [data, setData] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ task_date: today(), planned_visits: "" });
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

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
    } catch (ex) { setErr(ex.detail || "Could not create task"); }
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
    } catch (ex) { setErr(ex.detail || "Could not update task"); }
    finally { setSaving(false); }
  };

  return (
    <div>
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      <div className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="min-w-[220px] flex-1">
          <label className="label">Officer (managers only)</label>
          <select className="input" value={userId} onChange={(e) => setUserId(e.target.value)}>
            <option value="">My tasks</option>
            {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <button className="btn-primary" onClick={() => { setForm({ task_date: today(), planned_visits: "" }); setCreating(true); }}>+ New Task Plan</button>
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-border font-bold text-base">Daily Task Plans</div>
        {!data ? <Spinner /> : data.items.length === 0 ? <Empty text="No daily tasks." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Date</th><th className="th">Planned Visits</th><th className="th text-right">Actual</th>
                <th className="th text-right">Distance (km)</th><th className="th text-right">Stipend</th><th className="th">Status</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {data.items.map((t) => {
                  const planned = visitsList(t.planned_visits);
                  return (
                    <tr key={t.id} className="border-t border-border">
                      <td className="td font-semibold">{fmtDate(t.task_date)}</td>
                      <td className="td">{planned.length} planned{planned.length ? <span className="text-gray-400"> · {planned.join(", ")}</span> : null}</td>
                      <td className="td text-right tabnums">{t.actual_visits ?? 0}</td>
                      <td className="td text-right tabnums">{t.distance_km ?? "—"}</td>
                      <td className="td text-right tabnums">{t.stipend_kes != null ? `KES ${t.stipend_kes}` : "—"}</td>
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
        <Modal title="New Daily Task Plan" onClose={() => setCreating(false)}>
          <form onSubmit={create} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <div><label className="label">Task date</label>
              <input className="input" type="date" value={form.task_date} onChange={(e) => setForm((p) => ({ ...p, task_date: e.target.value }))} required /></div>
            <div><label className="label">Planned visits</label>
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
        <Modal title={`Update Task · ${fmtDate(editing.task_date)}`} onClose={() => setEditing(null)}>
          <form onSubmit={saveEdit} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <div><label className="label">Status</label>
              <select className="input" value={editing.status} onChange={(e) => setEditing((p) => ({ ...p, status: e.target.value }))}>
                {["planned", "active", "completed"].map((s) => <option key={s} value={s} className="capitalize">{s}</option>)}
              </select></div>
            <div><label className="label">Actual visits</label>
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

// ---------------------------------------------------------- Location alerts tab
function LocationAlertsTab({ staffMap }) {
  const [ack, setAck] = useState("false"); // "", "true", "false"
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(0);
  const [err, setErr] = useState("");

  const load = () => {
    setData(null);
    const qs = ack === "" ? "" : `?acknowledged=${ack}`;
    api(`/api/v1/field/location-alerts${qs}`).then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(load, [ack]);

  const acknowledge = async (id) => {
    setBusy(id); setErr("");
    try { await api(`/api/v1/field/location-alerts/${id}/acknowledge`, { method: "POST", body: {} }); load(); }
    catch (ex) { setErr(ex.detail || "Could not acknowledge"); }
    finally { setBusy(0); }
  };

  return (
    <div>
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      <div className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="min-w-[180px]">
          <label className="label">Filter</label>
          <select className="input" value={ack} onChange={(e) => setAck(e.target.value)}>
            <option value="false">Unacknowledged</option>
            <option value="true">Acknowledged</option>
            <option value="">All</option>
          </select>
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-border font-bold text-base">Location Alerts</div>
        {!data ? <Spinner /> : data.items.length === 0 ? <Empty text="No location alerts." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Officer</th><th className="th">Type</th><th className="th">Detail</th>
                <th className="th">Triggered</th><th className="th">Acknowledged</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id} className="border-t border-border">
                    <td className="td font-semibold">{staffMap[a.user_id] || `User ${a.user_id}`}</td>
                    <td className="td"><span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-amber-100 text-amber-700 capitalize">{(a.alert_type || "").replace(/_/g, " ")}</span></td>
                    <td className="td">{a.detail || "—"}</td>
                    <td className="td">{fmtTime(a.triggered_at)}</td>
                    <td className="td">{a.acknowledged_at ? fmtTime(a.acknowledged_at) : "—"}</td>
                    <td className="td text-right">
                      {!a.acknowledged_at && (
                        <button className="btn-ghost !py-1 !px-2.5 text-xs" disabled={busy === a.id}
                          onClick={() => acknowledge(a.id)}>{busy === a.id ? "…" : "Acknowledge"}</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

export default function FieldOps() {
  const { can } = useAuth();
  const [tab, setTab] = useState("gps");
  const [staff, setStaff] = useState([]);
  const [staffMap, setStaffMap] = useState({});

  useEffect(() => {
    api("/api/v1/lending/org").then((org) => {
      const list = org.staff || [];
      setStaff(list);
      const m = {}; list.forEach((s) => { m[s.id] = s.name; });
      setStaffMap(m);
    }).catch(() => {});
  }, []);

  const tabs = [["gps", "GPS Trail"], ["tasks", "Daily Tasks"]];
  if (can("field_ops.gps_view")) tabs.push(["alerts", "Location Alerts"]);

  return (
    <div>
      <PageHeader title="Field Operations" crumbs={["Field & Collections", "Field Ops"]} />
      <Tabs tab={tab} setTab={setTab} tabs={tabs} />
      {tab === "gps" && <GpsTrailTab staff={staff} />}
      {tab === "tasks" && <DailyTasksTab staff={staff} />}
      {tab === "alerts" && <LocationAlertsTab staffMap={staffMap} />}
    </div>
  );
}
