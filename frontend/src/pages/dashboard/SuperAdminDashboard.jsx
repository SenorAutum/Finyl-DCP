// Super-Admin platform command centre: every tenant on one screen — loan
// portfolios, subscriptions (SMS revenue), reminders, complaint SLAs and
// operations. Data: GET /api/v1/admin/tenants-dashboard.
import { useEffect, useState } from "react";
import { api, fmtKES } from "../../lib/api";
import { Empty, KpiCard, PageHeader, Skeleton } from "../../components/ui";

const num = (n) => (n == null ? "0" : Number(n).toLocaleString("en-KE"));

// Compact stacked bar of a tenant's loan status buckets.
const BUCKETS = [
  { key: "performing", label: "Performing", color: "#10B981" },
  { key: "approved", label: "Approved", color: "#14B8A6" },
  { key: "pending", label: "Pending", color: "#9CA3AF" },
  { key: "non_performing", label: "Non-Performing", color: "#EF4444" },
  { key: "closed", label: "Closed", color: "#0D9488" },
  { key: "rejected", label: "Rejected", color: "#6B7280" },
];

function LoanBar({ status }) {
  const total = BUCKETS.reduce((a, b) => a + (status[b.key] || 0), 0);
  if (!total) return <span className="text-xs text-gray-400">No loans</span>;
  return (
    <div className="w-full min-w-[120px]">
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-gray-100">
        {BUCKETS.map((b) => {
          const w = ((status[b.key] || 0) / total) * 100;
          return w > 0 ? <div key={b.key} style={{ width: `${w}%`, background: b.color }}
                             title={`${b.label}: ${status[b.key]}`} /> : null;
        })}
      </div>
      <div className="mt-1 text-[10px] text-gray-400 tabnums">
        {status.performing || 0} perf · {status.non_performing || 0} NPL · {total} total
      </div>
    </div>
  );
}

export default function SuperAdminDashboard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [days, setDays] = useState(30);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const res = await api(`/api/v1/admin/tenants-dashboard?days=${days}`);
        if (alive) setData(res);
      } catch (e) {
        if (alive) setErr(e?.message || "Failed to load tenants dashboard");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [days]);

  if (loading) {
    return (
      <div className="space-y-4">
        <PageHeader title="Platform Overview" />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3"><Skeleton className="h-24" lines={4} /></div>
      </div>
    );
  }
  if (err) {
    return (
      <div>
        <PageHeader title="Platform Overview" />
        <div className="card p-6 text-center text-sm text-red-600">{err}</div>
      </div>
    );
  }

  const t = data?.totals || {};
  const tenants = data?.tenants || [];

  return (
    // overflow-x-auto puts a horizontal scroll bar at the bottom for the wide
    // tenants table without breaking vertical page scroll.
    <div className="space-y-5 overflow-x-auto pb-3">
      <PageHeader
        title="Platform Overview"
        crumbs={[`${t.tenant_count ?? tenants.length} tenants · ${t.active_tenants ?? 0} active`]}
        actions={
          <select className="input w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        }
      />

      {/* Platform roll-up */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <KpiCard label="SMS Revenue" value={fmtKES(t.revenue ?? 0)} icon="💰" variant="hero"
                 sub={`Subscriptions · last ${data?.window_days ?? days}d`} />
        <KpiCard label="Total Clients" value={num(t.clients)} icon="👥"
                 sub={`${num(t.staff)} staff`} />
        <KpiCard label="Outstanding" value={fmtKES(t.outstanding ?? 0)} icon="📈"
                 sub={`${num(t.loans)} loans`} />
        <KpiCard label="Reminders Sent" value={num(t.reminders)} icon="🔔"
                 sub={`last ${data?.window_days ?? days}d`} />
      </div>

      {/* Per-tenant table */}
      <div className="card p-0 overflow-hidden">
        <div className="p-4 border-b border-border">
          <h3 className="font-bold text-base">Tenants</h3>
          <p className="text-xs text-gray-400 mt-0.5">
            Loan portfolios · subscriptions (SMS revenue) · reminders · SLAs · operations
          </p>
        </div>
        {tenants.length === 0 ? (
          <div className="p-8"><Empty text="No tenants found" /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1080px]">
              <thead>
                <tr>
                  <th className="th text-left">Tenant</th>
                  <th className="th text-left">Loan Book</th>
                  <th className="th text-right">Outstanding</th>
                  <th className="th text-right">PAR 30</th>
                  <th className="th text-right">Subscription<br/>(SMS Rev)</th>
                  <th className="th text-right">Reminders</th>
                  <th className="th text-right">SLA %</th>
                  <th className="th text-right">Breaches</th>
                  <th className="th text-right">Clients</th>
                  <th className="th text-right">Staff</th>
                  <th className="th text-right">Tasks</th>
                </tr>
              </thead>
              <tbody>
                {tenants.map((tn) => (
                  <tr key={tn.id} className={tn.active ? "" : "opacity-50"}>
                    <td className="td">
                      <div className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-full shrink-0"
                              style={{ background: tn.logo_color || "#14B8A6" }} />
                        <div>
                          <div className="font-semibold text-charcoal leading-tight">{tn.name}</div>
                          <div className="text-[10px] text-gray-400 uppercase tracking-wide">{tn.code}</div>
                        </div>
                      </div>
                    </td>
                    <td className="td"><LoanBar status={tn.loan_status || {}} /></td>
                    <td className="td text-right tabnums">{fmtKES(tn.portfolio?.total_outstanding ?? 0)}</td>
                    <td className="td text-right tabnums">
                      {(tn.portfolio?.par_30 ?? 0)}%
                    </td>
                    <td className="td text-right tabnums font-semibold text-teal-700">
                      {fmtKES(tn.subscription?.sms_revenue_kes ?? 0)}
                      <div className="text-[10px] text-gray-400 font-normal">
                        {num(tn.subscription?.billable_msgs)} msgs
                      </div>
                    </td>
                    <td className="td text-right tabnums">{num(tn.subscription?.reminders_sent)}</td>
                    <td className="td text-right tabnums">{tn.sla?.within_sla_pct ?? 0}%</td>
                    <td className="td text-right tabnums">
                      <span className={(tn.sla?.breached ?? 0) > 0 ? "text-red-600 font-semibold" : ""}>
                        {num(tn.sla?.breached)}
                      </span>
                    </td>
                    <td className="td text-right tabnums">{num(tn.operations?.clients)}</td>
                    <td className="td text-right tabnums">{num(tn.operations?.staff)}</td>
                    <td className="td text-right tabnums">{num(tn.operations?.pending_tasks)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
