// WP13 — one DCP, everything Finyl tracks about it: loan book and CBK
// classification, disbursed vs collected, SMS and other paid services, health,
// Finyl's invoices and loans to it, alerts. Data: GET /api/v1/platform/tenants/:id.
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Empty, KpiCard, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { fmtDate } from "../../lib/api";
import { COLORS, ErrorNote, Health, PlatformTabs, Severity, Tabs, exportCsv, kes, kes2, num, pct, useLoad, usePlatform } from "./common";
import { TenantBillingForm } from "./Billing";

const short = (v) => (Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}m` : Math.abs(v) >= 1e3 ? `${Math.round(v / 1e3)}k` : v);
const STATUS_LABELS = { pending: "Pending", underwriting: "Underwriting", approved: "Approved", disbursed: "Disbursed",
  active: "Active", overdue: "Overdue", defaulted: "Defaulted", written_off: "Written off", paid: "Paid",
  closed: "Closed", rejected: "Rejected" };

export default function DcpDetail() {
  const { id } = useParams();
  const [days, setDays] = useState(30);
  const [tab, setTab] = useState("overview");
  const { data: d, error, loading, reload } = useLoad(`/api/v1/platform/tenants/${id}?days=${days}`);
  const { can, isSuper } = usePlatform();
  const { switchTenant } = useAuth();
  const nav = useNavigate();

  if (error) return <div><PlatformTabs /><ErrorNote text={error} /><Link className="btn-ghost" to="/platform">Back</Link></div>;
  if (loading && !d) return <Spinner />;
  if (!d) return null;

  const tabs = [["overview", "Overview"], ["portfolio", "Portfolio"], ["services", "SMS & services"],
    ...(d.billing ? [["billing", "Billing"]] : []), ...(d.facilities ? [["lending", "Finyl loans"]] : []),
    ["alerts", `Alerts${d.alerts.length ? ` (${d.alerts.length})` : ""}`], ["operations", "Operations"]];

  const openAsDcp = async () => { await switchTenant(String(d.id)); nav("/dashboard/executive"); };

  return (
    <div>
      <PageHeader title={d.name} crumbs={["Finyl", "Command centre", d.name]}
        actions={
          <div className="flex gap-2 items-center">
            <select className="input !w-auto text-sm" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[7, 30, 90, 365].map((x) => <option key={x} value={x}>Last {x === 365 ? "12 months" : `${x} days`}</option>)}
            </select>
            {isSuper && <button className="btn-ghost" onClick={openAsDcp} title="Switch into this DCP's own screens">Open DCP screens</button>}
          </div>} />
      <PlatformTabs />
      <div className="flex flex-wrap items-center gap-3 mb-4 text-sm">
        <Health health={d.health} />
        <span className="text-gray-500">Health {d.health.score}/100 ({d.health.band})</span>
        {d.health.reasons.length > 0 && <span className="text-gray-500">· {d.health.reasons.join("; ")}</span>}
        <span className="text-gray-400">· Code {d.code}{d.created_at ? ` · on Finyl since ${fmtDate(d.created_at)}` : ""}</span>
        {!d.active && <Badge value="closed">Switched off</Badge>}
      </div>
      <Tabs tabs={tabs} value={tab} onChange={setTab} />

      {tab === "overview" && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-5">
            <KpiCard variant="hero" label="Loan book" value={kes(d.book.outstanding)} sub={`${num(d.book.open_loans)} open loans`} />
            <KpiCard label="PAR 30" value={pct(d.book.par30_pct)} sub={kes(d.book.par30)} tone={d.book.par30_pct > 10 ? "bad" : d.book.par30_pct > 5 ? "warn" : "good"} />
            <KpiCard label={`Disbursed · ${days}d`} value={kes(d.window.disbursed)} sub={`${num(d.window.loans_disbursed)} loans`} />
            <KpiCard label={`Collected · ${days}d`} value={kes(d.window.collected)} sub={`${num(d.window.repayments)} payments`} tone="good" />
            <KpiCard label="Collected / disbursed" value={pct(d.window.collection_ratio)} />
            <KpiCard label={`SMS · ${days}d`} value={num(d.sms.sent)} sub={`${kes2(d.sms.revenue)} revenue`} />
          </div>
          <div className="card p-4 mb-5">
            <div className="flex items-center mb-2">
              <h3 className="font-bold flex-1">Disbursed vs collected, last 12 months</h3>
              <button className="btn-ghost !py-1 text-xs" onClick={() => exportCsv(`${d.code}-disbursed-vs-collected.csv`,
                [["Month", "label"], ["Disbursed (KES)", "disbursed"], ["Collected (KES)", "collected"]], d.monthly)}>Export</button>
            </div>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={d.monthly} margin={{ left: 0, right: 8 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="label" tickLine={false} />
                <YAxis tickFormatter={short} tickLine={false} axisLine={false} width={48} />
                <Tooltip formatter={(v) => kes(v)} />
                <Legend />
                <Bar dataKey="disbursed" name="Disbursed" fill={COLORS.disbursed} radius={[4, 4, 0, 0]} />
                <Bar dataKey="collected" name="Collected" fill={COLORS.collected} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          {d.trend.length > 1 && (
            <div className="card p-4 mb-5">
              <h3 className="font-bold mb-2">Loan book and PAR 30, last 90 days</h3>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={d.trend} margin={{ left: 0, right: 8 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="day" tickFormatter={(v) => v.slice(5)} tickLine={false} />
                  <YAxis tickFormatter={short} tickLine={false} axisLine={false} width={48} />
                  <Tooltip formatter={(v) => kes(v)} />
                  <Legend />
                  <Line dataKey="outstanding" name="Loan book" stroke={COLORS.disbursed} dot={false} strokeWidth={2} />
                  <Line dataKey="par30" name="PAR 30" stroke="#DC2626" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}

      {tab === "portfolio" && (
        <div className="grid lg:grid-cols-2 gap-5">
          <div className="card overflow-hidden">
            <h3 className="font-bold px-4 pt-4 pb-2">CBK loan classification</h3>
            <table className="min-w-full text-sm">
              <thead><tr><th className="th">Class</th><th className="th text-right">Loans</th><th className="th text-right">Amount</th><th className="th text-right">Share</th></tr></thead>
              <tbody>
                {Object.entries(d.book.classes).map(([name, c]) => (
                  <tr key={name}><td className="td font-semibold capitalize">{name}</td><td className="td text-right tabnums">{num(c.count)}</td>
                    <td className="td text-right tabnums">{kes(c.amount)}</td>
                    <td className="td text-right tabnums">{d.book.outstanding ? pct(((100 * c.amount) / d.book.outstanding).toFixed(1)) : "—"}</td></tr>
                ))}
              </tbody>
            </table>
            <p className="text-[11px] text-gray-400 px-4 py-2">Days past due: Normal not late, Watch 1–30, Substandard 31–90, Doubtful 91–180, Loss over 180 (the same bands as the CBK report).</p>
          </div>
          <div className="card overflow-hidden">
            <h3 className="font-bold px-4 pt-4 pb-2">Loans by status (all time)</h3>
            <table className="min-w-full text-sm">
              <tbody>
                {Object.entries(d.book.status_counts).sort((a, b) => b[1] - a[1]).map(([s, n]) => (
                  <tr key={s}><td className="td">{STATUS_LABELS[s] || s}</td><td className="td text-right tabnums">{num(n)}</td></tr>
                ))}
                {!Object.keys(d.book.status_counts).length && <tr><td className="td text-gray-400">No loans yet</td></tr>}
              </tbody>
            </table>
            <div className="px-4 py-3 text-sm text-gray-500">PAR 90: {kes(d.book.par90)} · Waiting for a decision or payout: {num(d.book.pending)}</div>
          </div>
        </div>
      )}

      {tab === "services" && (
        <div className="grid lg:grid-cols-2 gap-5">
          <div className="card p-4">
            <h3 className="font-bold mb-3">SMS, last {days} days</h3>
            <div className="grid grid-cols-2 gap-3 text-sm mb-3">
              <div><div className="label">Sent (billable)</div><div className="text-xl font-bold tabnums">{num(d.sms.sent)}</div></div>
              <div><div className="label">Failed</div><div className={`text-xl font-bold tabnums ${d.sms.failed ? "text-red-600" : ""}`}>{num(d.sms.failed)}</div></div>
              <div><div className="label">Charged to the DCP</div><div className="font-semibold tabnums">{kes2(d.sms.revenue)}</div></div>
              <div><div className="label">Our cost (Uwazii)</div><div className="font-semibold tabnums">{kes2(d.sms.cost)}</div></div>
              <div><div className="label">Finyl margin</div><div className="font-semibold tabnums text-accent">{kes2(d.sms.margin)}</div></div>
            </div>
            <table className="min-w-full text-sm">
              <thead><tr><th className="th">Kind of message</th><th className="th text-right">Sent</th></tr></thead>
              <tbody>
                {Object.entries(d.sms.by_type).map(([k, n]) => <tr key={k}><td className="td">{k.replace(/_/g, " ")}</td><td className="td text-right tabnums">{num(n)}</td></tr>)}
                {!Object.keys(d.sms.by_type).length && <tr><td className="td text-gray-400" colSpan={2}>No SMS in this period</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="card p-4">
            <h3 className="font-bold mb-3">Other paid services, last {days} days</h3>
            <table className="min-w-full text-sm">
              <tbody>
                <tr><td className="td">CRB checks</td><td className="td text-right tabnums">{num(d.services.crb_checks)}</td></tr>
                <tr><td className="td">Face checks</td><td className="td text-right tabnums">{num(d.services.face_checks)}</td></tr>
                <tr><td className="td">M-Pesa statement analyses (all time)</td><td className="td text-right tabnums">{num(d.services.statement_analyses_total)}</td></tr>
                {Object.entries(d.services.mpesa).map(([k, v]) => (
                  <tr key={k}><td className="td">M-Pesa {k.replace(/_/g, " ")}</td>
                    <td className="td text-right tabnums">{num(v.count)} · {kes(v.amount)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "billing" && d.billing && (
        <div className="grid lg:grid-cols-2 gap-5">
          <div className="card p-4">
            <h3 className="font-bold mb-3">How this DCP is billed</h3>
            {can("platform.billing_manage")
              ? <TenantBillingForm tenantId={d.id} billing={d.billing} onSaved={reload} />
              : <BillingSummary b={d.billing} />}
          </div>
          <div className="card overflow-hidden">
            <h3 className="font-bold px-4 pt-4 pb-2">Invoices</h3>
            {!d.invoices.length ? <Empty text="No invoices yet" /> : (
              <table className="min-w-full text-sm">
                <thead><tr><th className="th">Number</th><th className="th">Month</th><th className="th text-right">Total</th><th className="th text-right">Balance</th><th className="th">Status</th></tr></thead>
                <tbody>
                  {d.invoices.map((i) => (
                    <tr key={i.id} className="hover:bg-canvas cursor-pointer" onClick={() => nav(`/platform/billing?invoice=${i.id}`)}>
                      <td className="td font-mono text-xs">{i.number}</td><td className="td">{i.period_label}</td>
                      <td className="td text-right tabnums">{kes(i.total)}</td><td className="td text-right tabnums">{kes(i.balance)}</td>
                      <td className="td"><Badge value={i.overdue ? "overdue" : i.status === "void" ? "closed" : i.status === "paid" ? "paid" : "pending"}>{i.overdue ? "overdue" : i.status.replace("_", " ")}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {tab === "lending" && d.facilities && (
        <div className="card overflow-hidden">
          <div className="flex items-center px-4 pt-4 pb-2"><h3 className="font-bold flex-1">Finyl's loans to {d.name}</h3>
            <Link className="btn-ghost !py-1 text-xs" to="/platform/lending">All facilities</Link></div>
          {!d.facilities.length ? <Empty text="No loan facility for this DCP" /> : (
            <table className="min-w-full text-sm">
              <thead><tr><th className="th">Facility</th><th className="th text-right">Limit</th><th className="th text-right">Drawn</th>
                <th className="th text-right">Principal owed</th><th className="th text-right">Arrears</th><th className="th">Class</th><th className="th">Status</th></tr></thead>
              <tbody>
                {d.facilities.map((f) => (
                  <tr key={f.id} className="hover:bg-canvas cursor-pointer" onClick={() => nav(`/platform/lending?facility=${f.id}`)}>
                    <td className="td font-mono text-xs">{f.number}</td><td className="td text-right tabnums">{kes(f.limit_amount)}</td>
                    <td className="td text-right tabnums">{kes(f.drawn)}</td><td className="td text-right tabnums">{kes(f.principal_outstanding)}</td>
                    <td className={`td text-right tabnums ${f.arrears ? "text-red-600 font-semibold" : ""}`}>{kes(f.arrears)}</td>
                    <td className="td">{f.class}</td><td className="td"><Badge value={f.status === "closed" ? "closed" : f.arrears ? "overdue" : "active"}>{f.status}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === "alerts" && (
        <div className="card overflow-hidden">
          {!d.alerts.length ? <Empty text="No open alerts for this DCP" /> : (
            <table className="min-w-full text-sm">
              <thead><tr><th className="th">Severity</th><th className="th">What</th><th className="th text-right">Times</th><th className="th">Last seen</th></tr></thead>
              <tbody>
                {d.alerts.map((a) => (
                  <tr key={a.id}><td className="td"><Severity value={a.severity} /></td><td className="td whitespace-normal">{a.title}</td>
                    <td className="td text-right tabnums">{a.count}</td><td className="td">{new Date(a.last_seen).toLocaleString("en-KE")}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          {can("platform.alerts") && <div className="px-4 py-3"><Link className="text-sm underline" to="/platform/alerts">Manage alerts</Link></div>}
        </div>
      )}

      {tab === "operations" && (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
          <KpiCard label="Clients" value={num(d.operations.clients)} />
          <KpiCard label="Staff" value={num(d.operations.staff)} />
          <KpiCard label="Branches" value={num(d.operations.branches)} />
          <KpiCard label="Open complaints" value={num(d.operations.complaints_open)} />
          <KpiCard label="Complaints past 14 days" value={num(d.operations.complaints_breached)} tone={d.operations.complaints_breached ? "bad" : "good"} />
          <KpiCard label="Last activity" value={d.operations.days_since_activity == null ? "—" : `${d.operations.days_since_activity}d ago`}
            sub={d.operations.last_activity ? fmtDate(d.operations.last_activity) : "No loans or payments yet"} />
          {d.lead && (
            <div className="card p-4 col-span-2">
              <div className="label">Sales contact</div>
              <div className="font-semibold">{d.lead.contact_name || "—"}</div>
              <div className="text-sm text-gray-500">{[d.lead.contact_phone, d.lead.contact_email].filter(Boolean).join(" · ")}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BillingSummary({ b }) {
  const e = b.effective;
  return (
    <dl className="grid grid-cols-2 gap-2 text-sm">
      <dt className="text-gray-500">Billing</dt><dd>{b.billing_active ? `On since ${fmtDate(b.billing_start)}` : "Off"}</dd>
      <dt className="text-gray-500">Onboarding (one-off)</dt><dd>{kes(e.onboarding_fee)} {b.onboarding_invoiced ? "· invoiced" : ""}</dd>
      <dt className="text-gray-500">Monthly subscription</dt><dd>{e.subscription_rate_pct}% of the loan book, at least {kes(e.subscription_minimum)}</dd>
      <dt className="text-gray-500">New branch</dt><dd>{kes(e.branch_fee)} each beyond {e.branches_included}</dd>
    </dl>
  );
}
