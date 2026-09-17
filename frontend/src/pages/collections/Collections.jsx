// Collections workbench — Promises-to-Pay lifecycle, per-client bank-statement
// affordability analysis and M-Pesa Ratiba (standing-order) status. Officer
// efficiency scorecards live on a dedicated page (/collections/efficiency).
import { useEffect, useState } from "react";
import { api, fmtDate, fmtKES } from "../../lib/api";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import PtpStatusBadge from "../../components/PtpStatusBadge";
import { useAuth } from "../../hooks/useAuth";

const PTP_STATUSES = ["pending", "honored", "broken", "partial", "rescheduled", "cancelled"];
const emptyPtp = { loan_id: "", ptp_date: "", amount: "", contact_method: "call", notes: "" };

function Tabs({ tab, setTab, tabs }) {
  return (
    <div className="flex gap-1 border-b border-border mb-5">
      {tabs.map(([key, label]) => (
        <button key={key} onClick={() => setTab(key)}
          className={`px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition-colors ${
            tab === key ? "border-accent text-accent" : "border-transparent text-gray-500 hover:text-charcoal"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ PTP tab
function PtpTab({ staffMap }) {
  const { can } = useAuth();
  const [status, setStatus] = useState("");
  const [data, setData] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(emptyPtp);
  const [editing, setEditing] = useState(null); // ptp row being updated
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const load = () => {
    setData(null);
    api(`/api/v1/collections/ptp?status=${status}`).then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(load, [status]);

  const set = (k) => (e) => setForm((p) => ({ ...p, [k]: e.target.value }));

  const create = async (e) => {
    e.preventDefault();
    setSaving(true); setErr("");
    try {
      await api("/api/v1/collections/ptp", { method: "POST", body: {
        loan_id: Number(form.loan_id), ptp_date: form.ptp_date,
        amount: Number(form.amount), contact_method: form.contact_method, notes: form.notes,
      }});
      setCreating(false); setForm(emptyPtp); load();
    } catch (ex) { setErr(ex.detail || "Could not log promise"); }
    finally { setSaving(false); }
  };

  const saveEdit = async (e) => {
    e.preventDefault();
    setSaving(true); setErr("");
    try {
      await api(`/api/v1/collections/ptp/${editing.id}`, { method: "PATCH", body: {
        status: editing.status,
        honored_amount: editing.honored_amount === "" || editing.honored_amount == null ? undefined : Number(editing.honored_amount),
        ptp_date: editing.ptp_date || undefined,
        notes: editing.notes || undefined,
      }});
      setEditing(null); load();
    } catch (ex) { setErr(ex.detail || "Could not update promise"); }
    finally { setSaving(false); }
  };

  const canManage = can("collections.ptp_manage");

  return (
    <div>
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      <div className="card overflow-hidden">
        <div className="p-3 border-b border-border flex flex-wrap gap-2 items-center">
          <select className="input max-w-[180px]" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {PTP_STATUSES.map((s) => <option key={s} value={s} className="capitalize">{s}</option>)}
          </select>
          <div className="flex-1" />
          {canManage && <button className="btn-primary" onClick={() => { setForm(emptyPtp); setCreating(true); }}>+ Log Promise</button>}
        </div>

        {!data ? <Spinner /> : data.items.length === 0 ? <Empty text="No promises-to-pay found." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Loan</th><th className="th">Officer</th><th className="th">Promise Date</th>
                <th className="th text-right">Amount</th><th className="th text-right">Honored</th>
                <th className="th">Method</th><th className="th">Status</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {data.items.map((p) => (
                  <tr key={p.id} className="border-t border-border">
                    <td className="td font-semibold">#{p.loan_id}</td>
                    <td className="td">{staffMap[p.logged_by] || `User ${p.logged_by}`}</td>
                    <td className="td">{fmtDate(p.ptp_date)}</td>
                    <td className="td text-right tabnums">{fmtKES(p.amount)}</td>
                    <td className="td text-right tabnums">{p.honored_amount != null ? fmtKES(p.honored_amount) : "—"}</td>
                    <td className="td capitalize">{p.contact_method || "—"}</td>
                    <td className="td"><PtpStatusBadge status={p.status} /></td>
                    <td className="td text-right">
                      {canManage && (
                        <button className="btn-ghost !py-1 !px-2.5 text-xs"
                          onClick={() => setEditing({ ...p, honored_amount: p.honored_amount ?? "", ptp_date: "", notes: "" })}>
                          Update
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating && (
        <Modal title="Log Promise-to-Pay" onClose={() => setCreating(false)}>
          <form onSubmit={create} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <div className="grid grid-cols-2 gap-3">
              <div><label className="label">Loan ID</label>
                <input className="input" type="number" value={form.loan_id} onChange={set("loan_id")} required /></div>
              <div><label className="label">Promise date</label>
                <input className="input" type="date" value={form.ptp_date} onChange={set("ptp_date")} required /></div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="label">Amount (KES)</label>
                <input className="input" type="number" value={form.amount} onChange={set("amount")} required /></div>
              <div><label className="label">Contact method</label>
                <select className="input" value={form.contact_method} onChange={set("contact_method")}>
                  {["call", "sms", "visit", "whatsapp", "email"].map((m) => <option key={m} value={m} className="capitalize">{m}</option>)}
                </select></div>
            </div>
            <div><label className="label">Notes</label>
              <textarea className="input" rows={2} value={form.notes} onChange={set("notes")} /></div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn-primary" disabled={saving}>{saving ? "Saving…" : "Log promise"}</button>
            </div>
          </form>
        </Modal>
      )}

      {editing && (
        <Modal title={`Update Promise · Loan #${editing.loan_id}`} onClose={() => setEditing(null)}>
          <form onSubmit={saveEdit} className="space-y-4">
            {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
            <div><label className="label">Status</label>
              <select className="input" value={editing.status} onChange={(e) => setEditing((p) => ({ ...p, status: e.target.value }))}>
                {PTP_STATUSES.map((s) => <option key={s} value={s} className="capitalize">{s}</option>)}
              </select></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="label">Honored amount (KES)</label>
                <input className="input" type="number" value={editing.honored_amount}
                  onChange={(e) => setEditing((p) => ({ ...p, honored_amount: e.target.value }))} /></div>
              <div><label className="label">Reschedule to</label>
                <input className="input" type="date" value={editing.ptp_date}
                  onChange={(e) => setEditing((p) => ({ ...p, ptp_date: e.target.value }))} /></div>
            </div>
            <div><label className="label">Notes</label>
              <textarea className="input" rows={2} value={editing.notes}
                onChange={(e) => setEditing((p) => ({ ...p, notes: e.target.value }))} /></div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn-primary" disabled={saving}>{saving ? "Saving…" : "Save update"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// -------------------------------------------------------- Bank statements tab
function BankStatementTab() {
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
    setData(null);
    api(`/api/v1/clients/${clientId}/bank-statements`).then(setData).catch((e) => setErr(e.detail));
  }, [clientId]);

  return (
    <div>
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
      ) : !data ? <Spinner /> : data.items.length === 0 ? (
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

function Metric({ label, value }) {
  return (
    <div className="rounded-xl bg-surface2 border border-border px-3 py-2">
      <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
      <div className="text-sm font-extrabold tabnums mt-0.5">{value}</div>
    </div>
  );
}

// -------------------------------------------------------------- Ratiba tab
function RatibaTab() {
  const { can } = useAuth();
  const [loanId, setLoanId] = useState("");
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const check = async (e) => {
    e?.preventDefault();
    if (!loanId) return;
    setBusy(true); setErr(""); setStatus(null);
    try { setStatus(await api(`/api/v1/loans/${loanId}/ratiba-status`)); }
    catch (ex) { setErr(ex.detail || "Could not load status"); }
    finally { setBusy(false); }
  };

  const initiate = async () => {
    setBusy(true); setErr("");
    try { setStatus(await api("/api/v1/collections/ratiba/initiate", { method: "POST", body: { loan_id: Number(loanId) } })); }
    catch (ex) { setErr(ex.detail || "Could not initiate Ratiba"); }
    finally { setBusy(false); }
  };

  return (
    <div>
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      <form onSubmit={check} className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="min-w-[200px]">
          <label className="label">Loan ID</label>
          <input className="input" type="number" value={loanId} onChange={(e) => setLoanId(e.target.value)} placeholder="e.g. 1024" />
        </div>
        <button className="btn-primary" disabled={busy || !loanId}>{busy ? "Loading…" : "Check status"}</button>
      </form>

      {status && (
        <div className="card p-5">
          <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
            <div>
              <h3 className="font-bold text-base">M-Pesa Ratiba · Loan #{status.loan_id}</h3>
              <p className="text-xs text-gray-400">Standing-order deduction mandate</p>
            </div>
            <Badge value={status.status === "none" ? "pending" : status.status} />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Metric label="Consent given" value={status.consent_given ? "Yes" : "No"} />
            <Metric label="Ratiba ref" value={status.ratiba_ref || "—"} />
            <Metric label="Deduction amount" value={status.deduction_amount != null ? fmtKES(status.deduction_amount) : "—"} />
            <Metric label="Deduction day" value={status.deduction_day || "—"} />
          </div>
          {can("collections.ptp_manage") && (
            <div className="mt-4 flex justify-end">
              <button className="btn-primary" onClick={initiate} disabled={busy || !status.consent_given}>
                {busy ? "Working…" : "Initiate Ratiba"}
              </button>
            </div>
          )}
          {!status.consent_given && (
            <p className="mt-2 text-xs text-amber-600 text-right">Client consent must be captured before initiating.</p>
          )}
        </div>
      )}
    </div>
  );
}

export default function Collections() {
  const [tab, setTab] = useState("ptp");
  const [staffMap, setStaffMap] = useState({});

  useEffect(() => {
    api("/api/v1/lending/org").then((org) => {
      const m = {};
      (org.staff || []).forEach((s) => { m[s.id] = s.name; });
      setStaffMap(m);
    }).catch(() => {});
  }, []);

  return (
    <div>
      <PageHeader title="Collections" crumbs={["Field & Collections", "Collections"]} />
      <Tabs tab={tab} setTab={setTab} tabs={[["ptp", "Promises to Pay"], ["bank", "Bank Statements"], ["ratiba", "M-Pesa Ratiba"]]} />
      {tab === "ptp" && <PtpTab staffMap={staffMap} />}
      {tab === "bank" && <BankStatementTab />}
      {tab === "ratiba" && <RatibaTab />}
    </div>
  );
}
