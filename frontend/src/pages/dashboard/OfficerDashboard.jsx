// Relationship-Officer home screen: the officer's OWN clients, their onboarding
// status and the status of their loan book — NOT the company-wide executive view.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmtKES, fmtDate } from "../../lib/api";
import { Badge, Empty, KpiCard, PageHeader, Skeleton } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

// Business buckets shown to the officer, in review order.
const LOAN_BUCKETS = [
  { key: "approved", label: "Approved", color: "#14B8A6", tone: "text-teal-700", tint: "bg-teal-50" },
  { key: "pending", label: "Pending", color: "#9CA3AF", tone: "text-gray-600", tint: "bg-gray-50" },
  { key: "rejected", label: "Rejected", color: "#6B7280", tone: "text-gray-600", tint: "bg-gray-50" },
  { key: "performing", label: "Performing", color: "#10B981", tone: "text-emerald-700", tint: "bg-emerald-50" },
  { key: "non_performing", label: "Non-Performing", color: "#EF4444", tone: "text-red-700", tint: "bg-red-50" },
];

// `style` maps to a colour key in the Badge STATUS_STYLES palette.
const PROFILE_BADGE = {
  approved: { style: "success", label: "Onboarded" },
  pending: { style: "medium", label: "Pending" },
  pending_approval: { style: "medium", label: "Pending" },
  draft: { style: "medium", label: "Draft" },
  rejected: { style: "failed", label: "Rejected" },
};
const KYC_BADGE = {
  validated: { style: "success", label: "KYC ✓" },
  draft: { style: "medium", label: "KYC pending" },
  failed: { style: "failed", label: "KYC failed" },
};

function StatusPill({ map, value }) {
  const v = (value || "").toLowerCase();
  const cfg = map[v] || { style: "draft", label: value || "—" };
  return <Badge value={cfg.style}>{cfg.label}</Badge>;
}

export default function OfficerDashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await api("/api/v1/dashboard/my-portfolio");
        if (alive) setData(res);
      } catch (e) {
        if (alive) setErr(e?.message || "Failed to load your portfolio");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  if (loading) {
    return (
      <div className="space-y-4">
        <PageHeader title="My Portfolio" />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3"><Skeleton className="h-24" lines={4} /></div>
      </div>
    );
  }
  if (err) {
    return (
      <div>
        <PageHeader title="My Portfolio" />
        <div className="card p-6 text-center text-sm text-red-600">{err}</div>
      </div>
    );
  }

  const k = data?.kpis || {};
  const onboarding = data?.onboarding || { onboarded: 0, pending: 0, rejected: 0 };
  const loanStatus = data?.loan_status || {};
  const clients = data?.clients || [];
  const companyView = data?.scope === "company";

  return (
    // overflow-x-auto adds the requested horizontal scroll bar at the bottom of
    // the page for wide content; the outer div keeps vertical scroll natural.
    <div className="space-y-5 overflow-x-auto pb-3">
      <PageHeader
        title={companyView ? "Team Portfolio" : "My Portfolio"}
        crumbs={[companyView ? "All clients in your organisation" :
          `Your clients & loan book${user?.full_name ? " — " + user.full_name : ""}`]}
      />

      {/* Headline KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <KpiCard label="My Clients" value={k.clients_total ?? 0} icon="👥"
                 sub={`${onboarding.onboarded} onboarded`} variant="hero" />
        <KpiCard label="Active Loans" value={k.loans_performing ?? 0} tone="good" icon="💳"
                 sub={`${k.loans_total ?? 0} total`} />
        <KpiCard label="Non-Performing" value={k.loans_non_performing ?? 0}
                 tone={(k.loans_non_performing ?? 0) > 0 ? "bad" : "default"} icon="⚠️"
                 sub={`PAR ${k.portfolio_at_risk_pct ?? 0}%`} />
        <KpiCard label="Outstanding" value={fmtKES(k.outstanding_balance ?? 0)} icon="📈"
                 sub="Performing + overdue" />
      </div>

      {/* Onboarding status */}
      <div className="card p-5">
        <h3 className="font-bold text-base mb-3">Client Onboarding Status</h3>
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: "Onboarded", value: onboarding.onboarded, color: "#10B981", tint: "bg-emerald-50", tone: "text-emerald-700" },
            { label: "Pending", value: onboarding.pending, color: "#F59E0B", tint: "bg-amber-50", tone: "text-amber-700" },
            { label: "Rejected", value: onboarding.rejected, color: "#EF4444", tint: "bg-red-50", tone: "text-red-700" },
          ].map((s) => (
            <div key={s.label} className={`rounded-xl border border-border ${s.tint} p-4`}>
              <div className={`text-2xl font-extrabold tabnums ${s.tone}`}>{s.value}</div>
              <div className="text-xs font-semibold text-gray-500 mt-0.5">{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Loan status buckets */}
      <div className="card p-5">
        <h3 className="font-bold text-base mb-3">Loan Status</h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {LOAN_BUCKETS.map((b) => (
            <div key={b.key} className={`rounded-xl border border-border ${b.tint} p-4`}>
              <div className={`text-2xl font-extrabold tabnums ${b.tone}`}>{loanStatus[b.key] ?? 0}</div>
              <div className="text-xs font-semibold text-gray-500 mt-0.5">{b.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Client table */}
      <div className="card p-0 overflow-hidden">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <h3 className="font-bold text-base">My Clients</h3>
          <span className="text-xs text-gray-400">{clients.length} record{clients.length === 1 ? "" : "s"}</span>
        </div>
        {clients.length === 0 ? (
          <div className="p-8"><Empty text="You have no clients yet" /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px]">
              <thead>
                <tr>
                  <th className="th text-left">Client</th>
                  <th className="th text-left">Phone</th>
                  <th className="th text-left">Onboarding</th>
                  <th className="th text-left">KYC</th>
                  <th className="th text-right">Loans</th>
                  <th className="th text-right">Active</th>
                  <th className="th text-right">Overdue</th>
                  <th className="th text-left">Joined</th>
                </tr>
              </thead>
              <tbody>
                {clients.map((c) => (
                  <tr key={c.id} className="hover:bg-surface2 cursor-pointer"
                      onClick={() => navigate(`/clients/${c.id}`)}>
                    <td className="td font-semibold text-charcoal">{c.name || "—"}</td>
                    <td className="td">{c.phone || "—"}</td>
                    <td className="td"><StatusPill map={PROFILE_BADGE} value={c.profile_status} /></td>
                    <td className="td"><StatusPill map={KYC_BADGE} value={c.kyc_status} /></td>
                    <td className="td text-right tabnums">{c.loans_total ?? 0}</td>
                    <td className="td text-right tabnums text-emerald-700">{c.loans_active ?? 0}</td>
                    <td className="td text-right tabnums text-red-600">{c.loans_overdue ?? 0}</td>
                    <td className="td">{fmtDate(c.created_at)}</td>
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
