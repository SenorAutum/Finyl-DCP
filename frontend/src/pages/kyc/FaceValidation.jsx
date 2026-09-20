// Face validation — client-lookup tool. Search & select a client, view the
// latest face-validation status (result, match score, liveness, provider) and
// trigger a fresh validation run against the configured biometric provider.
import { useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { Empty, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

const RESULT_STYLES = {
  pass: "bg-emerald-100 text-emerald-700",
  fail: "bg-red-100 text-red-700",
  not_run: "bg-gray-200 text-gray-600",
};

function Metric({ label, value }) {
  return (
    <div className="rounded-xl bg-surface2 border border-border px-3 py-2">
      <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
      <div className="text-sm font-extrabold tabnums mt-0.5">{value}</div>
    </div>
  );
}

export default function FaceValidation() {
  const { can } = useAuth();
  const [clientSearch, setClientSearch] = useState("");
  const [clients, setClients] = useState([]);
  const [clientId, setClientId] = useState("");
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    const t = setTimeout(() => {
      api(`/api/v1/clients?search=${encodeURIComponent(clientSearch)}&page=1`)
        .then((r) => setClients(r.items || [])).catch(() => setClients([]));
    }, 250);
    return () => clearTimeout(t);
  }, [clientSearch]);

  const load = () => {
    if (!clientId) { setStatus(null); return; }
    setStatus(null); setErr("");
    api(`/api/v1/clients/${clientId}/face-validation-status`).then(setStatus).catch((e) => setErr(e.detail));
  };
  useEffect(load, [clientId]);

  const runValidation = async () => {
    if (!clientId) return;
    setBusy(true); setErr("");
    try {
      const res = await api(`/api/v1/clients/${clientId}/face-validate`, { method: "POST", body: {} });
      setStatus((prev) => ({ ...(prev || {}), ...res }));
    } catch (ex) { setErr(ex.detail || "Could not run face validation"); }
    finally { setBusy(false); }
  };

  const canRun = can("clients.edit", "clients.create");

  return (
    <div>
      <PageHeader title="Face Validation" crumbs={["Registry", "Face Validation"]} />
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
        <div className="card"><Empty text="Select a client to view their face-validation status." /></div>
      ) : !status ? <Spinner /> : (
        <div className="card p-5">
          <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
            <div>
              <h3 className="font-bold text-base">Face Validation · Client #{status.client_id}</h3>
              <p className="text-xs text-gray-400">Biometric identity match {status.mandatory ? "(mandatory for this tenant)" : "(optional for this tenant)"}</p>
            </div>
            <span className={`inline-flex px-2.5 py-1 rounded-full text-xs font-bold uppercase ${RESULT_STYLES[status.result] || "bg-gray-200 text-gray-600"}`}>
              {(status.result || "not_run").replace(/_/g, " ")}
            </span>
          </div>

          {status.result === "not_run" ? (
            <p className="text-sm text-gray-500">No face validation has been run for this client yet.</p>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Metric label="Match score" value={status.match_score != null ? `${Math.round(status.match_score * 100)}%` : "—"} />
              <Metric label="Liveness" value={status.liveness_pass == null ? "—" : status.liveness_pass ? "Pass" : "Fail"} />
              <Metric label="Provider" value={status.provider || "—"} />
              <Metric label="Validated" value={status.validated_at ? fmtDate(status.validated_at) : "—"} />
            </div>
          )}

          {canRun && (
            <div className="mt-5 flex justify-end">
              <button className="btn-primary" onClick={runValidation} disabled={busy}>
                {busy ? "Running…" : status.result === "not_run" ? "Run face validation" : "Re-run validation"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
