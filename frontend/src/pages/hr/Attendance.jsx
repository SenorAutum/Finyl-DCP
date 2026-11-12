// WP15 — attendance: the daily register and the month's summary.
import { useEffect, useState } from "react";
import { Empty, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, notify } from "../../lib/api";
import { ErrorNote, Field, HrTabs, Tabs, exportCsv, monthStr, today, useLoad } from "./common";

const ST = [["present", "Present"], ["absent", "Absent"], ["half", "Half day"], ["leave", "On leave"], ["off", "Off duty"], ["holiday", "Holiday"]];

export default function Attendance() {
  const [tab, setTab] = useState("day");
  return (
    <div>
      <PageHeader title="Attendance" crumbs={["HR", "Attendance"]} />
      <HrTabs />
      <Tabs tabs={[["day", "Daily register"], ["month", "Month summary"]]} value={tab} onChange={setTab} />
      {tab === "day" ? <Day /> : <Month />}
    </div>
  );
}

function Day() {
  const { can } = useAuth();
  const [day, setDay] = useState(today());
  const { data, error, loading, reload } = useLoad(`/api/v1/hr/attendance?day=${day}`);
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  useEffect(() => { setRows(data?.rows || []); }, [data]);
  const set = (i, k, v) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const save = async () => {
    setErr("");
    try {
      const entries = rows.filter((r) => r.status).map((r) => ({ employee_id: r.employee_id, status: r.status, clock_in: r.clock_in || null, clock_out: r.clock_out || null, note: r.note || null }));
      const r = await api("/api/v1/hr/attendance", { method: "PUT", body: { day, entries } });
      notify(`${r.saved} marked`, { tone: "success" }); reload();
    } catch (e) { setErr(e.message); }
  };
  const edit = can("hr.manage");
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap gap-3 items-end px-4 py-3 border-b border-border">
        <Field label="Day"><input type="date" className="input !w-auto" max={today()} value={day} onChange={(e) => setDay(e.target.value)} /></Field>
        <div className="text-sm text-gray-500 pb-2">{data?.weekday}</div>
        <div className="flex-1" />
        {edit && <button className="btn-ghost" onClick={() => setRows(rows.map((r) => ({ ...r, status: r.status || (r.on_leave ? "leave" : "present") })))}>Mark everyone else present</button>}
        {edit && <button className="btn-primary" onClick={save}>Save register</button>}
      </div>
      <ErrorNote text={error || err} />
      {loading && !data ? <Spinner /> : !rows.length ? <Empty text="Nobody employed on this day." /> : (
        <table className="min-w-full text-sm"><thead><tr><th className="th">Employee</th><th className="th">Branch</th><th className="th">Status</th><th className="th">In</th><th className="th">Out</th><th className="th">Note</th></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={r.employee_id}><td className="td font-semibold">{r.name}{r.on_leave && <span className="text-xs text-blue-600"> · approved leave</span>}</td><td className="td">{r.branch || "Head office"}</td>
              <td className="td"><select className="input !w-36" disabled={!edit} value={r.status || ""} onChange={(e) => set(i, "status", e.target.value)}>
                <option value="">—</option>{ST.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
              <td className="td"><input type="time" className="input !w-28" disabled={!edit} value={r.clock_in || ""} onChange={(e) => set(i, "clock_in", e.target.value)} /></td>
              <td className="td"><input type="time" className="input !w-28" disabled={!edit} value={r.clock_out || ""} onChange={(e) => set(i, "clock_out", e.target.value)} /></td>
              <td className="td"><input className="input" disabled={!edit} value={r.note || ""} onChange={(e) => set(i, "note", e.target.value)} /></td></tr>
          ))}</tbody></table>
      )}
    </div>
  );
}

function Month() {
  const [month, setMonth] = useState(monthStr());
  const { data, error, loading } = useLoad(`/api/v1/hr/attendance/summary?month=${month}`);
  return (
    <div className="card overflow-hidden">
      <div className="flex gap-3 items-end px-4 py-3 border-b border-border">
        <Field label="Month"><input type="month" className="input !w-auto" value={month} onChange={(e) => setMonth(e.target.value)} /></Field>
        <div className="text-sm text-gray-500 pb-2">{data && `${data.working_days} working days`}</div>
        <div className="flex-1" />
        {data?.rows?.length > 0 && <button className="btn-ghost" onClick={() => exportCsv(`attendance_${month}.csv`, [["Employee", "name"], ...ST.map(([k, l]) => [l, k])], data.rows)}>Export CSV</button>}
      </div>
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !data?.rows?.length ? <Empty text="No attendance marked this month." /> : (
        <table className="min-w-full text-sm"><thead><tr><th className="th">Employee</th>{ST.map(([k, l]) => <th key={k} className="th text-right">{l}</th>)}</tr></thead>
          <tbody>{data.rows.map((r) => <tr key={r.employee_id}><td className="td font-semibold">{r.name}</td>{ST.map(([k]) => <td key={k} className={`td text-right ${k === "absent" && r[k] ? "text-red-600 font-semibold" : ""}`}>{r[k] || ""}</td>)}</tr>)}</tbody></table>
      )}
    </div>
  );
}
