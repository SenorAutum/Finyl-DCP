// WP15 — HR at a glance: headcount, who is on leave, contracts and probation
// ending, documents expiring, birthdays, last payroll, statutory amounts owed.
import { Link } from "react-router-dom";
import { KpiCard, PageHeader, Spinner } from "../../components/ui";
import { fmtDate } from "../../lib/api";
import { ErrorNote, HrTabs, TYPE, kes, useLoad } from "./common";

function List({ title, rows, render, empty }) {
  return (
    <div className="card p-4">
      <div className="font-bold mb-2">{title}</div>
      {!rows?.length ? <div className="text-sm text-gray-400">{empty}</div> : <ul className="text-sm space-y-1">{rows.map(render)}</ul>}
    </div>
  );
}

export default function HrOverview() {
  const { data: d, error, loading } = useLoad("/api/v1/hr/overview");
  return (
    <div>
      <PageHeader title="Human resources" crumbs={["HR", "Overview"]} />
      <HrTabs />
      <ErrorNote text={error} />
      {loading && !d ? <Spinner /> : d && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
            <KpiCard label="People employed" value={d.headcount} sub={`${d.left} have left`} />
            <KpiCard label="Monthly basic pay" value={kes(d.monthly_basic)} />
            <KpiCard label="On leave today" value={d.on_leave_today.length} sub={`${d.pending_leave} requests waiting`} tone={d.pending_leave ? "warning" : "default"} />
            <KpiCard label="Last payroll" value={d.last_payroll ? kes(d.last_payroll.totals.net) : "—"}
              sub={d.last_payroll ? `${fmtDate(d.last_payroll.period).replace(/^\d+ /, "")} · ${d.last_payroll.status}` : "none yet"} />
          </div>
          <div className="grid md:grid-cols-3 gap-4 mb-4">
            <List title="On leave today" rows={d.on_leave_today} empty="Nobody"
              render={(x) => <li key={x.id}><Link className="hover:underline" to={`/hr/employees/${x.id}`}>{x.name}</Link> <span className="text-gray-400">until {fmtDate(x.until)}</span></li>} />
            <List title="Contracts ending in 60 days" rows={d.contracts_ending} empty="None"
              render={(x) => <li key={x.id}><Link className="hover:underline" to={`/hr/employees/${x.id}`}>{x.name}</Link> <span className="text-amber-600">{fmtDate(x.date)}</span></li>} />
            <List title="Probation ending in 30 days" rows={d.probation_ending} empty="None"
              render={(x) => <li key={x.id}><Link className="hover:underline" to={`/hr/employees/${x.id}`}>{x.name}</Link> <span className="text-gray-500">{fmtDate(x.date)}</span></li>} />
            <List title="Documents expiring" rows={d.documents_expiring} empty="None"
              render={(x, i) => <li key={i}>{x.name}: {x.doc_type.replace("_", " ")} <span className="text-amber-600">{fmtDate(x.expires_on)}</span></li>} />
            <List title="Birthdays this month" rows={d.birthdays} empty="None"
              render={(x) => <li key={x.id}>🎂 {x.name} <span className="text-gray-500">on the {x.day}</span></li>} />
            {d.statutory && <List title="Statutory amounts owed" rows={d.statutory.filter((s) => s.owed)} empty="Nothing owed"
              render={(x) => <li key={x.key} className="flex justify-between"><span>{x.label}</span><b className="tabnums">{kes(x.owed)}</b></li>} />}
          </div>
          <div className="grid md:grid-cols-3 gap-4">
            {[["By branch", d.by_branch], ["By department", d.by_department], ["By type", Object.fromEntries(Object.entries(d.by_type).map(([k, v]) => [TYPE[k] || k, v]))]].map(([t, m]) => (
              <div key={t} className="card p-4"><div className="font-bold mb-2">{t}</div>
                <table className="w-full text-sm"><tbody>{Object.entries(m).map(([k, v]) => <tr key={k}><td className="py-0.5">{k}</td><td className="text-right font-semibold">{v}</td></tr>)}</tbody></table>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
