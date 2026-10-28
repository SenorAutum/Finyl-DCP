// Suspense account: paybill receipts that could not be applied automatically
// (unknown account number, customer with no / several open loans, overpayment
// excess, closed loan). Officers with reconcile.execute can Allocate a receipt
// to a loan or Refund it, and Record a payment received outside the paybill
// flow (till, bank, cash). Above the DCP's manual-payment threshold every one
// of these waits for a second person in Approvals → Pending money actions.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Badge, Empty, Modal, PageHeader, Pagination, Spinner } from "../../components/ui";

const kes2 = (n) =>
  n == null ? "—" : `KES ${Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtDT = (d) => (d ? new Date(d).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }) : "—");

const STATUSES = ["", "open", "pending_approval", "refund_processing", "reversal_pending", "allocated", "refunded", "reversed"];
const CHANNELS = [
  ["mpesa_paybill", "M-Pesa paybill"],
  ["mpesa_till", "M-Pesa till"],
  ["bank", "Bank deposit / transfer"],
  ["cash", "Cash at branch"],
  ["other", "Other"],
];
const REASONS = {
  unmatched: "account not recognised",
  no_open_loan: "customer has no open loan",
  ambiguous_customer: "customer has several open loans",
  overpayment: "overpayment",
  closed_loan: "loan not open",
  interest_refund_due: "interest refund due (early repayment)",
};

const pendingMsg = (r, done) =>
  r.status === "pending_approval" ? "Sent for approval — a second person must approve it under Approvals."
    : r.status === "already_resolved" ? "Entry was already resolved."
    : done;

// Local date-time string for <input type="datetime-local"> defaults.
const nowLocal = () => {
  const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
};

function RecordPayment({ onClose, onDone }) {
  const [f, setF] = useState({ channel: "mpesa_paybill", amount: "", reference: "", paid_at: nowLocal(),
                               target: "customer", customer_number: "", loan_id: "",
                               payer_name: "", payer_phone: "", note: "" });
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }));

  async function submit() {
    setBusy(true); setErr("");
    try {
      const body = {
        channel: f.channel, amount: Number(f.amount), reference: f.reference || null,
        paid_at: new Date(f.paid_at).toISOString(), note: f.note,
        payer_name: f.payer_name || null, payer_phone: f.payer_phone || null,
        ...(f.target === "customer" ? { customer_number: f.customer_number } : { loan_id: Number(f.loan_id) }),
      };
      const r = await api("/api/v1/payments/manual", { method: "POST", body });
      onDone(pendingMsg(r, `Payment applied to loan #${r.loan_id}.`));
    } catch (e) { setErr(e.detail || "Could not record the payment."); }
    finally { setBusy(false); }
  }

  return (
    <Modal title="Record a payment" onClose={onClose}>
      <div className="space-y-3">
        {err && <div className="text-sm bg-red-50 text-red-700 rounded-lg p-2">{err}</div>}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-semibold text-gray-600">Received via</label>
            <select className="input" value={f.channel} onChange={(e) => set("channel", e.target.value)}>
              {CHANNELS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs font-semibold text-gray-600">Amount (KES)</label>
            <input type="number" min="1" className="input" value={f.amount} onChange={(e) => set("amount", e.target.value)} />
          </div>
          <div>
            <label className="text-xs font-semibold text-gray-600">
              {f.channel === "bank" ? "Bank reference" : "M-Pesa code"}{f.channel === "cash" ? " (optional)" : ""}
            </label>
            <input className="input font-mono uppercase" value={f.reference} onChange={(e) => set("reference", e.target.value)} />
          </div>
          <div>
            <label className="text-xs font-semibold text-gray-600">When the customer paid</label>
            <input type="datetime-local" className="input" value={f.paid_at} onChange={(e) => set("paid_at", e.target.value)} />
          </div>
        </div>
        <div>
          <div className="flex gap-4 text-sm mb-1">
            <label className="flex items-center gap-1"><input type="radio" checked={f.target === "customer"} onChange={() => set("target", "customer")} /> Customer number</label>
            <label className="flex items-center gap-1"><input type="radio" checked={f.target === "loan"} onChange={() => set("target", "loan")} /> Loan ID</label>
          </div>
          {f.target === "customer"
            ? <input className="input font-mono" inputMode="numeric" placeholder="7-digit customer number" value={f.customer_number} onChange={(e) => set("customer_number", e.target.value)} />
            : <input type="number" className="input" placeholder="Loan ID" value={f.loan_id} onChange={(e) => set("loan_id", e.target.value)} />}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-semibold text-gray-600">Payer name (optional)</label>
            <input className="input" value={f.payer_name} onChange={(e) => set("payer_name", e.target.value)} />
          </div>
          <div>
            <label className="text-xs font-semibold text-gray-600">Payer phone (optional)</label>
            <input className="input" value={f.payer_phone} onChange={(e) => set("payer_phone", e.target.value)} />
          </div>
        </div>
        <div>
          <label className="text-xs font-semibold text-gray-600">Evidence (required)</label>
          <textarea className="input" rows={2} placeholder="e.g. Equity statement 24 Oct line 12; customer forwarded M-Pesa SMS"
            value={f.note} onChange={(e) => set("note", e.target.value)} />
        </div>
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !f.amount || !f.note.trim()} onClick={submit}>
            {busy ? "Saving…" : "Record payment"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export default function Suspense() {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("open");
  const [page, setPage] = useState(1);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(null);
  const [recording, setRecording] = useState(false);

  const load = () => {
    setData(null);
    api(`/api/v1/payments/suspense?status=${status}&page=${page}&page_size=50`)
      .then(setData).catch((e) => { setMsg(e.detail || "Failed to load suspense entries."); setData({ items: [], total: 0 }); });
  };
  useEffect(load, [status, page]);

  const allocate = async (row) => {
    const hint = row.suggested_loan_id ? ` (suggested: #${row.suggested_loan_id})` : "";
    const loanId = prompt(`Allocate ${kes2(row.amount)} (${row.mpesa_ref || row.phone || "receipt"}) to which loan ID?${hint}`,
                          row.suggested_loan_id ? String(row.suggested_loan_id) : "");
    if (!loanId) return;
    setBusy(row.id); setMsg("");
    try {
      const r = await api(`/api/v1/payments/suspense/${row.id}/allocate`, { method: "POST", body: { loan_id: Number(loanId) } });
      setMsg(pendingMsg(r, `Allocated to loan #${loanId}.`));
      load();
    } catch (e) { setMsg(e.detail || "Allocation failed."); }
    finally { setBusy(null); }
  };

  const refund = async (row) => {
    if (!confirm(`Request a refund of ${kes2(row.amount)} to ${row.phone || "the payer"}? A second person must approve it before money is sent.`)) return;
    const note = prompt("Reason for the refund (shown to the approver):") || null;
    let refund_phone = null;
    if (!/^(\+?254|0)?[71]\d{8}$/.test((row.phone || "").replace(/\s/g, ""))) {
      refund_phone = prompt("The paying number is not available. Enter the number to refund (confirm it with the payer first):");
      if (!refund_phone) return;
    }
    setBusy(row.id); setMsg("");
    try {
      const r = await api(`/api/v1/payments/suspense/${row.id}/refund`, { method: "POST", body: { note, refund_phone } });
      setMsg(pendingMsg(r, "Refund sent."));
      load();
    } catch (e) { setMsg(e.detail || "Refund failed."); }
    finally { setBusy(null); }
  };

  const reverse = async (row) => {
    if (!confirm(`Ask Safaricom to reverse ${kes2(row.amount)} (${row.mpesa_ref}) back to the payer? A second person must approve it first.`)) return;
    const note = prompt("Why is this payment being reversed? (shown to the approver)");
    if (!note) return;
    setBusy(row.id); setMsg("");
    try {
      const r = await api(`/api/v1/payments/suspense/${row.id}/reverse`, { method: "POST", body: { note } });
      setMsg(pendingMsg(r, "Reversal requested."));
      load();
    } catch (e) { setMsg(e.detail || "Reversal request failed."); }
    finally { setBusy(null); }
  };

  return (
    <div>
      <PageHeader title="Suspense Account" crumbs={["Transactions", "Payments", "Suspense"]}
        actions={<button className="btn-primary" onClick={() => setRecording(true)}>+ Record payment</button>} />

      {msg && <div className="mb-3 text-sm text-teal bg-teal-50 rounded-lg p-3">{msg}</div>}
      {recording && (
        <RecordPayment onClose={() => setRecording(false)}
          onDone={(m) => { setRecording(false); setMsg(m); load(); }} />
      )}

      <div className="card overflow-hidden">
        <div className="p-3 border-b border-border flex flex-wrap items-center gap-3">
          <select className="input !w-auto" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            {STATUSES.map((s) => <option key={s} value={s}>{s ? s.replace(/_/g, " ") : "All statuses"}</option>)}
          </select>
          {data?.open_balance != null && (
            <span className="text-sm text-gray-500">Open balance: <span className="font-bold text-charcoal">{kes2(data.open_balance)}</span></span>
          )}
        </div>
        {!data ? <Spinner /> : data.items.length === 0 ? <Empty text="No suspense entries" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Paid</th><th className="th">M-Pesa Ref</th>
                <th className="th">Payer</th><th className="th">Account entered</th>
                <th className="th">Amount</th><th className="th">Reason</th>
                <th className="th">Status</th><th className="th text-right">Action</th>
              </tr></thead>
              <tbody>
                {data.items.map((s) => (
                  <tr key={s.id} className="border-t border-border hover:bg-canvas/60">
                    <td className="td whitespace-nowrap">{fmtDT(s.paid_at || s.created_at)}</td>
                    <td className="td font-mono text-xs">{s.mpesa_ref || "—"}</td>
                    <td className="td">
                      <div>{s.payer_name || "—"}</div>
                      <div className="text-[11px] text-gray-400">{s.phone || ""}</div>
                    </td>
                    <td className="td font-mono text-xs">{s.bill_ref || "—"}</td>
                    <td className="td font-semibold">{kes2(s.amount)}</td>
                    <td className="td">
                      {REASONS[s.reason] || (s.reason || "—").replace(/_/g, " ")}
                      {s.suggested_loan_id && <div className="text-[11px] text-amber-700">suggested loan #{s.suggested_loan_id}</div>}
                    </td>
                    <td className="td">
                      <Badge value={s.status} />
                      {s.matched_loan_id && <span className="ml-1 text-[11px] text-gray-400">#{s.matched_loan_id}</span>}
                    </td>
                    <td className="td text-right space-x-1 whitespace-nowrap">
                      {s.status === "open" ? (
                        <>
                          <button className="btn-primary !py-1 !px-2 text-xs" disabled={busy === s.id} onClick={() => allocate(s)}>Allocate</button>
                          {s.can_reverse && (
                            <button className="btn-ghost !py-1 !px-2 text-xs text-red-600" disabled={busy === s.id} onClick={() => reverse(s)}
                              title="Safaricom returns the whole payment to the payer — no phone number needed">Reverse</button>
                          )}
                          <button className="btn-ghost !py-1 !px-2 text-xs text-red-600" disabled={busy === s.id} onClick={() => refund(s)}>Refund</button>
                        </>
                      ) : s.status === "pending_approval" ? (
                        <span className="text-xs text-amber-700 italic">awaiting approval</span>
                      ) : ["reversal_pending", "refund_processing"].includes(s.status) ? (
                        <span className="text-xs text-amber-700 italic">waiting for Safaricom</span>
                      ) : (
                        <span className="text-xs text-gray-400 italic">resolved</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && <Pagination page={page} total={data.total} onPage={setPage} />}
      </div>
    </div>
  );
}
