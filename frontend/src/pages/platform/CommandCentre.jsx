// WP13 — the Finyl command centre: every DCP this person may see on one screen.
// Click a DCP for its full dashboard. Data: GET /api/v1/platform/overview.
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Empty, KpiCard, PageHeader, Spinner } from "../../components/ui";
import { ErrorNote, Health, PlatformTabs, exportCsv, kes, num, pct, useLoad, usePlatform } from "./common";

const PERIODS = [[7, "7 days"], [30, "30 days"], [90, "90 days"], [365, "12 months"]];

export default function CommandCentre() {
  const [days, setDays] = useState(30);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState(["outstanding", -1]);
  const { data, error, loading } = useLoad(`/api/v1/platform/overview?days=${days}`);
  const { can, platform, isSuper } = usePlatform();
  const nav = useNavigate();

  const rows = useMemo(() => {
    const list = (data?.tenants || []).filter((t) => !q || t.name.toLowerCase().includes(q.toLowerCase()));
    const key = {
      name: (t) => t.name.toLowerCase(), health: (t) => t.health.score, outstanding: (t) => t.book.outstanding,
      par: (t) => t.book.par30_pct, disbursed: (t) => t.window.disbursed, collected: (t) => t.window.collected,
      sms: (t) => t.sms.sent, alerts: (t) => t.alerts.critical * 1000 + t.alerts.warning,
    }[sort[0]];
    return [...list].sort((a, b) => (key(a) > key(b) ? 1 : key(a) < key(b) ? -1 : 0) * sort[1]);
  }, [data, q, sort]);

  const T = data?.totals || {};
  const Th = ({ k, children, right }) => (
    <th className={`th cursor-pointer select-none ${right ? "text-right" : ""}`}
      onClick={() => setSort(([s, d]) => [k, s === k ? -d : -1])}>
      {children}{sort[0] === k ? (sort[1] < 0 ? " ↓" : " ↑") : ""}
    </th>
  );

  const doExport = () => exportCsv(`finyl-dcps-${days}d.csv`, [
    ["DCP", "name"], ["Code", "code"], ["Active", (t) => (t.active ? "yes" : "no")],
    ["Health score", (t) => t.health.score], ["Health", (t) => t.health.band],
    ["Loan book (KES)", (t) => t.book.outstanding], ["Open loans", (t) => t.book.open_loans],
    ["PAR30 (KES)", (t) => t.book.par30], ["PAR30 %", (t) => t.book.par30_pct],
    [`Disbursed ${days}d (KES)`, (t) => t.window.disbursed], [`Collected ${days}d (KES)`, (t) => t.window.collected],
    ["Collected / disbursed %", (t) => t.window.collection_ratio], ["Clients", (t) => t.operations.clients],
    ["Branches", (t) => t.operations.branches], [`SMS sent ${days}d`, (t) => t.sms.sent],
    [`SMS revenue ${days}d (KES)`, (t) => t.sms.revenue], [`SMS margin ${days}d (KES)`, (t) => t.sms.margin],
    ["Open critical alerts", (t) => t.alerts.critical], ["Open warnings", (t) => t.alerts.warning],
  ], rows);

  return (
    <div>
      <PageHeader title="Finyl command centre" crumbs={["Finyl", "Command centre"]}
        actions={
          <div className="flex gap-2 items-center">
            <select className="input !w-auto text-sm" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {PERIODS.map(([d, l]) => <option key={d} value={d}>Last {l}</option>)}
            </select>
            <button className="btn-ghost" onClick={doExport} disabled={!rows.length}>Export CSV</button>
          </div>} />
      <PlatformTabs />
      {platform && !platform.all_tenants && (
        <div className="text-xs text-gray-500 mb-3">
          You see the {platform.tenant_ids.length} DCP{platform.tenant_ids.length === 1 ? "" : "s"} assigned to you
          {platform.title ? ` as ${platform.title}` : ""}.
        </div>
      )}
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-3">
            <KpiCard variant="hero" label="Loan book (all DCPs)" value={kes(T.outstanding)}
              sub={`${num(T.clients)} clients · ${T.live}/${T.dcps} DCPs live`} />
            <KpiCard label="PAR 30" value={kes(T.par30)}
              sub={T.outstanding ? `${((100 * T.par30) / T.outstanding).toFixed(1)}% of the book` : "—"}
              tone={T.outstanding && T.par30 / T.outstanding > 0.1 ? "bad" : "default"} />
            <KpiCard label={`Disbursed · ${days}d`} value={kes(T.disbursed)} />
            <KpiCard label={`Collected · ${days}d`} value={kes(T.collected)}
              sub={T.disbursed ? `${((100 * T.collected) / T.disbursed).toFixed(0)}% of disbursed` : ""} tone="good" />
            <KpiCard label={`SMS · ${days}d`} value={num(T.sms_sent)} sub={`Revenue ${kes(T.sms_revenue)} · margin ${kes(T.sms_margin)}`} />
            <KpiCard label="Open alerts" value={num(T.open_alerts)} sub={`${T.red} DCP(s) in red`}
              tone={T.red || T.open_alerts ? "warn" : "good"} />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            {can("platform.revenue") && (
              <Link to="/platform/billing" className="card p-3 hover:shadow-card-hover">
                <div className="text-[11px] font-bold uppercase text-gray-500">Unpaid invoices</div>
                <div className="text-lg font-extrabold tabnums">{kes(T.unpaid_invoices)}</div>
                <div className="text-xs text-gray-400">{T.last_invoiced ? `${T.last_invoiced.label}: ${kes(T.last_invoiced.total)} invoiced` : "No invoices yet"}</div>
              </Link>)}
            {can("platform.lending") && (
              <Link to="/platform/lending" className="card p-3 hover:shadow-card-hover">
                <div className="text-[11px] font-bold uppercase text-gray-500">Finyl lent to DCPs</div>
                <div className="text-lg font-extrabold tabnums">{kes(T.lent_outstanding)}</div>
                <div className={`text-xs ${T.lent_arrears ? "text-red-600 font-semibold" : "text-gray-400"}`}>Arrears {kes(T.lent_arrears)}</div>
              </Link>)}
            {can("platform.pipeline") && (
              <Link to="/platform/pipeline" className="card p-3 hover:shadow-card-hover">
                <div className="text-[11px] font-bold uppercase text-gray-500">Potential DCPs</div>
                <div className="text-lg font-extrabold tabnums">{num(T.pipeline_open)}</div>
                <div className="text-xs text-gray-400">in the sales pipeline</div>
              </Link>)}
            {can("platform.alerts") && (
              <Link to="/platform/alerts" className="card p-3 hover:shadow-card-hover">
                <div className="text-[11px] font-bold uppercase text-gray-500">Alerts</div>
                <div className="text-lg font-extrabold tabnums">{num(T.open_alerts)} open</div>
                <div className="text-xs text-gray-400">failures across Finyl</div>
              </Link>)}
          </div>

          <div className="card overflow-hidden">
            <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
              <h3 className="font-bold flex-1">DCPs</h3>
              <input className="input !w-56 text-sm" placeholder="Find a DCP…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            {!rows.length ? <Empty text={data.tenants.length ? "No DCP matches" : "No DCPs assigned to you yet"} /> : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead><tr>
                    <Th k="name">DCP</Th><Th k="health">Health</Th><Th k="outstanding" right>Loan book</Th>
                    <Th k="par" right>PAR 30</Th><Th k="disbursed" right>Disbursed</Th><Th k="collected" right>Collected</Th>
                    <th className="th text-right">Coll. %</th><th className="th text-right">Clients</th>
                    <Th k="sms" right>SMS</Th><th className="th text-right">SMS revenue</th><Th k="alerts" right>Alerts</Th>
                  </tr></thead>
                  <tbody>
                    {rows.map((t) => (
                      <tr key={t.id} className="hover:bg-canvas cursor-pointer" onClick={() => nav(`/platform/dcp/${t.id}`)}>
                        <td className="td">
                          <div className="flex items-center gap-2">
                            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: t.color || "#10B981" }} />
                            <span className="font-semibold">{t.name}</span>
                            {!t.active && <span className="text-[10px] font-bold uppercase text-gray-400">off</span>}
                          </div>
                        </td>
                        <td className="td"><Health health={t.health} /></td>
                        <td className="td text-right tabnums">{kes(t.book.outstanding)}</td>
                        <td className={`td text-right tabnums ${t.book.par30_pct > 10 ? "text-red-600 font-semibold" : ""}`}>{pct(t.book.par30_pct)}</td>
                        <td className="td text-right tabnums">{kes(t.window.disbursed)}</td>
                        <td className="td text-right tabnums">{kes(t.window.collected)}</td>
                        <td className="td text-right tabnums">{pct(t.window.collection_ratio)}</td>
                        <td className="td text-right tabnums">{num(t.operations.clients)}</td>
                        <td className="td text-right tabnums">{num(t.sms.sent)}{t.sms.failed ? <span className="text-red-500"> ({t.sms.failed} failed)</span> : null}</td>
                        <td className="td text-right tabnums">{kes(t.sms.revenue)}</td>
                        <td className="td text-right tabnums">
                          {t.alerts.critical ? <span className="text-red-600 font-bold">{t.alerts.critical}</span> : null}
                          {t.alerts.critical && t.alerts.warning ? " / " : null}
                          {t.alerts.warning ? <span className="text-amber-600">{t.alerts.warning}</span> : null}
                          {!t.alerts.critical && !t.alerts.warning ? <span className="text-gray-300">0</span> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          {isSuper && (
            <p className="text-xs text-gray-400 mt-3">
              The earlier all-tenants table (subscriptions, reminders, complaint SLAs) is still at{" "}
              <Link className="underline" to="/platform/classic">Classic view</Link>.
            </p>
          )}
        </>
      )}
    </div>
  );
}
