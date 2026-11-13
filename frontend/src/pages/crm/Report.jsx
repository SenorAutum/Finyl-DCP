// WP17 — the sales pipeline report: how many leads at each stage, how many
// became clients, how fast, and how each officer is doing.
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, KpiCard, PageHeader, Spinner } from "../../components/ui";
import { CrmTabs, ErrorNote, exportCsv, kes, num, useLoad } from "./common";

const STAGE_LABELS = { lead: "Lead", contacted: "Contacted", field_visit: "Field visit", app_setup: "App set up", disbursed: "Disbursed" };

export default function Report() {
  const [months, setMonths] = useState(6);
  const { data: r, error, loading } = useLoad(`/api/v1/crm-plus/pipeline?months=${months}`);
  return (
    <div>
      <PageHeader title="Pipeline report" crumbs={["Engagement", "CRM", "Report"]}
        actions={<select className="input !w-auto text-sm" value={months} onChange={(e) => setMonths(Number(e.target.value))} aria-label="Months">
          <option value={3}>Last 3 months</option><option value={6}>Last 6 months</option><option value={12}>Last 12 months</option></select>} />
      <CrmTabs />
      <ErrorNote text={error} />
      {loading && !r ? <Spinner /> : !r ? null : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
            <KpiCard label="Leads" value={num(r.total)} />
            <KpiCard label="Became clients" value={num(r.converted)} sub={`${r.conversion_pct}% of leads`} tone="good" />
            <KpiCard label="Average days to convert" value={r.avg_days_to_convert ?? "—"} />
            <KpiCard label="Still in the pipeline" value={kes(r.pipeline_value)} sub="estimated loans" />
          </div>
          <div className="grid lg:grid-cols-2 gap-5 mb-5">
            <div className="card p-5">
              <div className="font-bold mb-3">Leads at each stage</div>
              {r.by_stage.map((s) => {
                const max = Math.max(1, ...r.by_stage.map((x) => x.count));
                return (
                  <div key={s.stage} className="flex items-center gap-3 text-sm mb-2">
                    <span className="w-28 shrink-0">{STAGE_LABELS[s.stage] || s.stage}</span>
                    <div className="flex-1 bg-canvas rounded h-5"><div className="bg-accent h-5 rounded" style={{ width: `${(s.count / max) * 100}%` }} /></div>
                    <span className="w-10 text-right tabnums font-semibold">{s.count}</span>
                  </div>
                );
              })}
            </div>
            <div className="card p-5">
              <div className="font-bold mb-3">New leads and conversions by month</div>
              {!r.monthly.length ? <Empty text="No leads in this period." /> : (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={r.monthly}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="month" fontSize={11} /><YAxis allowDecimals={false} fontSize={11} />
                    <Tooltip /><Legend /><Bar dataKey="new" name="New leads" fill="#94a3b8" /><Bar dataKey="converted" name="Became clients" fill="#10B981" /></BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
          <div className="card overflow-hidden">
            <div className="flex px-4 py-3 border-b border-border items-center"><span className="font-bold">By officer</span><div className="flex-1" />
              <button className="btn-ghost" disabled={!r.by_officer.length} onClick={() => exportCsv("pipeline-by-officer.csv", [["Officer", "officer"], ["Leads", "leads"], ["Converted", "converted"],
                ["Conversion %", "conversion_pct"], ["Estimated value", "value"]], r.by_officer)}>Export CSV</button></div>
            {!r.by_officer.length ? <Empty text="No leads yet." /> : (
              <table className="min-w-full text-sm"><thead><tr><th className="th">Officer</th><th className="th text-right">Leads</th><th className="th text-right">Became clients</th>
                <th className="th text-right">Conversion</th><th className="th text-right">Estimated loans</th></tr></thead>
                <tbody>{r.by_officer.map((o) => (
                  <tr key={o.officer}><td className="td font-semibold">{o.officer}</td><td className="td text-right tabnums">{o.leads}</td><td className="td text-right tabnums">{o.converted}</td>
                    <td className="td text-right tabnums">{o.conversion_pct}%</td><td className="td text-right tabnums">{kes(o.value)}</td></tr>))}</tbody></table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
