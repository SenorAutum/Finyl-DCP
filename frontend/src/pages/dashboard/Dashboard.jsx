// Executive Financial Health & Staff Analysis Dashboard.
import { useEffect, useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, fmtKES } from "../../lib/api";
import { Empty, KpiCard, PageHeader, Spinner } from "../../components/ui";

const STATUS_COLORS = { active: "#10B981", paid: "#0D9488", overdue: "#F59E0B", defaulted: "#EF4444", pending: "#9CA3AF", underwriting: "#3B82F6", rejected: "#6B7280", approved: "#14B8A6" };

// Exact 2-decimal KES (fmtKES rounds to whole shillings, unsuitable for provisions).
const kes2 = (n) =>
  n == null ? "—" : `KES ${Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (frac) => (frac == null ? "—" : `${(Number(frac) * 100).toFixed(2)}%`);

// IFRS 9 Expected Credit Loss provisioning summary (additive; renders only when
// the backend supplies data.ecl). Three staged buckets + total provision + coverage.
function EclCard({ ecl }) {
  if (!ecl) return null;
  const stages = [
    { key: 1, label: "Stage 1", note: "Performing (12-month ECL)", exp: ecl.stage1_exposure, prov: ecl.stage1_provision, rate: ecl.rates?.stage1_rate, tone: "text-emerald-700" },
    { key: 2, label: "Stage 2", note: "Under-performing (lifetime ECL)", exp: ecl.stage2_exposure, prov: ecl.stage2_provision, rate: ecl.rates?.stage2_rate, tone: "text-amber-700" },
    { key: 3, label: "Stage 3", note: "Non-performing (lifetime ECL)", exp: ecl.stage3_exposure, prov: ecl.stage3_provision, rate: ecl.rates?.stage3_rate, tone: "text-red-700" },
  ];
  return (
    <div className="card p-4 mb-5">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-3">
        <h3 className="font-bold">IFRS 9 Expected Credit Loss</h3>
        <div className="text-sm text-gray-500">
          Total provision <span className="font-bold text-charcoal">{kes2(ecl.total_ecl_provision)}</span>
          <span className="mx-2 text-gray-300">·</span>
          Coverage ratio <span className="font-bold text-accent">{pct(ecl.coverage_ratio)}</span>
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {stages.map((s) => (
          <div key={s.key} className="rounded-lg border border-border p-3">
            <div className="flex items-center justify-between">
              <div className={`font-bold text-sm ${s.tone}`}>{s.label}</div>
              <div className="text-[11px] text-gray-400">rate {pct(s.rate)}</div>
            </div>
            <div className="text-[11px] text-gray-400 mb-2">{s.note}</div>
            <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">Exposure</div>
            <div className="text-sm font-medium">{kes2(s.exp)}</div>
            <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500 mt-1.5">Provision</div>
            <div className="text-sm font-semibold">{kes2(s.prov)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [org, setOrg] = useState(null);
  const [products, setProducts] = useState([]);
  const [f, setF] = useState({ region_id: "", branch_id: "", product_id: "", staff_id: "", date_from: "", date_to: "" });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api("/api/v1/lending/org").then(setOrg).catch(() => {});
    api("/api/v1/lending/products").then(setProducts).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    const qs = Object.entries(f).filter(([, v]) => v).map(([k, v]) => `${k}=${v}`).join("&");
    api(`/api/v1/dashboard/overview${qs ? "?" + qs : ""}`).then(setData).finally(() => setLoading(false));
  }, [f]);

  const matrix = useMemo(() => {
    if (!data) return null;
    const { regions, products: prods, cells } = data.product_region_matrix;
    const map = {};
    cells.forEach((c) => { map[`${c.region}|${c.product}`] = c; });
    return { regions, prods, map };
  }, [data]);

  const heat = (rate) => {
    if (rate == null) return "bg-gray-50 text-gray-300";
    if (rate >= 85) return "bg-emerald-100 text-emerald-800";
    if (rate >= 70) return "bg-emerald-50 text-emerald-700";
    if (rate >= 50) return "bg-amber-100 text-amber-800";
    return "bg-red-100 text-red-800";
  };

  const set = (k) => (e) => setF((p) => ({ ...p, [k]: e.target.value }));
  const k = data?.kpis;

  return (
    <div>
      <PageHeader title="Executive Dashboard" crumbs={["Dashboard"]} />

      {/* Global filter bar */}
      <div className="card p-3 mb-5 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
        <select className="input" value={f.region_id} onChange={set("region_id")}>
          <option value="">All regions</option>
          {org?.regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
        <select className="input" value={f.branch_id} onChange={set("branch_id")}>
          <option value="">All branches</option>
          {org?.branches.filter((b) => !f.region_id || b.region_id === +f.region_id).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <select className="input" value={f.product_id} onChange={set("product_id")}>
          <option value="">All products</option>
          {products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <select className="input" value={f.staff_id} onChange={set("staff_id")}>
          <option value="">All staff</option>
          {org?.staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <input className="input" type="date" value={f.date_from} onChange={set("date_from")} title="From (application date)" />
        <input className="input" type="date" value={f.date_to} onChange={set("date_to")} title="To (application date)" />
      </div>

      {loading || !data ? <Spinner /> : (
        <>
          {/* KPI cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            <KpiCard label="PAR 1" value={`${k.par_1}%`} tone={k.par_1 > 15 ? "bad" : k.par_1 > 5 ? "warn" : "good"} sub="Portfolio at risk > 1 day" />
            <KpiCard label="PAR 30" value={`${k.par_30}%`} tone={k.par_30 > 10 ? "bad" : k.par_30 > 5 ? "warn" : "good"} sub="> 30 days overdue" />
            <KpiCard label="PAR 90" value={`${k.par_90}%`} tone={k.par_90 > 5 ? "bad" : "warn"} sub="> 90 days overdue" />
            <KpiCard label="Repayment Rate" value={`${k.repayment_rate}%`} tone={k.repayment_rate >= 90 ? "good" : k.repayment_rate >= 70 ? "warn" : "bad"} sub="Collected vs expected" />
            <KpiCard label="Disbursement Volume" value={fmtKES(k.disbursement_volume)} sub="Cumulative principal" />
            <KpiCard label="Total Outstanding" value={fmtKES(k.total_outstanding)} sub={`${k.overdue_loans} overdue loans`} />
            <KpiCard label="Yield on Portfolio" value={`${k.yield_on_portfolio}%`} tone="good" sub="Interest income / outstanding" />
            <KpiCard label="Active Loans" value={k.active_loans} sub={`${fmtKES(k.total_collected)} collected`} />
          </div>

          {/* IFRS 9 Expected Credit Loss */}
          <EclCard ecl={data.ecl} />

          {/* Charts row */}
          <div className="grid lg:grid-cols-3 gap-4 mb-5">
            <div className="card p-4 lg:col-span-2">
              <h3 className="font-bold mb-3">Disbursements vs Collections (12 months)</h3>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={data.trend}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${(v / 1e6).toFixed(1)}M`} />
                  <Tooltip formatter={(v) => fmtKES(v)} />
                  <Legend />
                  <Line type="monotone" dataKey="disbursed" stroke="#0D9488" strokeWidth={2} dot={false} name="Disbursed" />
                  <Line type="monotone" dataKey="collected" stroke="#10B981" strokeWidth={2} dot={false} name="Collected" />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="card p-4">
              <h3 className="font-bold mb-3">Portfolio Status Mix</h3>
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie data={data.status_mix} dataKey="count" nameKey="status" innerRadius={55} outerRadius={90} paddingAngle={2}>
                    {data.status_mix.map((s) => <Cell key={s.status} fill={STATUS_COLORS[s.status] || "#9CA3AF"} />)}
                  </Pie>
                  <Tooltip /><Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Product × Region heatmap */}
          <div className="card p-4 mb-5 overflow-x-auto">
            <h3 className="font-bold mb-1">Product-per-Region Performance Matrix</h3>
            <p className="text-xs text-gray-400 mb-3">Success rate = paid + performing loans as % of all disbursed, per region × product.</p>
            {matrix?.regions.length ? (
              <table className="min-w-full">
                <thead><tr>
                  <th className="th">Product ↓ / Region →</th>
                  {matrix.regions.map((r) => <th key={r} className="th text-center">{r}</th>)}
                </tr></thead>
                <tbody>
                  {matrix.prods.map((p) => (
                    <tr key={p} className="border-t border-border">
                      <td className="td font-semibold">{p}</td>
                      {matrix.regions.map((r) => {
                        const c = matrix.map[`${r}|${p}`];
                        return (
                          <td key={r} className="td text-center">
                            <span className={`inline-block min-w-[64px] px-2 py-1 rounded-md text-xs font-bold ${heat(c?.success_rate)}`}>
                              {c ? `${c.success_rate}%` : "—"}
                            </span>
                            {c && <div className="text-[10px] text-gray-400 mt-0.5">{c.total} loans</div>}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty />}
          </div>

          {/* Staff performance */}
          <div className="grid lg:grid-cols-2 gap-4">
            <div className="card p-4 overflow-x-auto">
              <h3 className="font-bold mb-1">Staff Net Margin Ranking</h3>
              <p className="text-xs text-gray-400 mb-3">Net margin = interest recovered − (salary + petty cash + defaulted principal).</p>
              <table className="min-w-full">
                <thead><tr><th className="th">#</th><th className="th">Staff</th><th className="th">Loans</th><th className="th text-right">Interest Rec.</th><th className="th text-right">Defaults</th><th className="th text-right">Net Margin</th></tr></thead>
                <tbody>
                  {data.staff_performance.map((s, i, arr) => {
                    const top = i < 3, bottom = i >= arr.length - 3;
                    return (
                      <tr key={s.staff_id} className={`border-t border-border ${top ? "bg-emerald-50/60" : bottom ? "bg-red-50/60" : ""}`}>
                        <td className="td font-bold">{s.rank}</td>
                        <td className="td"><div className="font-semibold">{s.name}</div><div className="text-[11px] text-gray-400">{s.branch}</div></td>
                        <td className="td">{s.loans_managed}</td>
                        <td className="td text-right">{fmtKES(s.interest_recovered)}</td>
                        <td className="td text-right text-red-600">{fmtKES(s.defaulted_principal)}</td>
                        <td className={`td text-right font-bold ${s.net_margin >= 0 ? "text-accent" : "text-red-600"}`}>{fmtKES(s.net_margin)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="card p-4">
              <h3 className="font-bold mb-3">Net Margin by Staff (KES)</h3>
              <ResponsiveContainer width="100%" height={Math.max(260, data.staff_performance.length * 26)}>
                <BarChart data={data.staff_performance} layout="vertical" margin={{ left: 30 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                  <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
                  <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 10 }} />
                  <Tooltip formatter={(v) => fmtKES(v)} />
                  <Bar dataKey="net_margin" name="Net margin">
                    {data.staff_performance.map((s) => <Cell key={s.staff_id} fill={s.net_margin >= 0 ? "#10B981" : "#EF4444"} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
