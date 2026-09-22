// Director surface — a company-wide executive view: portfolio-health KPIs,
// per-branch performance, top officer rankings, the pending-approval queue and
// the CRM lead-conversion funnel. Data is scoped server-side to the caller's
// data scope (directors are company-scope). Access is gated by permission
// (dashboard.company) at the route level — no role-name hardcoding here.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtKES } from "../../lib/api";
import { PageHeader, KpiCard, Spinner, Empty } from "../../components/ui";

export default function DirectorDashboard() {
  const [data, setData] = useState(null);
  const [pendingLoans, setPendingLoans] = useState(null);
  const [pendingClients, setPendingClients] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api("/api/v1/dashboard/executive").then(setData).catch((e) => setErr(e.detail || "Failed to load"));
    // Both approval endpoints return the full pending list; use its length as
    // the count. Failures degrade to a dash rather than blocking the page.
    api("/api/v1/approvals/loans?limit=1")
      .then((r) => setPendingLoans(Array.isArray(r) ? r.length : (r?.total ?? 0)))
      .catch(() => setPendingLoans(null));
    api("/api/v1/approvals/clients?limit=1")
      .then((r) => setPendingClients(Array.isArray(r) ? r.length : (r?.total ?? 0)))
      .catch(() => setPendingClients(null));
  }, []);

  if (err) return <div className="card p-6 text-sm text-red-600">{err}</div>;
  if (!data) return <div className="p-10 flex justify-center"><Spinner /></div>;

  const k = data.kpis || {};
  const branches = data.branch_breakdown || [];
  const officers = (data.officer_rankings || []).slice(0, 8);
  const lc = data.lead_conversion;

  // Yield: prefer yield_on_portfolio, fall back to average_yield, else "—".
  const yieldVal = k.yield_on_portfolio ?? k.average_yield;
  const yieldStr = yieldVal == null ? "—" : `${yieldVal}%`;
  // Collection / repayment rate is optional.
  const collectionVal = k.collection_rate ?? k.repayment_rate;
  const collectionStr = collectionVal == null ? "—" : `${collectionVal}%`;

  return (
    <div>
      <PageHeader title="Director View" crumbs={["Dashboard", "Director"]} />

      {/* Row 1 — Portfolio Health KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-5">
        <KpiCard label="Total Outstanding" value={fmtKES(k.total_outstanding || 0)} icon="🏦" variant="hero" sub="Active book" />
        <KpiCard label="Active Loans" value={k.active_loans ?? 0} icon="📋" sub={`${k.overdue_loans ?? 0} overdue`} />
        <KpiCard label="PAR 30" value={`${k.par_30 ?? 0}%`} icon="⏱" tone={(k.par_30 ?? 0) > 10 ? "bad" : (k.par_30 ?? 0) > 5 ? "warn" : "good"} sub="> 30 days overdue" />
        <KpiCard label="Yield on Portfolio" value={yieldStr} icon="📈" tone="good" sub="Interest / outstanding" />
        <KpiCard label="Collection Rate" value={collectionStr} icon="✅" tone={collectionVal == null ? undefined : collectionVal >= 90 ? "good" : collectionVal >= 70 ? "warn" : "bad"} sub="Collected vs expected" />
      </div>

      {/* Row 2 — Branch Performance */}
      <div className="card p-4 mb-5 overflow-x-auto">
        <h3 className="font-bold mb-3">Branch Performance</h3>
        {branches.length === 0 ? (
          <p className="text-sm text-gray-400">No branch data available</p>
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr>
                <th className="th">Branch</th>
                <th className="th text-right">Active Loans</th>
                <th className="th text-right">Outstanding</th>
                <th className="th text-right">PAR %</th>
              </tr>
            </thead>
            <tbody>
              {branches.map((b) => (
                <tr key={b.branch_name} className="border-t border-border">
                  <td className="td font-semibold">{b.branch_name}</td>
                  <td className="td text-right tabnums">{b.active_loans}</td>
                  <td className="td text-right tabnums">{fmtKES(b.outstanding)}</td>
                  <td className={`td text-right tabnums font-semibold ${b.par_30 > 10 ? "text-red-600" : b.par_30 > 5 ? "text-amber-600" : "text-accent"}`}>{b.par_30}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Row 3 — Top Officer Rankings */}
      <div className="card p-4 mb-5 overflow-x-auto">
        <h3 className="font-bold mb-1">Top Officer Rankings</h3>
        <p className="text-xs text-gray-400 mb-3">Top officers by net margin (interest recovered − cost − defaults).</p>
        {officers.length === 0 ? <Empty /> : (
          <table className="min-w-full text-sm">
            <thead>
              <tr>
                <th className="th">Rank</th>
                <th className="th">Officer</th>
                <th className="th">Branch</th>
                <th className="th text-right">Net Margin (KES)</th>
              </tr>
            </thead>
            <tbody>
              {officers.map((o, i) => {
                const medal = i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : null;
                return (
                  <tr key={o.staff_id ?? i} className={`border-t border-border ${i < 3 ? "bg-emerald-50/40" : ""}`}>
                    <td className="td font-bold tabnums">{medal ? <span className="text-base">{medal}</span> : (o.rank ?? i + 1)}</td>
                    <td className="td font-semibold">{o.name}</td>
                    <td className="td text-gray-500">{o.branch || o.role || "—"}</td>
                    <td className={`td text-right font-bold tabnums ${(o.net_margin ?? 0) >= 0 ? "text-accent" : "text-red-600"}`}>{fmtKES(o.net_margin ?? 0)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Row 4 — Approval Queue */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-5">
        <Link to="/approvals/loans" className="card p-5 hover:border-accent/40 transition-colors">
          <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Pending Loan Approvals</div>
          <div className="text-3xl font-extrabold tabnums leading-tight mt-1">{pendingLoans == null ? "—" : pendingLoans}</div>
          <div className="text-xs text-accent mt-2">Go to Loan Approvals →</div>
        </Link>
        <Link to="/approvals/clients" className="card p-5 hover:border-accent/40 transition-colors">
          <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Pending Client Approvals</div>
          <div className="text-3xl font-extrabold tabnums leading-tight mt-1">{pendingClients == null ? "—" : pendingClients}</div>
          <div className="text-xs text-accent mt-2">Go to Client Approvals →</div>
        </Link>
      </div>

      {/* Row 5 — Lead Conversion Funnel */}
      {lc && (Array.isArray(lc) ? lc.length > 0 : Object.keys(lc).length > 0) && (
        <div className="card p-5">
          <h3 className="font-bold mb-1">Lead Conversion Funnel</h3>
          <p className="text-xs text-gray-400 mb-4">CRM pipeline → clients</p>
          {Array.isArray(lc) ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
              {lc.map((stage, i) => (
                <div key={stage.stage ?? i} className="rounded-xl bg-surface2 border border-border px-3 py-2 text-center">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500 capitalize">{stage.stage ?? stage.label ?? `Stage ${i + 1}`}</div>
                  <div className="text-lg font-extrabold tabnums">{stage.count ?? stage.value ?? 0}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div className="rounded-xl bg-brand-gradient text-white px-3 py-2 text-center">
                <div className="text-[10px] font-bold uppercase tracking-wider text-white/80">Conversion</div>
                <div className="text-lg font-extrabold tabnums">{lc.conversion_rate_pct ?? 0}%</div>
              </div>
              <div className="rounded-xl bg-surface2 border border-border px-3 py-2 text-center">
                <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Leads</div>
                <div className="text-lg font-extrabold tabnums">{lc.total_leads ?? 0}</div>
              </div>
              <div className="rounded-xl bg-surface2 border border-border px-3 py-2 text-center">
                <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Converted</div>
                <div className="text-lg font-extrabold tabnums text-accent">{lc.converted ?? 0}</div>
              </div>
              <div className="rounded-xl bg-surface2 border border-border px-3 py-2 text-center">
                <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Approved</div>
                <div className="text-lg font-extrabold tabnums">{lc.bm_approved ?? 0}</div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
