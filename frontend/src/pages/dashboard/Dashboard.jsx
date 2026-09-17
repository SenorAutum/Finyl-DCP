// Executive Financial Health & Staff Analysis Dashboard.
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Label, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, fmtKES } from "../../lib/api";
import { Empty, KpiCard, PageHeader, Skeleton } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

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
    { key: 1, label: "Stage 1", note: "Performing (12-month ECL)", exp: ecl.stage1_exposure, prov: ecl.stage1_provision, rate: ecl.rates?.stage1_rate, color: "#10B981", tint: "bg-emerald-50", tone: "text-emerald-700" },
    { key: 2, label: "Stage 2", note: "Under-performing (lifetime ECL)", exp: ecl.stage2_exposure, prov: ecl.stage2_provision, rate: ecl.rates?.stage2_rate, color: "#F59E0B", tint: "bg-amber-50", tone: "text-amber-700" },
    { key: 3, label: "Stage 3", note: "Non-performing (lifetime ECL)", exp: ecl.stage3_exposure, prov: ecl.stage3_provision, rate: ecl.rates?.stage3_rate, color: "#EF4444", tint: "bg-red-50", tone: "text-red-700" },
  ];
  // Provision mix bar — proportions of each stage's provision (real ecl fields only).
  const provTotal = stages.reduce((a, s) => a + (Number(s.prov) || 0), 0);

  return (
    <div className="card p-5 mb-5">
      <div className="flex items-start justify-between flex-wrap gap-4 mb-4">
        <div>
          <h3 className="font-bold text-base">IFRS 9 Expected Credit Loss</h3>
          <p className="text-xs text-gray-400 mt-0.5">Staged provisioning &amp; portfolio coverage</p>
        </div>
        <div className="flex items-stretch gap-3">
          <div className="rounded-xl bg-surface2 border border-border px-4 py-2">
            <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Total provision</div>
            <div className="text-lg font-extrabold text-charcoal tabnums leading-tight">{kes2(ecl.total_ecl_provision)}</div>
          </div>
          <div className="rounded-xl bg-brand-gradient text-white px-4 py-2 shadow-card">
            <div className="text-[10px] font-bold uppercase tracking-wider text-white/80">Coverage ratio</div>
            <div className="text-lg font-extrabold tabnums leading-tight">{pct(ecl.coverage_ratio)}</div>
          </div>
        </div>
      </div>

      {/* Provision mix bar */}
      {provTotal > 0 && (
        <div className="mb-4">
          <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-gray-100">
            {stages.map((s) => {
              const w = ((Number(s.prov) || 0) / provTotal) * 100;
              return w > 0 ? <div key={s.key} style={{ width: `${w}%`, background: s.color }} title={`${s.label}: ${kes2(s.prov)}`} /> : null;
            })}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {stages.map((s) => (
              <div key={s.key} className="flex items-center gap-1.5 text-[11px] text-gray-500">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: s.color }} />
                {s.label} <span className="font-semibold text-charcoal tabnums">{kes2(s.prov)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {stages.map((s) => (
          <div key={s.key} className="relative rounded-xl border border-border p-3.5 overflow-hidden bg-surface">
            <span className="absolute left-0 top-0 h-full w-1" style={{ background: s.color }} />
            <div className="flex items-center justify-between">
              <div className={`font-bold text-sm ${s.tone}`}>{s.label}</div>
              <div className={`text-[11px] font-semibold px-1.5 py-0.5 rounded-md ${s.tint} ${s.tone}`}>rate {pct(s.rate)}</div>
            </div>
            <div className="text-[11px] text-gray-400 mb-2.5">{s.note}</div>
            <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">Exposure</div>
            <div className="text-sm font-medium tabnums">{kes2(s.exp)}</div>
            <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500 mt-1.5">Provision</div>
            <div className="text-sm font-semibold tabnums">{kes2(s.prov)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Skeleton placeholder mirroring the dashboard layout while data loads.
function DashboardSkeleton() {
  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="card p-4"><Skeleton className="h-3 w-20" /><Skeleton className="h-7 w-28 mt-3" /><Skeleton className="h-3 w-24 mt-2" /></div>
        ))}
      </div>
      <div className="card p-4 mb-5"><Skeleton className="h-4 w-48" /><Skeleton className="h-40 w-full mt-4" /></div>
      <div className="grid lg:grid-cols-3 gap-4 mb-5">
        <div className="card p-4 lg:col-span-2"><Skeleton className="h-4 w-56" /><Skeleton className="h-56 w-full mt-4" /></div>
        <div className="card p-4"><Skeleton className="h-4 w-40" /><Skeleton className="h-56 w-full mt-4" /></div>
      </div>
    </>
  );
}

export default function Dashboard() {
  const nav = useNavigate();
  const { can, user } = useAuth();
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
  const statusTotal = data?.status_mix?.reduce((a, s) => a + s.count, 0) ?? 0;

  return (
    <div>
      <PageHeader title="Executive Dashboard" crumbs={["Dashboard"]}
        actions={(user?.role === "super_admin" || can("reports.export")) && (
          <button className="btn-primary" onClick={() => nav("/dashboard/executive")}>Executive Analytics →</button>
        )} />

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

      {loading || !data ? <DashboardSkeleton /> : (
        <>
          {/* KPI cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            <KpiCard label="PAR 1" value={`${k.par_1}%`} icon="⚡" tone={k.par_1 > 15 ? "bad" : k.par_1 > 5 ? "warn" : "good"} sub="Portfolio at risk > 1 day" />
            <KpiCard label="PAR 30" value={`${k.par_30}%`} icon="⏱" tone={k.par_30 > 10 ? "bad" : k.par_30 > 5 ? "warn" : "good"} sub="> 30 days overdue" />
            <KpiCard label="PAR 90" value={`${k.par_90}%`} icon="🚩" tone={k.par_90 > 5 ? "bad" : "warn"} sub="> 90 days overdue" />
            <KpiCard label="Repayment Rate" value={`${k.repayment_rate}%`} icon="✅" tone={k.repayment_rate >= 90 ? "good" : k.repayment_rate >= 70 ? "warn" : "bad"} sub="Collected vs expected" />
            <KpiCard label="Disbursement Volume" value={fmtKES(k.disbursement_volume)} icon="💸" variant="hero" sub="Cumulative principal" />
            <KpiCard label="Total Outstanding" value={fmtKES(k.total_outstanding)} icon="🏦" variant="hero" sub={`${k.overdue_loans} overdue loans`} />
            <KpiCard label="Yield on Portfolio" value={`${k.yield_on_portfolio}%`} icon="📈" tone="good" sub="Interest income / outstanding" />
            <KpiCard label="Active Loans" value={k.active_loans} icon="📋" sub={`${fmtKES(k.total_collected)} collected`} />
          </div>

          {/* IFRS 9 Expected Credit Loss */}
          <EclCard ecl={data.ecl} />

          {/* Charts row */}
          <div className="grid lg:grid-cols-3 gap-4 mb-5">
            <div className="card p-4 lg:col-span-2">
              <h3 className="font-bold mb-3">Disbursements vs Collections (12 months)</h3>
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={data.trend} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="gDisbursed" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#0D9488" stopOpacity={0.28} />
                      <stop offset="95%" stopColor="#0D9488" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="gCollected" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#10B981" stopOpacity={0.28} />
                      <stop offset="95%" stopColor="#10B981" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${(v / 1e6).toFixed(1)}M`} axisLine={false} tickLine={false} />
                  <Tooltip formatter={(v) => fmtKES(v)} />
                  <Legend iconType="circle" />
                  <Area type="monotone" dataKey="disbursed" stroke="#0D9488" strokeWidth={2.5} fill="url(#gDisbursed)" name="Disbursed" activeDot={{ r: 4 }} />
                  <Area type="monotone" dataKey="collected" stroke="#10B981" strokeWidth={2.5} fill="url(#gCollected)" name="Collected" activeDot={{ r: 4 }} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div className="card p-4">
              <h3 className="font-bold mb-3">Portfolio Status Mix</h3>
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie data={data.status_mix} dataKey="count" nameKey="status" innerRadius={58} outerRadius={92} paddingAngle={2} cornerRadius={4} stroke="none">
                    {data.status_mix.map((s) => <Cell key={s.status} fill={STATUS_COLORS[s.status] || "#9CA3AF"} />)}
                    <Label content={({ viewBox }) => {
                      const { cx, cy } = viewBox;
                      return (
                        <g>
                          <text x={cx} y={cy - 6} textAnchor="middle" style={{ fontSize: 24, fontWeight: 800, fill: "#1F2937" }}>{statusTotal.toLocaleString()}</text>
                          <text x={cx} y={cy + 15} textAnchor="middle" style={{ fontSize: 11, fill: "#9CA3AF" }}>loans</text>
                        </g>
                      );
                    }} />
                  </Pie>
                  <Tooltip /><Legend iconType="circle" />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Product × Region heatmap */}
          <div className="card p-4 mb-5 overflow-x-auto">
            <div className="flex items-start justify-between flex-wrap gap-2 mb-3">
              <div>
                <h3 className="font-bold mb-1">Product-per-Region Performance Matrix</h3>
                <p className="text-xs text-gray-400">Success rate = paid + performing loans as % of all disbursed, per region × product.</p>
              </div>
              {/* Heat legend */}
              <div className="flex items-center gap-2 text-[10px] text-gray-500">
                <span className="font-semibold uppercase tracking-wider">Low</span>
                <span className="w-6 h-3 rounded bg-red-100" />
                <span className="w-6 h-3 rounded bg-amber-100" />
                <span className="w-6 h-3 rounded bg-emerald-50" />
                <span className="w-6 h-3 rounded bg-emerald-100" />
                <span className="font-semibold uppercase tracking-wider">High</span>
              </div>
            </div>
            {matrix?.regions.length ? (
              <table className="min-w-full border-separate border-spacing-0">
                <thead><tr>
                  <th className="th sticky left-0 z-20">Product ↓ / Region →</th>
                  {matrix.regions.map((r) => <th key={r} className="th text-center">{r}</th>)}
                </tr></thead>
                <tbody>
                  {matrix.prods.map((p) => (
                    <tr key={p}>
                      <td className="td font-semibold sticky left-0 bg-surface z-10">{p}</td>
                      {matrix.regions.map((r) => {
                        const c = matrix.map[`${r}|${p}`];
                        return (
                          <td key={r} className="td text-center">
                            <span className={`inline-block min-w-[64px] px-2.5 py-1.5 rounded-lg text-xs font-bold ring-1 ring-inset ring-black/5 ${heat(c?.success_rate)}`}>
                              {c ? `${c.success_rate}%` : "—"}
                            </span>
                            {c && <div className="text-[10px] text-gray-400 mt-1 tabnums">{c.total} loans</div>}
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
                    const medal = i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : null;
                    return (
                      <tr key={s.staff_id} className={`${top ? "bg-emerald-50/50" : bottom ? "bg-red-50/40" : ""}`}>
                        <td className="td font-bold tabnums">{medal ? <span className="text-base">{medal}</span> : s.rank}</td>
                        <td className="td"><div className="font-semibold">{s.name}</div><div className="text-[11px] text-gray-400">{s.branch}</div></td>
                        <td className="td tabnums">{s.loans_managed}</td>
                        <td className="td text-right tabnums">{fmtKES(s.interest_recovered)}</td>
                        <td className="td text-right text-red-600 tabnums">{fmtKES(s.defaulted_principal)}</td>
                        <td className={`td text-right font-bold tabnums ${s.net_margin >= 0 ? "text-accent" : "text-red-600"}`}>{fmtKES(s.net_margin)}</td>
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
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} axisLine={false} tickLine={false} />
                  <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
                  <Tooltip formatter={(v) => fmtKES(v)} cursor={{ fill: "rgba(16,185,129,0.06)" }} />
                  <Bar dataKey="net_margin" name="Net margin" radius={[0, 4, 4, 0]}>
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
