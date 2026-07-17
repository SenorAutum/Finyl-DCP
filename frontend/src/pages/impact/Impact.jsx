// Social Impact suite: investor dashboard (impact → financial KPIs by age group),
// impact survey registry, and the P2P mentorship pairing engine.
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, fmtDate, fmtKES } from "../../lib/api";
import { Empty, KpiCard, PageHeader, Pagination, Spinner } from "../../components/ui";

function Investor() {
  const [data, setData] = useState(null);
  useEffect(() => { api("/api/v1/impact/investor-dashboard").then(setData).catch(() => {}); }, []);
  if (!data) return <Spinner />;
  const t = data.totals || {};
  return (
    <div className="p-4 space-y-5">
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        <KpiCard label="Jobs created" value={t.total_jobs_created ?? 0} tone="good" sub="Across surveyed borrowers" />
        <KpiCard label="Sales improved" value={`${t.pct_sales_improved ?? 0}%`} sub="Borrowers reporting growth" />
        <KpiCard label="Repeat retention" value={`${t.repeat_borrower_retention_pct ?? 0}%`} sub="Borrowers on 2nd+ cycle" />
        <KpiCard label="Surveys collected" value={t.surveys_collected ?? 0} sub="Forced on repeat cycles" />
      </div>
      <div className="card p-4">
        <h4 className="text-sm font-bold mb-1">Impact by Borrower Age Group</h4>
        <p className="text-xs text-gray-400 mb-3">Revenue growth % and jobs created, segmented for investor reporting.</p>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={data.age_groups} margin={{ left: -15 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
            <XAxis dataKey="group" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="revenue_growth_pct" name="Revenue growth %" fill="#10B981" radius={[6, 6, 0, 0]} />
            <Bar dataKey="jobs_created" name="Jobs created" fill="#0D9488" radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="overflow-x-auto card">
        <table className="w-full text-sm">
          <thead><tr>
            <th className="th">Age group</th><th className="th">Surveys</th>
            <th className="th">Revenue growth</th><th className="th">Jobs created</th>
          </tr></thead>
          <tbody>{(data.age_groups || []).map((g) => (
            <tr key={g.group} className="hover:bg-canvas/60">
              <td className="td font-semibold">{g.group}</td>
              <td className="td">{g.surveys}</td>
              <td className="td"><span className={g.revenue_growth_pct >= 0 ? "text-accent font-bold" : "text-red-600 font-bold"}>{g.revenue_growth_pct}%</span></td>
              <td className="td">{g.jobs_created}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

function Surveys() {
  const [data, setData] = useState(null);
  const [page, setPage] = useState(1);
  useEffect(() => { api(`/api/v1/impact/surveys?page=${page}`).then(setData).catch(() => {}); }, [page]);
  if (!data) return <Spinner />;
  return (
    <>
      {data.items.length === 0 ? <Empty /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr>
              <th className="th">Survey</th><th className="th">Borrower</th><th className="th">Cycle</th>
              <th className="th">Sales before</th><th className="th">Sales now</th><th className="th">Growth</th>
              <th className="th">Jobs</th><th className="th">Date</th>
            </tr></thead>
            <tbody>{data.items.map((s) => {
              const growth = s.monthly_sales_pre ? Math.round(1000 * (s.monthly_sales_post - s.monthly_sales_pre) / s.monthly_sales_pre) / 10 : 0;
              return (
                <tr key={s.id} className="hover:bg-canvas/60">
                  <td className="td font-mono text-xs font-semibold">{s.survey_id}</td>
                  <td className="td">{s.borrower_name}</td>
                  <td className="td">#{s.loan_cycle_number}</td>
                  <td className="td">{fmtKES(s.monthly_sales_pre)}</td>
                  <td className="td">{fmtKES(s.monthly_sales_post)}</td>
                  <td className="td"><span className={growth >= 0 ? "text-accent font-bold" : "text-red-600 font-bold"}>{growth > 0 ? "+" : ""}{growth}%</span></td>
                  <td className="td">{s.jobs_created}</td>
                  <td className="td">{fmtDate(s.survey_date)}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      )}
      <Pagination page={page} total={data.total} onPage={setPage} />
    </>
  );
}

function Mentorship() {
  const [pairs, setPairs] = useState(null);
  useEffect(() => { api("/api/v1/impact/mentorship-pairings").then(setPairs).catch(() => {}); }, []);
  if (!pairs) return <Spinner />;
  if (pairs.length === 0) return <Empty text="No eligible mentor/mentee matches yet" />;
  return (
    <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
      {pairs.map((p, i) => (
        <div key={i} className="border border-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold uppercase tracking-wider text-gray-400">Suggested pairing</span>
            <span className="text-xs font-bold text-accent bg-emerald-50 px-2 py-0.5 rounded-full">Match score {p.match_score}</span>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex-1 bg-emerald-50/70 rounded-lg p-3">
              <div className="text-[10px] font-bold uppercase text-accent">Mentor · veteran</div>
              <div className="font-semibold text-sm mt-0.5">{p.mentor.name}</div>
              <div className="text-xs text-gray-500 capitalize">{p.mentor.business_sector} · {p.mentor.region_name}</div>
              <div className="text-xs text-gray-500 mt-1">{p.mentor.completed_cycles} cycles · {p.mentor.repayment_rate}% repay · +{p.mentor.sales_growth_pct}% sales</div>
            </div>
            <div className="text-xl text-gray-300">→</div>
            <div className="flex-1 bg-canvas rounded-lg p-3">
              <div className="text-[10px] font-bold uppercase text-gray-400">Mentee · rookie</div>
              <div className="font-semibold text-sm mt-0.5">{p.mentee.name}</div>
              <div className="text-xs text-gray-500 capitalize">{p.mentee.business_sector} · {p.mentee.region_name}</div>
              <div className="text-xs text-gray-500 mt-1">Cycle #{p.mentee.max_cycle}</div>
            </div>
          </div>
          <ul className="mt-3 space-y-1">
            {p.reasons.map((r, j) => (
              <li key={j} className="text-xs text-gray-500 flex gap-1.5"><span className="text-accent">✓</span>{r}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export default function Impact() {
  const [tab, setTab] = useState("investor");
  return (
    <div>
      <PageHeader title="Impact & Investors" crumbs={["Engagement", "Impact"]} />
      <div className="card overflow-hidden">
        <div className="flex border-b border-border overflow-x-auto">
          {[["investor", "Investor Dashboard"], ["surveys", "Impact Surveys"], ["mentorship", "P2P Mentorship"]].map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)}
              className={`px-5 py-3 text-sm font-semibold whitespace-nowrap border-b-2 -mb-px ${tab === k ? "border-accent text-accent" : "border-transparent text-gray-400 hover:text-charcoal"}`}>
              {label}
            </button>
          ))}
        </div>
        {tab === "investor" && <Investor />}
        {tab === "surveys" && <Surveys />}
        {tab === "mentorship" && <Mentorship />}
      </div>
    </div>
  );
}
