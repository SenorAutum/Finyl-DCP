// Bank statements — per-client statement affordability analysis. Search & select
// a client to view ingested statements: cashflow metrics, affordability score,
// detected lenders and integrity/tampering flags.
import { useEffect, useState } from "react";
import { api, fmtDate, fmtKES } from "../../lib/api";
import { Empty, PageHeader, Spinner } from "../../components/ui";

function Metric({ label, value }) {
  return (
    <div className="rounded-xl bg-surface2 border border-border px-3 py-2">
      <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
      <div className="text-sm font-extrabold tabnums mt-0.5">{value}</div>
    </div>
  );
}

export default function BankStatements() {
  const [clientSearch, setClientSearch] = useState("");
  const [clients, setClients] = useState([]);
  const [clientId, setClientId] = useState("");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    const t = setTimeout(() => {
      api(`/api/v1/clients?search=${encodeURIComponent(clientSearch)}&page=1`)
        .then((r) => setClients(r.items || [])).catch(() => setClients([]));
    }, 250);
    return () => clearTimeout(t);
  }, [clientSearch]);

  useEffect(() => {
    if (!clientId) { setData(null); return; }
    setData(null); setErr("");
    api(`/api/v1/clients/${clientId}/bank-statements`).then(setData).catch((e) => setErr(e.detail));
  }, [clientId]);

  return (
    <div>
      <PageHeader title="Bank Statements" crumbs={["Field & Collections", "Bank Statements"]} />
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="flex-1 min-w-[220px]">
          <label className="label">Find a client</label>
          <input className="input" placeholder="Search name, National ID or phone…"
            value={clientSearch} onChange={(e) => setClientSearch(e.target.value)} />
        </div>
        <div className="flex-1 min-w-[220px]">
          <label className="label">Client</label>
          <select className="input" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">Select a client…</option>
            {clients.map((c) => <option key={c.id} value={c.id}>{c.full_name}{c.phone ? ` · ${c.phone}` : ""}</option>)}
          </select>
        </div>
      </div>

      {!clientId ? (
        <div className="card"><Empty text="Select a client to view analysed bank statements." /></div>
      ) : !data ? <Spinner /> : (data.items || []).length === 0 ? (
        <div className="card"><Empty text="No bank statements ingested for this client." /></div>
      ) : (
        <div className="space-y-4">
          {data.items.map((s) => (
            <div key={s.id} className="card p-5">
              <div className="flex items-start justify-between flex-wrap gap-3 mb-3">
                <div>
                  <h3 className="font-bold text-base">{s.bank_name || "Bank statement"}</h3>
                  <p className="text-xs text-gray-400">{fmtDate(s.period_start)} – {fmtDate(s.period_end)} · {s.months_covered || 0} months · {s.transactions_count || 0} transactions</p>
                </div>
                {s.tampering_suspected
                  ? <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-red-100 text-red-700">⚠ Tampering suspected</span>
                  : <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-100 text-emerald-700">Integrity OK</span>}
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                <Metric label="Avg credit / mo" value={fmtKES(s.avg_monthly_credit)} />
                <Metric label="Avg debit / mo" value={fmtKES(s.avg_monthly_debit)} />
                <Metric label="Net cashflow / mo" value={fmtKES(s.net_monthly_cashflow)} />
                <Metric label="Affordability" value={s.affordability_score != null ? s.affordability_score : "—"} />
                <Metric label="Comfortable installment" value={fmtKES(s.comfortable_installment)} />
                <Metric label="Detected lenders" value={(s.detected_lenders || []).length} />
              </div>
              {(s.integrity_flags || []).length > 0 && (
                <div className="mt-3 text-xs text-red-700">
                  <span className="font-semibold">Integrity flags: </span>{(s.integrity_flags || []).join(", ")}
                </div>
              )}
              {(s.detected_lenders || []).length > 0 && (
                <div className="mt-2 text-xs text-gray-500">
                  <span className="font-semibold">Detected lenders: </span>{(s.detected_lenders || []).join(", ")}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
