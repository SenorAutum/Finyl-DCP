// Credit analysis panel for a client: M-Pesa statement affordability + external
// lender detection, and a Credit Reference Bureau (CRB) check. Both hit LIVE
// backend endpoints; the CRB card degrades gracefully when the bureau is
// credential-gated (status 'not_configured') instead of faking a score.
import { useEffect, useState } from "react";
import { api, upload, fmtKES, fmtDate } from "../../lib/api";
import { useAuth } from "../../hooks/useAuth";

function ScoreGauge({ score }) {
  const s = Math.max(0, Math.min(100, score || 0));
  const tone = s >= 70 ? "text-emerald-600" : s >= 45 ? "text-amber-600" : "text-red-600";
  const bar = s >= 70 ? "bg-emerald-500" : s >= 45 ? "bg-amber-500" : "bg-red-500";
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className={`text-3xl font-extrabold ${tone}`}>{s}</span>
        <span className="text-xs text-gray-400">/ 100 affordability</span>
      </div>
      <div className="mt-1 h-2 w-full rounded-full bg-gray-200 overflow-hidden">
        <div className={`h-full ${bar}`} style={{ width: `${s}%` }} />
      </div>
    </div>
  );
}

function Stat({ label, value, tone }) {
  const t = tone === "bad" ? "text-red-600" : tone === "good" ? "text-emerald-600" : "text-charcoal";
  return (
    <div>
      <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`text-sm font-semibold mt-0.5 ${t}`}>{value}</div>
    </div>
  );
}

function StatementCard({ clientId, onScore }) {
  const { can } = useAuth();
  const [a, setA] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [file, setFile] = useState(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api(`/api/v1/clients/${clientId}/mpesa-statement`)
      .then((d) => setA(d)).catch(() => {}).finally(() => setLoaded(true));
  }, [clientId]);

  const submit = async () => {
    if (!file) return;
    setBusy(true); setErr("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const qs = password ? `?password=${encodeURIComponent(password)}` : "";
      const res = await upload(`/api/v1/clients/${clientId}/mpesa-statement${qs}`, fd);
      setA(res); setFile(null); setPassword("");
      if (onScore) onScore();
    } catch (e) { setErr(e.detail || "Analysis failed"); }
    finally { setBusy(false); }
  };

  const s = a?.summary || {};
  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3 border-b border-border font-bold text-base flex items-center justify-between">
        <span>M-Pesa Statement Analysis</span>
        {a?.tampering_suspected && (
          <span className="text-[11px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-700">
            ⚠ Tampering suspected
          </span>
        )}
      </div>

      <div className="p-5 space-y-4">
        {can("clients.edit") && (
          <div className="bg-canvas/60 rounded-lg p-3 space-y-2">
            <div className="text-xs text-gray-500">
              Upload the borrower's official Safaricom M-Pesa statement PDF. Parsed on-server
              (no third-party API). Locked PDFs default to the client's National ID as password.
            </div>
            <input type="file" accept="application/pdf"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              className="block w-full text-sm" />
            <input className="input" placeholder="PDF password (optional — defaults to National ID)"
              value={password} onChange={(e) => setPassword(e.target.value)} />
            <button className="btn-primary" disabled={!file || busy} onClick={submit}>
              {busy ? "Analysing…" : "Analyse statement"}
            </button>
            {err && <div className="text-sm text-red-600">{err}</div>}
          </div>
        )}

        {!loaded ? null : !a ? (
          <div className="text-sm text-gray-400">No statement analysed yet.</div>
        ) : (
          <>
            <ScoreGauge score={a.affordability_score} />

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <Stat label="Period" value={
                s.period_start ? `${fmtDate(s.period_start)} → ${fmtDate(s.period_end)}` : "—"} />
              <Stat label="Months" value={s.months_covered ?? a.months_covered ?? "—"} />
              <Stat label="Transactions" value={a.transactions_count ?? "—"} />
              <Stat label="Avg monthly in" value={fmtKES(s.avg_monthly_inflow)} tone="good" />
              <Stat label="Avg monthly out" value={fmtKES(s.avg_monthly_outflow)} />
              <Stat label="Net monthly" value={fmtKES(a.net_monthly_cash_flow)}
                tone={a.net_monthly_cash_flow >= 0 ? "good" : "bad"} />
              <Stat label="Avg balance" value={fmtKES(s.avg_balance)} />
              <Stat label="Monthly debt service" value={fmtKES(a.monthly_debt_service)}
                tone={a.monthly_debt_service > 0 ? "bad" : undefined} />
              <Stat label="Comfortable installment" value={fmtKES(a.comfortable_installment)} tone="good" />
            </div>

            <div>
              <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1.5">
                External lenders detected ({(a.detected_lenders || []).length})
              </div>
              {(a.detected_lenders || []).length === 0 ? (
                <div className="text-sm text-emerald-600">None — no other digital lenders found.</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead><tr>
                      <th className="th">Lender</th><th className="th">Type</th>
                      <th className="th">Borrowed</th><th className="th">Repaid</th><th className="th">Txns</th>
                    </tr></thead>
                    <tbody>
                      {a.detected_lenders.map((l, i) => (
                        <tr key={i} className="border-t border-border">
                          <td className="td font-semibold">{l.name}</td>
                          <td className="td capitalize">{(l.category || "").replace(/_/g, " ")}</td>
                          <td className="td">{fmtKES(l.borrowed)}</td>
                          <td className="td">{fmtKES(l.repaid)}</td>
                          <td className="td">{l.count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {(a.integrity_flags || []).length > 0 && (
              <div className="text-sm bg-amber-50 text-amber-700 rounded-lg p-3">
                <div className="font-semibold mb-1">Integrity checks</div>
                <ul className="list-disc ml-4 space-y-0.5">
                  {a.integrity_flags.map((f, i) => <li key={i}>{f}</li>)}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function CrbCard({ clientId, onScore }) {
  const { can } = useAuth();
  const [c, setC] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api(`/api/v1/clients/${clientId}/crb-check`)
      .then((d) => setC(d)).catch(() => {}).finally(() => setLoaded(true));
  }, [clientId]);

  const run = async () => {
    setBusy(true); setErr("");
    try {
      const res = await api(`/api/v1/clients/${clientId}/crb-check`, { method: "POST" });
      setC(res);
      if (res.status === "ok" && onScore) onScore();
    } catch (e) { setErr(e.detail || "CRB check failed"); }
    finally { setBusy(false); }
  };

  const notConfigured = c && c.status === "not_configured";
  const ok = c && c.status === "ok";
  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3 border-b border-border font-bold text-base flex items-center justify-between">
        <span>Credit Reference Bureau</span>
        {c && (
          <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${
            ok ? "bg-emerald-100 text-emerald-700"
              : notConfigured ? "bg-gray-200 text-gray-600" : "bg-red-100 text-red-700"}`}>
            {(c.provider || "CRB")}{ok ? " · OK" : notConfigured ? " · NOT CONFIGURED" : " · " + (c.status || "error")}
          </span>
        )}
      </div>

      <div className="p-5 space-y-4">
        {can("clients.edit") && (
          <button className="btn-primary" disabled={busy} onClick={run}>
            {busy ? "Checking…" : c ? "Re-run CRB check" : "Run CRB check"}
          </button>
        )}
        {err && <div className="text-sm text-red-600">{err}</div>}

        {!loaded ? null : !c ? (
          <div className="text-sm text-gray-400">No CRB check run yet.</div>
        ) : notConfigured ? (
          <div className="text-sm bg-gray-50 border border-border rounded-lg p-3 text-gray-500">
            {c.error || "CRB provider not configured."} The integration is credential-gated —
            add the bureau credentials in DCP Setup and it activates automatically.
          </div>
        ) : ok ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <Stat label="Bureau score" value={c.credit_score ?? "—"} tone="good" />
            <Stat label="Active accounts" value={c.active_accounts ?? "—"} />
            <Stat label="Defaults" value={c.defaults_count ?? "—"}
              tone={c.defaults_count > 0 ? "bad" : "good"} />
            <Stat label="Total outstanding" value={fmtKES(c.total_outstanding)} />
            <Stat label="Reference" value={c.reference || "—"} />
            <Stat label="Checked" value={fmtDate(c.created_at)} />
          </div>
        ) : (
          <div className="text-sm text-red-600 bg-red-50 rounded-lg p-3">
            {c.error || "CRB check returned an error."}
          </div>
        )}
      </div>
    </div>
  );
}

export default function CreditAnalysis({ clientId, onScore }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
      <StatementCard clientId={clientId} onScore={onScore} />
      <CrbCard clientId={clientId} onScore={onScore} />
    </div>
  );
}
