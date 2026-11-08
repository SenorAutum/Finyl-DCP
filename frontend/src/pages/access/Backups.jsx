// Data Integrity — record reference counts and run consistency checks.
// Finyl platform only. WP7: the old "Run backup" button never made a backup
// (it only counted rows); it is now labelled for what it does, and the page
// says where real backups come from.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { PageHeader, Spinner, KpiCard } from "../../components/ui";

export default function Backups() {
  const [integrity, setIntegrity] = useState(null);
  const [backup, setBackup] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const loadIntegrity = () => api("/api/v1/access/integrity").then(setIntegrity).catch((e) => setMsg(e.detail));
  useEffect(() => { loadIntegrity(); }, []);

  const runBackup = async () => {
    setBusy(true); setMsg("");
    try { setBackup(await api("/api/v1/access/backup", { method: "POST" })); setMsg("Counts recorded in the audit trail."); }
    catch (e) { setMsg(e.detail); } finally { setBusy(false); }
  };

  return (
    <div>
      <PageHeader title="Backups & Data Integrity" crumbs={["Administration", "Backups"]}
        actions={<button className="btn-primary" disabled={busy} onClick={runBackup}>{busy ? "Recording…" : "Record counts"}</button>} />
      <div className="mb-4 text-sm bg-amber-50 border border-amber-200 text-amber-900 rounded-lg p-3">
        This page does <b>not</b> make backups. Backups are taken by the database service
        (on AWS: RDS automated backups and snapshots) or by <code>deploy/backup_db.sh</code>.
        “Record counts” saves how many clients, loans and repayments exist now, so a restored
        copy can be checked against it.
      </div>
      {msg && <div className="mb-3 text-sm px-3 py-2 rounded-lg bg-teal-50 text-teal-700 border border-teal-200">{msg}</div>}

      {backup && (
        <div className="card p-4 mb-4">
          <div className="font-semibold mb-1">Counts recorded — {backup.reference}</div>
          <div className="text-xs text-gray-500 mb-2">{backup.note}</div>
          <div className="grid grid-cols-3 gap-3">
            {Object.entries(backup.snapshot_counts || {}).map(([k, v]) => <KpiCard key={k} label={k} value={v} />)}
          </div>
        </div>
      )}

      <h3 className="font-bold mb-2">Data-integrity checks</h3>
      {!integrity ? <Spinner /> : (
        <>
          <div className={`mb-3 inline-flex px-3 py-1 rounded-full text-sm font-semibold ${integrity.healthy ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
            {integrity.healthy ? "✓ Healthy" : "⚠ Issues detected"}
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {Object.entries(integrity.checks || {}).map(([k, v]) => (
              <KpiCard key={k} label={k.replace(/_/g, " ")} value={v}
                tone={v > 0 && k !== "total_loans_checked" ? "warn" : "default"} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
