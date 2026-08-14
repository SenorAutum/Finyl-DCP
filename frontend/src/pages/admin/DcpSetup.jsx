// DCP Setup — System Admin view of every live integration, with status chips
// and 'Test connection' actions. Mirrors the Cortex DCP-setup layout (tabs for
// SMS, M-Pesa, eKYC, CRB, ID OCR, CBK Reporting). Nothing is faked: each chip
// reflects the backend's real credential state (LIVE / SANDBOX / NOT CONFIGURED).
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { PageHeader, Spinner } from "../../components/ui";

const CHIP = {
  LIVE: "bg-emerald-100 text-emerald-700 ring-emerald-200",
  SANDBOX: "bg-amber-100 text-amber-700 ring-amber-200",
  "NOT CONFIGURED": "bg-gray-200 text-gray-600 ring-gray-300",
  ERROR: "bg-red-100 text-red-700 ring-red-200",
};

function StatusChip({ status }) {
  const cls = CHIP[status] || CHIP["NOT CONFIGURED"];
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-bold ring-1 ${cls}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />{status}
    </span>
  );
}

function Field({ label, value }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 border-b border-border/60 last:border-0 text-sm">
      <span className="text-gray-500">{label}</span>
      <span className="font-medium text-charcoal text-right break-all">{value || "—"}</span>
    </div>
  );
}

function TestResult({ result }) {
  if (!result) return null;
  const ok = result.ok;
  return (
    <div className={`mt-3 text-sm rounded-lg p-3 ${ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"}`}>
      <div className="font-semibold">{ok ? "✓ Test succeeded" : "✗ Test failed"}</div>
      <pre className="mt-1 whitespace-pre-wrap text-xs opacity-90">{JSON.stringify(result, null, 2)}</pre>
    </div>
  );
}

const CONFIG_LABELS = {
  provider: "Provider", base_url: "Base URL", sender_id: "Sender ID",
  access_token: "Access token", environment: "Environment", shortcode: "Shortcode",
  initiator_name: "Initiator", consumer_key: "Consumer key",
  consumer_secret: "Consumer secret", callback_base_url: "Callback base URL",
  username: "Username", strategy_id: "Strategy ID", mock_mode: "Mock mode",
  api_key: "API key", vision_model: "Vision model", fallback: "Fallback engine",
};

function IntegrationPanel({ item, onTest, testing, result }) {
  const testable = ["sms", "mpesa", "ekyc", "crb"].includes(item.key);
  return (
    <div>
      <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div>
          <h2 className="text-lg font-bold">{item.name}</h2>
          <div className="text-xs text-gray-400 uppercase tracking-wider">{item.category}</div>
        </div>
        <StatusChip status={item.status} />
      </div>

      <div className="card p-4">
        {Object.entries(item.config || {}).map(([k, v]) => (
          <Field key={k} label={CONFIG_LABELS[k] || k}
            value={typeof v === "boolean" ? (v ? "Yes" : "No") : String(v ?? "")} />
        ))}
      </div>

      {item.status === "NOT CONFIGURED" && (
        <div className="mt-3 text-sm bg-gray-50 border border-border rounded-lg p-3 text-gray-500">
          This integration is <b>credential-gated</b>. Add the secret(s) above to the
          server environment and it will activate automatically — no code change needed.
        </div>
      )}

      {testable && (
        <div className="mt-4">
          {item.key === "sms" ? (
            <SmsTester onTest={onTest} testing={testing} />
          ) : (
            <button className="btn-primary" disabled={testing}
              onClick={() => onTest(item.key)}>
              {testing ? "Testing…" : "Test connection"}
            </button>
          )}
          <TestResult result={result} />
        </div>
      )}
    </div>
  );
}

function SmsTester({ onTest, testing }) {
  const [phone, setPhone] = useState("");
  const [message, setMessage] = useState("Finyl DCP test — Uwazii SMS is live.");
  return (
    <div className="space-y-2 max-w-md">
      <input className="input w-full" placeholder="Phone e.g. 0712345678"
        value={phone} onChange={(e) => setPhone(e.target.value)} />
      <textarea className="input w-full" rows={2} value={message}
        onChange={(e) => setMessage(e.target.value)} />
      <button className="btn-primary" disabled={testing || !phone}
        onClick={() => onTest("sms", { phone, message })}>
        {testing ? "Sending…" : "Send test SMS"}
      </button>
    </div>
  );
}

function CbkPanel({ cbk }) {
  return (
    <div>
      <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div>
          <h2 className="text-lg font-bold">{cbk.name}</h2>
          <div className="text-xs text-gray-400 uppercase tracking-wider">{cbk.category}</div>
        </div>
      </div>
      <p className="text-sm text-gray-500 mb-4">{cbk.description}</p>
      <div className="card divide-y divide-border">
        {(cbk.tenants || []).map((t) => (
          <div key={t.tenant_id} className="flex items-center justify-between px-4 py-3">
            <div>
              <div className="font-semibold text-sm">{t.tenant}</div>
              <div className="text-xs text-gray-400">{t.code}</div>
            </div>
            <StatusChip status={t.enabled ? "LIVE" : "NOT CONFIGURED"} />
          </div>
        ))}
      </div>
      <div className="mt-3 text-xs text-gray-400">
        Toggle CBK Reporting per tenant in the Super Admin → Module Matrix.
      </div>
    </div>
  );
}

export default function DcpSetup() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState(0);
  const [testing, setTesting] = useState(false);
  const [results, setResults] = useState({});

  useEffect(() => {
    api("/api/v1/integrations/status").then(setData).catch((e) => setErr(e.detail));
  }, []);

  const runTest = async (key, body) => {
    setTesting(true);
    try {
      const endpoint = { sms: "test-sms", mpesa: "test-mpesa", ekyc: "test-ekyc", crb: "test-crb" }[key];
      const res = await api(`/api/v1/integrations/${endpoint}`, { method: "POST", body: body || {} });
      setResults((r) => ({ ...r, [key]: res }));
    } catch (e) {
      setResults((r) => ({ ...r, [key]: { ok: false, detail: e.detail } }));
    } finally {
      setTesting(false);
    }
  };

  if (err) return <div className="text-sm text-red-600 bg-red-50 rounded-lg p-3">{err}</div>;
  if (!data) return <Spinner />;

  const tabs = [
    ...data.integrations.map((it) => ({ label: it.name, key: it.key, item: it })),
    { label: data.cbk_reporting.name, key: "cbk", cbk: data.cbk_reporting },
  ];
  const active = tabs[tab];

  return (
    <div>
      <PageHeader title="DCP Setup — Integrations" crumbs={["Platform", "DCP Setup"]} />
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        Live status of every external integration for this Digital Credit Provider.
        Chips reflect real credential state — <b>LIVE</b> (configured &amp; production),
        <b> SANDBOX</b> (test environment) or <b>NOT CONFIGURED</b> (credential-gated,
        activates automatically once secrets are supplied).
      </p>

      <div className="flex flex-wrap gap-2 mb-5 border-b border-border">
        {tabs.map((t, i) => (
          <button key={t.key} onClick={() => setTab(i)}
            className={`px-3 py-2 text-sm font-semibold -mb-px border-b-2 transition-colors ${
              i === tab ? "border-accent text-accent" : "border-transparent text-gray-500 hover:text-charcoal"}`}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="max-w-2xl">
        {active.key === "cbk"
          ? <CbkPanel cbk={active.cbk} />
          : <IntegrationPanel item={active.item} onTest={runTest}
              testing={testing} result={results[active.key]} />}
      </div>
    </div>
  );
}
