// GPS tracking — per-officer daily GPS trail (coordinate table + distance KPI)
// alongside that day's planned/actual task summary. No map dependency; a plain
// coordinate table keeps the view lightweight and dependency-free.
import { useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { Empty, KpiCard, PageHeader, Spinner } from "../../components/ui";

const today = () => new Date().toISOString().slice(0, 10);
const fmtTime = (d) => (d ? new Date(d).toLocaleString("en-KE", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" }) : "—");

export default function GpsTracker() {
  const [staff, setStaff] = useState([]);
  const [userId, setUserId] = useState("");
  const [day, setDay] = useState(today());
  const [trail, setTrail] = useState(null);
  const [tasks, setTasks] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api("/api/v1/lending/org").then((org) => setStaff(org.staff || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (!userId) { setTrail(null); setTasks(null); return; }
    setTrail(null); setTasks(null); setErr("");
    api(`/api/v1/field/gps-trail?user_id=${userId}&day=${day}`).then(setTrail).catch((e) => setErr(e.detail));
    api(`/api/v1/field/daily-tasks?user_id=${userId}`).then(setTasks).catch(() => setTasks({ items: [] }));
  }, [userId, day]);

  const dayTask = (tasks?.items || []).find((t) => (t.task_date || "").slice(0, 10) === day);

  return (
    <div>
      <PageHeader title="GPS Tracking" crumbs={["Field & Collections", "GPS Tracking"]} />
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
      ) : !trail ? <Spinner /> : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-5">
            <KpiCard label="Distance covered" value={`${trail.distance_km ?? 0} km`} icon="🛣" />
            <KpiCard label="Ping points" value={(trail.points || []).length} icon="📍" />
            <KpiCard label="Planned visits" value={dayTask ? (Array.isArray(dayTask.planned_visits) ? dayTask.planned_visits.length : 0) : "—"} icon="🗺" />
            <KpiCard label="Actual visits" value={dayTask ? (dayTask.actual_visits ?? 0) : "—"} icon="✅" />
          </div>

          <div className="card overflow-hidden">
            <div className="px-5 py-3 border-b border-border font-bold text-base">Coordinate Trail · {fmtDate(trail.date || day)}</div>
            {(trail.points || []).length === 0 ? <Empty text="No GPS pings recorded for this day." /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr>
                    <th className="th">#</th><th className="th">Time</th><th className="th">Latitude</th>
                    <th className="th">Longitude</th><th className="th">Accuracy (m)</th><th className="th">Task</th>
                  </tr></thead>
                  <tbody>
                    {trail.points.map((p, i) => (
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
