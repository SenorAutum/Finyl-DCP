// Executive surface — headline portfolio KPIs, officer net-margin rankings,
// per-product performance and the CRM lead-conversion funnel. All figures are
// scoped server-side to the caller's data scope.
import { useEffect, useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, fmtKES } from "../../lib/api";
import { Empty, KpiCard, PageHeader, Skeleton } from "../../components/ui";

export default function ExecutiveDashboard() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api("/api/v1/dashboard/executive").then(setData).catch((e) => setErr(e.detail));
  }, []);

  const netMarginTotal = useMemo(
    () => (data?.officer_rankings || []).reduce((a, o) => a + (o.net_margin || 0), 0),
    [data]);

  if (err) return <div className="card p-6 text-sm text-red-600">{err}</div>;

  const k = data?.kpis;
  const lc = data?.lead_conversion;

  return (
    <div>
      <PageHeader title="Executive Dashboard" crumbs={["Dashboard", "Executive"]} />

      {!data ? (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="card p-4"><Skeleton className="h-3 w-20" /><Skeleton className="h-7 w-28 mt-3" /><Skeleton className="h-3 w-24 mt-2" /></div>
            ))}
          </div>
          <div className="card p-4"><Skeleton className="h-4 w-48" /><Skeleton className="h-56 w-full mt-4" /></div>
        </>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            <KpiCard label="PAR 30" value={`${k.par_30}%`} icon="⏱" tone={k.par_30 > 10 ? "bad" : k.par_30 > 5 ? "warn" : "good"} sub="> 30 days overdue" />
            <KpiCard label="PAR 90" value={`${k.par_90}%`} icon="🚩" tone={k.par_90 > 5 ? "bad" : "warn"} sub="> 90 days overdue" />
            <KpiCard label="Net Margin" value={fmtKES(netMarginTotal)} icon="📊" tone={netMarginTotal >= 0 ? "good" : "bad"} sub="Sum of ranked officers" />
            <KpiCard label="Repayment Rate" value={`${k.repayment_rate}%`} icon="✅" tone={k.repayment_rate >= 90 ? "good" : k.repayment_rate >= 70 ? "warn" : "bad"} sub="Collected vs expected" />
            <KpiCard label="Total Disbursed" value={fmtKES(k.disbursement_volume)} icon="💸" variant="hero" sub="Cumulative principal" />
            <KpiCard label="Active Portfolio" value={fmtKES(k.total_outstanding)} icon="🏦" variant="hero" sub={`${k.overdue_loans} overdue loans`} />
            <KpiCard label="Yield on Portfolio" value={`${k.yield_on_portfolio}%`} icon="📈" tone="good" sub="Interest income / outstanding" />
            <KpiCard label="Active Loans" value={k.active_loans} icon="📋" sub={`${fmtKES(k.total_collected)} collected`} />
          </div>

          {/* Officer rankings */}
          <div className="grid lg:grid-cols-2 gap-4 mb-5">
            <div className="card p-4 overflow-x-auto">
              <h3 className="font-bold mb-1">Officer Net-Margin Ranking</h3>
              <p className="text-xs text-gray-400 mb-3">Top officers by net margin (interest recovered − cost − defaults).</p>
              {(data.officer_rankings || []).length === 0 ? <Empty /> : (
                <table className="min-w-full text-sm">
                  <thead><tr><th className="th">#</th><th className="th">Officer</th><th className="th">Loans</th><th className="th text-right">Interest Rec.</th><th className="th text-right">Defaults</th><th className="th text-right">Net Margin</th></tr></thead>
                  <tbody>
                    {data.officer_rankings.map((o, i) => {
                      const medal = i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : null;
                      return (
                        <tr key={o.staff_id} className={`border-t border-border ${i < 3 ? "bg-emerald-50/40" : ""}`}>
                          <td className="td font-bold tabnums">{medal ? <span className="text-base">{medal}</span> : (o.rank ?? i + 1)}</td>
                          <td className="td"><div className="font-semibold">{o.name}</div><div className="text-[11px] text-gray-400">{o.branch || o.role}</div></td>
                          <td className="td tabnums">{o.loans_managed}</td>
                          <td className="td text-right tabnums">{fmtKES(o.interest_recovered)}</td>
                          <td className="td text-right tabnums text-red-600">{fmtKES(o.defaulted_principal)}</td>
                          <td className={`td text-right font-bold tabnums ${o.net_margin >= 0 ? "text-accent" : "text-red-600"}`}>{fmtKES(o.net_margin)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
            <div className="card p-4">
              <h3 className="font-bold mb-3">Net Margin by Officer (KES)</h3>
              {(data.officer_rankings || []).length === 0 ? <Empty /> : (
                <ResponsiveContainer width="100%" height={Math.max(260, data.officer_rankings.length * 26)}>
                  <BarChart data={data.officer_rankings} layout="vertical" margin={{ left: 30 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} axisLine={false} tickLine={false} />
                    <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
                    <Tooltip formatter={(v) => fmtKES(v)} cursor={{ fill: "rgba(16,185,129,0.06)" }} />
                    <Bar dataKey="net_margin" name="Net margin" radius={[0, 4, 4, 0]}>
                      {data.officer_rankings.map((o) => <Cell key={o.staff_id} fill={o.net_margin >= 0 ? "#10B981" : "#EF4444"} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* Product performance + lead conversion */}
          <div className="grid lg:grid-cols-3 gap-4">
            <div className="card p-4 lg:col-span-2 overflow-x-auto">
              <h3 className="font-bold mb-3">Product Performance</h3>
              {(data.product_performance || []).length === 0 ? <Empty /> : (
                <table className="min-w-full text-sm">
                  <thead><tr><th className="th">Product</th><th className="th text-right">Loans</th><th className="th text-right">Active</th><th className="th text-right">Portfolio</th><th className="th text-right">Interest Rec.</th><th className="th text-right">Default Rate</th></tr></thead>
                  <tbody>
                    {data.product_performance.map((p) => (
                      <tr key={p.product_id} className="border-t border-border">
                        <td className="td font-semibold">{p.product_name || `Product ${p.product_id}`}</td>
                        <td className="td text-right tabnums">{p.loans}</td>
                        <td className="td text-right tabnums">{p.active}</td>
                        <td className="td text-right tabnums">{fmtKES(p.portfolio)}</td>
                        <td className="td text-right tabnums">{fmtKES(p.interest_recovered)}</td>
                        <td className={`td text-right tabnums font-semibold ${p.default_rate_pct > 10 ? "text-red-600" : p.default_rate_pct > 5 ? "text-amber-600" : "text-accent"}`}>{p.default_rate_pct}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="card p-5">
              <h3 className="font-bold mb-1">Lead Conversion</h3>
              <p className="text-xs text-gray-400 mb-4">CRM pipeline → clients</p>
              {!lc ? <Empty /> : (
                <>
                  <div className="rounded-xl bg-brand-gradient text-white px-4 py-3 mb-4">
                    <div className="text-[10px] font-bold uppercase tracking-wider text-white/80">Conversion rate</div>
                    <div className="text-3xl font-extrabold tabnums leading-tight">{lc.conversion_rate_pct}%</div>
                  </div>
                  <div className="grid grid-cols-3 gap-2 mb-4">
                    <div className="rounded-xl bg-surface2 border border-border px-3 py-2 text-center">
                      <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Leads</div>
                      <div className="text-lg font-extrabold tabnums">{lc.total_leads}</div>
                    </div>
                    <div className="rounded-xl bg-surface2 border border-border px-3 py-2 text-center">
                      <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Converted</div>
                      <div className="text-lg font-extrabold tabnums text-accent">{lc.converted}</div>
                    </div>
                    <div className="rounded-xl bg-surface2 border border-border px-3 py-2 text-center">
                      <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">BM Approved</div>
                      <div className="text-lg font-extrabold tabnums">{lc.bm_approved}</div>
                    </div>
                  </div>
                  {Object.keys(lc.by_temperature || {}).length > 0 && (
                    <div>
                      <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1.5">By temperature</div>
                      <div className="flex flex-wrap gap-1.5">
                        {Object.entries(lc.by_temperature).map(([k2, v]) => (
                          <span key={k2} className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-gray-100 text-gray-700 capitalize">{k2}: {v}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
