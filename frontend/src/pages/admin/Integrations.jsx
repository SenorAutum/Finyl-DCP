// Integrations module — the platform's single home for every external
// integration. Three views:
//   1. Registry     — live status of each integration, masked config, a Test
//                      button and per-integration test history.
//   2. SMS Revenue   — per-DCP SMS usage & revenue (billable 'sent' messages
//                      priced from the active rate card) + platform roll-up.
//   3. Message Logs  — paginated, filterable SMS log with delivery status.
//
// This supersedes the old "DCP Setup" screen (which only showed status chips):
// the Registry tab keeps that capability and adds test history, and the two new
// tabs add the revenue tracking + message auditing the platform now needs.
//
// Adding a NEW integration is a one-place change on the backend (register it in
// REGISTRY with a status() and optional test() callable) — this UI renders it
// automatically from GET /api/v1/integrations. No frontend change required.
import { useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { PageHeader, Spinner, KpiCard, Pagination, Empty, Modal } from "../../components/ui";

// SMS amounts are fractional (sub-shilling per message), so revenue needs more
// precision than the app-wide fmtKES (0 decimals).
const kes = (n, dp = 2) =>
  "KES " + Number(n || 0).toLocaleString("en-KE", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const pct = (r) => (r == null ? "—" : (Number(r) * 100).toFixed(1) + "%");

const STATUS_CHIP = {
  LIVE: "bg-emerald-100 text-emerald-700 ring-emerald-200",
  SANDBOX: "bg-amber-100 text-amber-700 ring-amber-200",
  "NOT CONFIGURED": "bg-gray-200 text-gray-600 ring-gray-300",
  ERROR: "bg-red-100 text-red-700 ring-red-200",
};

function StatusChip({ status }) {
  const cls = STATUS_CHIP[status] || STATUS_CHIP["NOT CONFIGURED"];
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold ring-1 ${cls}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />{status}
    </span>
  );
}

const DELIVERY_CHIP = {
  delivered: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
  undelivered: "bg-amber-100 text-amber-700",
  unknown: "bg-gray-200 text-gray-500",
};
function DeliveryChip({ status }) {
  const cls = DELIVERY_CHIP[status] || DELIVERY_CHIP.unknown;
  return <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold ${cls}`}>{status || "unknown"}</span>;
}

const CONFIG_LABELS = {
  provider: "Provider", base_url: "Base URL", sender_id: "Sender ID",
  access_token: "Access token", environment: "Environment", shortcode: "Shortcode",
  initiator_name: "Initiator", consumer_key: "Consumer key",
  consumer_secret: "Consumer secret", callback_base_url: "Callback base URL",
  username: "Username", password: "Password", strategy_id: "Strategy ID",
  mock_mode: "Mock mode", api_key: "API key", vision_model: "Vision model",
  fallback: "Fallback engine",
};

// ============================================================================
// Registry tab
// ============================================================================
function TestHistory({ integrationKey }) {
  const [logs, setLogs] = useState(null);
  useEffect(() => {
    let alive = true;
    api(`/api/v1/integrations/${integrationKey}/test-logs?limit=15`)
      .then((d) => alive && setLogs(d.logs || []))
      .catch(() => alive && setLogs([]));
    return () => { alive = false; };
  }, [integrationKey]);

  if (logs === null) return <Spinner />;
  if (!logs.length) return <Empty text="No test runs yet." />;
  return (
    <div className="divide-y divide-border">
      {logs.map((l) => (
        <div key={l.id} className="flex items-start gap-3 py-2.5 text-sm">
          <span className={`mt-0.5 font-bold ${l.ok ? "text-accent" : "text-red-600"}`}>{l.ok ? "✓" : "✗"}</span>
          <div className="flex-1 min-w-0">
            <div className="text-charcoal break-words">{l.detail || (l.ok ? "OK" : "Failed")}</div>
            <div className="text-[11px] text-gray-400 mt-0.5">
              {l.at ? new Date(l.at).toLocaleString("en-KE") : "—"}{l.run_by ? ` · ${l.run_by}` : ""}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function IntegrationCard({ item, onTested }) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);
  const [showHistory, setShowHistory] = useState(false);

  const runTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      const res = await api(`/api/v1/integrations/${item.key}/test`, { method: "POST", body: {} });
      setResult(res);
    } catch (e) {
      setResult({ ok: false, detail: e.detail });
    } finally {
      setTesting(false);
      onTested && onTested();
    }
  };

  return (
    <div className="card p-5 flex flex-col">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h3 className="text-base font-bold text-charcoal truncate">{item.name}</h3>
          <div className="text-[11px] text-gray-400 uppercase tracking-wider">
            {item.category} · {item.provider}
          </div>
        </div>
        <StatusChip status={item.status} />
      </div>

      <div className="rounded-lg bg-gray-50 border border-border/70 px-3 py-2 mb-3">
        {Object.entries(item.config || {}).map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4 py-1 text-[13px] border-b border-border/50 last:border-0">
            <span className="text-gray-500">{CONFIG_LABELS[k] || k}</span>
            <span className="font-medium text-charcoal text-right break-all">
              {typeof v === "boolean" ? (v ? "Yes" : "No") : String(v ?? "—") || "—"}
            </span>
          </div>
        ))}
      </div>

      {item.status === "NOT CONFIGURED" && (
        <div className="text-[12px] bg-gray-50 border border-border rounded-lg p-2.5 text-gray-500 mb-3">
          Credential-gated — supply the secret(s) above in the server environment and
          this integration activates automatically. No code change needed.
        </div>
      )}

      <div className="mt-auto flex items-center gap-2 flex-wrap">
        {item.testable ? (
          <button className="btn-primary !py-1.5" disabled={testing} onClick={runTest}>
            {testing ? "Testing…" : "Test connection"}
          </button>
        ) : (
          <span className="text-[12px] text-gray-400">No live probe — status is credential-derived.</span>
        )}
        <button className="btn-ghost !py-1.5" onClick={() => setShowHistory(true)}>History</button>
      </div>

      {result && (
        <div className={`mt-3 text-sm rounded-lg p-3 ${result.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"}`}>
          <div className="font-semibold">{result.ok ? "✓ Test succeeded" : "✗ Test failed"}</div>
          <div className="mt-0.5 text-[13px] break-words">{result.detail}</div>
          {result.provider_ref && (
            <div className="mt-1 text-[12px] opacity-80">Provider ref: <b>{result.provider_ref}</b></div>
          )}
        </div>
      )}

      {showHistory && (
        <Modal title={`${item.name} — test history`} onClose={() => setShowHistory(false)}>
          <TestHistory integrationKey={item.key} />
        </Modal>
      )}
    </div>
  );
}

function RegistryTab() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [nonce, setNonce] = useState(0);

  const load = () => api("/api/v1/integrations")
    .then((d) => setData(d.integrations || []))
    .catch((e) => setErr(e.detail));

  useEffect(() => { load(); }, [nonce]);

  if (err) return <div className="text-sm text-red-600 bg-red-50 rounded-lg p-3">{err}</div>;
  if (!data) return <Spinner />;

  return (
    <div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        Every external integration with its live credential state — <b>LIVE</b> (production),
        <b> SANDBOX</b> (test) or <b>NOT CONFIGURED</b>. Run a real connectivity test on any
        integration; results are recorded and viewable under <b>History</b>.
      </p>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {data.map((it) => <IntegrationCard key={it.key} item={it} onTested={() => setNonce((n) => n + 1)} />)}
      </div>
    </div>
  );
}

// ============================================================================
// SMS Revenue tab
// ============================================================================
function RangeFilters({ from, to, trigger, tenant, tenants, isSuper, onChange }) {
  return (
    <div className="flex flex-wrap items-end gap-3 mb-4">
      <div>
        <label className="block text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">From</label>
        <input type="date" className="input !w-auto" value={from} onChange={(e) => onChange({ from: e.target.value })} />
      </div>
      <div>
        <label className="block text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">To</label>
        <input type="date" className="input !w-auto" value={to} onChange={(e) => onChange({ to: e.target.value })} />
      </div>
      <div>
        <label className="block text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">Trigger</label>
        <select className="input !w-auto" value={trigger} onChange={(e) => onChange({ trigger: e.target.value })}>
          <option value="">All triggers</option>
          <option value="manual">Manual</option>
          <option value="loan_disbursed">Loan disbursed</option>
          <option value="repayment_received">Repayment received</option>
          <option value="loan_overdue">Loan overdue</option>
          <option value="otp">OTP</option>
        </select>
      </div>
      {isSuper && (
        <div>
          <label className="block text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">DCP</label>
          <select className="input !w-auto" value={tenant} onChange={(e) => onChange({ tenant: e.target.value })}>
            <option value="">All DCPs</option>
            {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </div>
      )}
    </div>
  );
}

function RevenueTab({ isSuper, tenants }) {
  const [f, setF] = useState({ from: "", to: "", trigger: "", tenant: "" });
  const [data, setData] = useState(null);
  const [rate, setRate] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => { api("/api/v1/integrations/sms/rate").then(setRate).catch(() => {}); }, []);

  useEffect(() => {
    setLoading(true);
    const q = new URLSearchParams();
    if (f.from) q.set("from", f.from);
    if (f.to) q.set("to", f.to);
    if (f.trigger) q.set("trigger_type", f.trigger);
    if (f.tenant) q.set("tenant_id", f.tenant);
    api(`/api/v1/integrations/sms/usage?${q.toString()}`)
      .then((d) => { setData(d); setErr(""); })
      .catch((e) => setErr(e.detail))
      .finally(() => setLoading(false));
  }, [f]);

  const onChange = (patch) => setF((prev) => ({ ...prev, ...patch }));
  const roll = data?.rollup;

  return (
    <div>
      <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
        <p className="text-sm text-gray-500 max-w-2xl">
          Revenue is recognised on <b>billable</b> messages (successfully sent), priced from the
          active SMS rate card at send time.
        </p>
        {rate?.configured && (
          <div className="text-[12px] text-gray-500 bg-gray-50 border border-border rounded-lg px-3 py-1.5">
            Active rate — sell <b>{kes(rate.sell_price_kes, 2)}</b> · cost <b>{kes(rate.cost_price_kes, 2)}</b> ·
            margin <b>{kes(rate.margin_kes, 2)}</b> / SMS
          </div>
        )}
      </div>

      <RangeFilters {...f} tenant={f.tenant} tenants={tenants} isSuper={isSuper} onChange={onChange} />

      {err && <div className="text-sm text-red-600 bg-red-50 rounded-lg p-3 mb-4">{err}</div>}
      {loading || !data ? <Spinner /> : (
        <>
          <div className="grid gap-4 grid-cols-2 lg:grid-cols-4 mb-5">
            <KpiCard label="Messages sent" value={roll.messages_sent.toLocaleString()} />
            <KpiCard label="Delivery rate" value={pct(roll.delivery_rate)}
              sub={`${roll.messages_delivered.toLocaleString()} delivered`} />
            <KpiCard label="Billable" value={roll.billable_count.toLocaleString()} tone="good" />
            <KpiCard label="Revenue (sell)" value={kes(roll.total_sell_kes)} tone="good"
              sub={`Margin ${kes(roll.total_margin_kes)}`} />
          </div>

          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left">
                    <th className="th">DCP</th>
                    <th className="th text-right">Sent</th>
                    <th className="th text-right">Delivered</th>
                    <th className="th text-right">Delivery %</th>
                    <th className="th text-right">Billable</th>
                    <th className="th text-right">Sell</th>
                    <th className="th text-right">Cost</th>
                    <th className="th text-right">Margin</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.length === 0 && (
                    <tr><td colSpan={8}><Empty text="No SMS activity in this period." /></td></tr>
                  )}
                  {data.rows.map((r) => (
                    <tr key={r.tenant_id} className="border-t border-border">
                      <td className="td">
                        <div className="font-semibold text-charcoal">{r.tenant}</div>
                        {r.code && <div className="text-[11px] text-gray-400">{r.code}</div>}
                      </td>
                      <td className="td text-right">{r.messages_sent.toLocaleString()}</td>
                      <td className="td text-right">{r.messages_delivered.toLocaleString()}</td>
                      <td className="td text-right">{pct(r.delivery_rate)}</td>
                      <td className="td text-right">{r.billable_count.toLocaleString()}</td>
                      <td className="td text-right font-medium">{kes(r.total_sell_kes)}</td>
                      <td className="td text-right text-gray-500">{kes(r.total_cost_kes)}</td>
                      <td className="td text-right font-semibold text-accent">{kes(r.total_margin_kes)}</td>
                    </tr>
                  ))}
                </tbody>
                {data.rows.length > 0 && (
                  <tfoot>
                    <tr className="border-t-2 border-border bg-gray-50 font-bold">
                      <td className="td">Total ({data.rows.length} DCP{data.rows.length === 1 ? "" : "s"})</td>
                      <td className="td text-right">{roll.messages_sent.toLocaleString()}</td>
                      <td className="td text-right">{roll.messages_delivered.toLocaleString()}</td>
                      <td className="td text-right">{pct(roll.delivery_rate)}</td>
                      <td className="td text-right">{roll.billable_count.toLocaleString()}</td>
                      <td className="td text-right">{kes(roll.total_sell_kes)}</td>
                      <td className="td text-right">{kes(roll.total_cost_kes)}</td>
                      <td className="td text-right text-accent">{kes(roll.total_margin_kes)}</td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          </div>
          <div className="text-[11px] text-gray-400 mt-2">
            Period {data.from} → {data.to} · scope: {data.scope}
          </div>
        </>
      )}
    </div>
  );
}

// ============================================================================
// Message Logs tab
// ============================================================================
function LogsTab({ isSuper, tenants }) {
  const [f, setF] = useState({ from: "", to: "", trigger: "", tenant: "", status: "", delivery_status: "", phone: "" });
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const pageSize = 25;

  useEffect(() => {
    setLoading(true);
    const q = new URLSearchParams();
    if (f.from) q.set("from", f.from);
    if (f.to) q.set("to", f.to);
    if (f.trigger) q.set("trigger_type", f.trigger);
    if (f.tenant) q.set("tenant_id", f.tenant);
    if (f.status) q.set("status", f.status);
    if (f.delivery_status) q.set("delivery_status", f.delivery_status);
    if (f.phone) q.set("phone", f.phone);
    q.set("page", page);
    q.set("page_size", pageSize);
    api(`/api/v1/integrations/sms/logs?${q.toString()}`)
      .then((d) => { setData(d); setErr(""); })
      .catch((e) => setErr(e.detail))
      .finally(() => setLoading(false));
  }, [f, page]);

  const onChange = (patch) => { setPage(1); setF((prev) => ({ ...prev, ...patch })); };

  return (
    <div>
      <div className="flex flex-wrap items-end gap-3 mb-4">
        <div>
          <label className="block text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">From</label>
          <input type="date" className="input !w-auto" value={f.from} onChange={(e) => onChange({ from: e.target.value })} />
        </div>
        <div>
          <label className="block text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">To</label>
          <input type="date" className="input !w-auto" value={f.to} onChange={(e) => onChange({ to: e.target.value })} />
        </div>
        <div>
          <label className="block text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">Send status</label>
          <select className="input !w-auto" value={f.status} onChange={(e) => onChange({ status: e.target.value })}>
            <option value="">All</option>
            <option value="sent">Sent</option>
            <option value="failed">Failed</option>
            <option value="skipped">Skipped</option>
          </select>
        </div>
        <div>
          <label className="block text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">Delivery</label>
          <select className="input !w-auto" value={f.delivery_status} onChange={(e) => onChange({ delivery_status: e.target.value })}>
            <option value="">All</option>
            <option value="delivered">Delivered</option>
            <option value="failed">Failed</option>
            <option value="undelivered">Undelivered</option>
            <option value="unknown">Unknown</option>
          </select>
        </div>
        <div>
          <label className="block text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">Phone</label>
          <input className="input !w-auto" placeholder="07…" value={f.phone} onChange={(e) => onChange({ phone: e.target.value })} />
        </div>
        {isSuper && (
          <div>
            <label className="block text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">DCP</label>
            <select className="input !w-auto" value={f.tenant} onChange={(e) => onChange({ tenant: e.target.value })}>
              <option value="">All DCPs</option>
              {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
        )}
      </div>

      {err && <div className="text-sm text-red-600 bg-red-50 rounded-lg p-3 mb-4">{err}</div>}
      {loading || !data ? <Spinner /> : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left">
                  {isSuper && <th className="th">DCP</th>}
                  <th className="th">Recipient</th>
                  <th className="th">Trigger</th>
                  <th className="th">Send</th>
                  <th className="th">Delivery</th>
                  <th className="th text-right">Sell</th>
                  <th className="th">Sent at</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.length === 0 && (
                  <tr><td colSpan={isSuper ? 7 : 6}><Empty text="No messages match these filters." /></td></tr>
                )}
                {data.rows.map((r) => (
                  <tr key={r.id} className="border-t border-border align-top">
                    {isSuper && <td className="td">{r.tenant || `#${r.tenant_id}`}</td>}
                    <td className="td font-medium">{r.recipient_phone}</td>
                    <td className="td"><span className="text-[12px] text-gray-500">{(r.trigger_type || "—").replace(/_/g, " ")}</span></td>
                    <td className="td">
                      <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                        r.status === "sent" ? "bg-emerald-100 text-emerald-700" : "bg-gray-200 text-gray-600"}`}>
                        {r.status}
                      </span>
                    </td>
                    <td className="td"><DeliveryChip status={r.delivery_status} /></td>
                    <td className="td text-right">{r.billable ? kes(r.sell_price_kes, 2) : "—"}</td>
                    <td className="td text-[12px] text-gray-500">{fmtDate(r.sent_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={data.page} total={data.total} pageSize={data.page_size} onPage={setPage} />
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Shell
// ============================================================================
const TABS = [
  { key: "registry", label: "Registry" },
  { key: "revenue", label: "SMS Revenue" },
  { key: "logs", label: "Message Logs" },
];

export default function Integrations() {
  const [tab, setTab] = useState("registry");
  const [me, setMe] = useState(null);
  const [tenants, setTenants] = useState([]);

  useEffect(() => {
    api("/api/v1/auth/me").then(setMe).catch(() => setMe({}));
  }, []);
  useEffect(() => {
    if (me?.role === "super_admin") api("/api/v1/auth/tenants").then(setTenants).catch(() => {});
  }, [me]);

  const isSuper = me?.role === "super_admin";

  return (
    <div>
      <PageHeader title="Integrations" crumbs={["Platform", "Integrations"]} />
      <div className="flex flex-wrap gap-2 mb-5 border-b border-border">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-3 py-2 text-sm font-semibold -mb-px border-b-2 transition-colors ${
              tab === t.key ? "border-accent text-accent" : "border-transparent text-gray-500 hover:text-charcoal"}`}>
            {t.label}
          </button>
        ))}
      </div>

      {!me ? <Spinner /> : (
        <>
          {tab === "registry" && <RegistryTab />}
          {tab === "revenue" && <RevenueTab isSuper={isSuper} tenants={tenants} />}
          {tab === "logs" && <LogsTab isSuper={isSuper} tenants={tenants} />}
        </>
      )}
    </div>
  );
}
