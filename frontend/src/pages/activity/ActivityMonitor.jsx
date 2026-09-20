// Staff activity monitor — audit-style event log stream plus captured action
// screenshots. Admins filter by officer and date range; both feeds are paginated.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Empty, Pagination, PageHeader, Spinner } from "../../components/ui";

const today = () => new Date().toISOString().slice(0, 10);
const fmtTime = (d) => (d ? new Date(d).toLocaleString("en-KE", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short", year: "numeric" }) : "—");

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

function Filters({ staff, userId, setUserId, from, setFrom, to, setTo, extra }) {
  return (
    <div className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
      <div className="min-w-[200px]">
        <label className="label">Officer</label>
        <select className="input" value={userId} onChange={(e) => setUserId(e.target.value)}>
          <option value="">All officers</option>
          {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <div className="min-w-[150px]">
        <label className="label">From</label>
        <input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
      </div>
      <div className="min-w-[150px]">
        <label className="label">To</label>
        <input className="input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
      </div>
      {extra}
    </div>
  );
}

// ---------------------------------------------------------------- Logs tab
function LogsTab({ staff, staffMap }) {
  const [userId, setUserId] = useState("");
  const [eventType, setEventType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => { setPage(1); }, [userId, eventType, from, to]);

  useEffect(() => {
    setData(null); setErr("");
    const qs = new URLSearchParams({ page: String(page), page_size: "50" });
    if (userId) qs.set("user_id", userId);
    if (eventType) qs.set("event_type", eventType);
    if (from) qs.set("date_from", from);
    if (to) qs.set("date_to", to);
    api(`/api/v1/activity/logs?${qs.toString()}`).then(setData).catch((e) => setErr(e.detail));
  }, [userId, eventType, from, to, page]);

  return (
    <div>
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      <Filters staff={staff} userId={userId} setUserId={setUserId} from={from} setFrom={setFrom} to={to} setTo={setTo}
        extra={
          <div className="min-w-[180px] flex-1">
            <label className="label">Event type</label>
            <input className="input" value={eventType} onChange={(e) => setEventType(e.target.value)} placeholder="e.g. login, page_view" />
          </div>
        } />

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-border font-bold text-base">Activity Log</div>
        {!data ? <Spinner /> : (data.items || []).length === 0 ? <Empty text="No activity events found." /> : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr>
                  <th className="th">Time</th><th className="th">Officer</th><th className="th">Event</th>
                  <th className="th">Detail</th><th className="th">IP</th><th className="th">Device</th>
                </tr></thead>
                <tbody>
                  {data.items.map((r) => (
                    <tr key={r.id} className="border-t border-border">
                      <td className="td whitespace-nowrap">{fmtTime(r.recorded_at)}</td>
                      <td className="td font-semibold">{staffMap[r.user_id] || `User ${r.user_id}`}</td>
                      <td className="td"><span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-blue-100 text-blue-700 capitalize">{(r.event_type || "").replace(/_/g, " ")}</span></td>
                      <td className="td max-w-[320px] truncate" title={r.event_detail || ""}>{r.event_detail || "—"}</td>
                      <td className="td tabnums">{r.ip || "—"}</td>
                      <td className="td max-w-[140px] truncate" title={r.device_fingerprint || ""}>{r.device_fingerprint || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={data.page || page} total={data.total || 0} pageSize={50} onPage={setPage} />
          </>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------- Screenshots tab
function ScreenshotsTab({ staff, staffMap }) {
  const [userId, setUserId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => { setPage(1); }, [userId, from, to]);

  useEffect(() => {
    setData(null); setErr("");
    const qs = new URLSearchParams({ page: String(page), page_size: "24" });
    if (userId) qs.set("user_id", userId);
    if (from) qs.set("date_from", from);
    if (to) qs.set("date_to", to);
    api(`/api/v1/activity/screenshots?${qs.toString()}`).then(setData).catch((e) => setErr(e.detail));
  }, [userId, from, to, page]);

  return (
    <div>
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      <Filters staff={staff} userId={userId} setUserId={setUserId} from={from} setFrom={setFrom} to={to} setTo={setTo} />

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-border font-bold text-base">Captured Screenshots</div>
        {!data ? <Spinner /> : (data.items || []).length === 0 ? <Empty text="No screenshots captured." /> : (
          <>
            <div className="p-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
              {data.items.map((s) => (
                <div key={s.id} className="rounded-xl border border-border overflow-hidden bg-surface2">
                  <div className="aspect-video bg-canvas flex items-center justify-center text-3xl text-gray-300">🖼</div>
                  <div className="p-3">
                    <div className="text-xs font-semibold truncate">{staffMap[s.user_id] || `User ${s.user_id}`}</div>
                    <div className="text-[11px] text-gray-400 mt-0.5">{fmtTime(s.recorded_at)}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {s.capture_trigger && <span className="inline-flex px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-gray-200 text-gray-600 capitalize">{s.capture_trigger.replace(/_/g, " ")}</span>}
                      {s.event_type && <span className="inline-flex px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-blue-100 text-blue-700 capitalize">{s.event_type.replace(/_/g, " ")}</span>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <Pagination page={data.page || page} total={data.total || 0} pageSize={24} onPage={setPage} />
          </>
        )}
      </div>
    </div>
  );
}

export default function ActivityMonitor() {
  const [tab, setTab] = useState("logs");
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

  return (
    <div>
      <PageHeader title="Activity Monitor" crumbs={["Security", "Activity Monitor"]} />
      <Tabs tab={tab} setTab={setTab} tabs={[["logs", "Activity Log"], ["screenshots", "Screenshots"]]} />
      {tab === "logs" && <LogsTab staff={staff} staffMap={staffMap} />}
      {tab === "screenshots" && <ScreenshotsTab staff={staff} staffMap={staffMap} />}
    </div>
  );
}
