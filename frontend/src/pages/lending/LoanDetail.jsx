// Loan detail: client profile, lifecycle transitions, disbursement (maker-checker
// via Daraja B2C), officer reassignment, repayment schedule/history, STK-push.
// Approval itself now lives in the Approvals inbox with threshold + escalation.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDate, fmtKES } from "../../lib/api";
import { Badge, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

// Lifecycle transitions still handled inline (approval/disbursement excluded).
const NEXT = {
  pending: [{ to: "underwriting", label: "Move to Underwriting", cls: "btn-primary" },
            { to: "rejected", label: "Reject", cls: "btn-ghost !text-red-600" }],
  underwriting: [{ to: "rejected", label: "Reject", cls: "btn-ghost !text-red-600" }],
  active: [{ to: "defaulted", label: "Mark Defaulted", cls: "btn-ghost !text-red-600" }],
  overdue: [{ to: "defaulted", label: "Mark Defaulted", cls: "btn-ghost !text-red-600" }],
};

export default function LoanDetail() {
  const { id } = useParams();
  const { canAccess, can } = useAuth();
  const [loan, setLoan] = useState(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [stk, setStk] = useState(null); // amount string when modal open
  const [reassign, setReassign] = useState(false);
  const [staff, setStaff] = useState([]);
  const [busy, setBusy] = useState(false);

  const load = () => api(`/api/v1/lending/loans/${id}`).then(setLoan).catch((e) => setErr(e.detail));
  useEffect(() => { load(); }, [id]);
  useEffect(() => { if (can("loans.reassign")) api("/api/v1/lending/org").then((o) => setStaff(o.staff || [])).catch(() => {}); }, [can]);

  const transition = async (to) => {
    if (!confirm(`Move loan to "${to}"?`)) return;
    setBusy(true); setErr(""); setMsg("");
    try {
      await api(`/api/v1/lending/loans/${id}/transition`, { method: "POST", body: { status: to } });
      setMsg(`Loan moved to ${to}.`);
      load();
    } catch (e) { setErr(e.detail); } finally { setBusy(false); }
  };

  const disburse = async () => {
    if (!confirm("Disburse this approved loan via M-Pesa B2C?")) return;
    setBusy(true); setErr(""); setMsg("");
    try {
      const r = await api("/api/v1/payments/disburse", { method: "POST", body: { loan_id: Number(id) } });
      setMsg(r.status === "pending_approval"
        ? `Amount exceeds the maker-checker threshold — parked for a second approver (pending #${r.pending_id}).`
        : "Loan disbursed via M-Pesa B2C and marked active.");
      load();
    } catch (e) { setErr(e.detail); } finally { setBusy(false); }
  };

  const doReassign = async (staffId, reason) => {
    setBusy(true); setErr(""); setMsg("");
    try {
      await api(`/api/v1/lending/loans/${id}/reassign`, { method: "POST", body: { staff_id: Number(staffId), reason } });
      setMsg("Loan reassigned."); setReassign(false); load();
    } catch (e) { setErr(e.detail); } finally { setBusy(false); }
  };

  const sendStk = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const res = await api("/api/v1/payments/stk-push", { method: "POST", body: { loan_id: Number(id), amount: Number(stk) } });
      setMsg(`STK push sent to ${loan.borrower_phone} — ${res.CustomerMessage || res.ResponseDescription || "prompt delivered"}`);
      setStk(null); load();
    } catch (e2) { setErr(e2.detail); } finally { setBusy(false); }
  };

  const simulateRepayment = async () => {
    // Demo helper: fire the Daraja C2B confirmation webhook with a mock payment.
    const amount = prompt("Simulate M-Pesa C2B repayment amount (KES):", "5000");
    if (!amount) return;
    setBusy(true); setErr("");
    try {
      await api("/api/v1/payments/mpesa-c2b-callback", { method: "POST", body: {
        TransAmount: Number(amount), MSISDN: loan.borrower_phone, BillRefNumber: loan.account_number,
      }});
      setMsg("C2B repayment recorded — balance updated, receipt SMS sent.");
      load();
    } catch (e2) { setErr(e2.detail); } finally { setBusy(false); }
  };

  if (err && !loan) return <div className="card p-6 text-red-600">{err}</div>;
  if (!loan) return <Spinner />;

  // Lifecycle buttons only for approvers / writeoff authority.
  const canLifecycle = can("loans.approve", "loans.writeoff");
  const actions = canLifecycle ? (NEXT[loan.status] || []).map((a) => (
    <button key={a.to} disabled={busy} className={a.cls} onClick={() => transition(a.to)}>{a.label}</button>
  )) : [];

  return (
    <div>
      <PageHeader title={loan.account_number} crumbs={["Lending", <Link key="l" to="/loans" className="hover:underline">Loans</Link>, loan.account_number]}
        actions={<>
          {["pending", "underwriting"].includes(loan.status) && can("loans.approve") && (
            <Link to="/approvals" className="btn-ghost">Open in Approvals →</Link>
          )}
          {actions}
          {loan.status === "approved" && can("disburse.execute") && (
            <button className="btn-primary" disabled={busy} onClick={disburse}>💸 Disburse (B2C)</button>
          )}
          {can("loans.reassign") && (
            <button className="btn-ghost" disabled={busy} onClick={() => setReassign(true)}>↪ Reassign officer</button>
          )}
          {["active", "overdue"].includes(loan.status) && canAccess("payments") && (
            <>
              <button className="btn-primary" disabled={busy} onClick={() => setStk(String(Math.min(loan.outstanding_balance, 5000)))}>📲 STK Push</button>
              <button className="btn-ghost" disabled={busy} onClick={simulateRepayment}>Simulate C2B repayment</button>
            </>
          )}
        </>} />

      {err && <div className="mb-3 text-sm text-red-600 bg-red-50 rounded-lg p-3">{err}</div>}
      {msg && <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 rounded-lg p-3">{msg}</div>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-5 space-y-2.5">
          <div className="flex items-center justify-between">
            <h3 className="font-bold">Loan Summary</h3><Badge value={loan.status} />
          </div>
          {[
            ["Product", loan.product_name], ["Principal", fmtKES(loan.principal)],
            ["Interest rate", `${loan.interest_rate}% flat`], ["Total due", fmtKES(loan.total_due)],
            ["Outstanding", fmtKES(loan.outstanding_balance)], ["Loan cycle", `#${loan.loan_cycle_number}`],
            ["Applied", fmtDate(loan.application_date)], ["Disbursed", fmtDate(loan.disbursement_date)],
            ["Due date", fmtDate(loan.due_date)], ["Officer", loan.staff_name || "—"],
          ].map(([k, v]) => (
            <div key={k} className="flex justify-between text-sm">
              <span className="text-gray-400">{k}</span><span className="font-semibold">{v}</span>
            </div>
          ))}
        </div>

        <div className="card p-5 space-y-2.5">
          <h3 className="font-bold">Client</h3>
          {[
            ["Name", loan.borrower.full_name], ["National ID", loan.borrower.national_id],
            ["Phone", loan.borrower.phone], ["Sector", loan.borrower.business_sector || "—"],
            ["KYC", <Badge key="k" value={loan.borrower.kyc_status} />],
            ["Credit score", loan.borrower.credit_score ?? "—"],
            ["Baseline sales", fmtKES(loan.borrower.baseline_monthly_sales)],
          ].map(([k, v]) => (
            <div key={k} className="flex justify-between text-sm">
              <span className="text-gray-400">{k}</span><span className="font-semibold">{v}</span>
            </div>
          ))}
        </div>

        <div className="card p-5">
          <h3 className="font-bold mb-2">Repayment Schedule</h3>
          {loan.schedule.length === 0 ? <p className="text-sm text-gray-400">Available after disbursement.</p> : (
            <div className="max-h-64 overflow-y-auto">
              <table className="w-full text-sm">
                <thead><tr><th className="th">#</th><th className="th">Due</th><th className="th">Amount</th></tr></thead>
                <tbody>{loan.schedule.map((s) => (
                  <tr key={s.n}><td className="td">{s.n}</td><td className="td">{fmtDate(s.due_date)}</td><td className="td">{fmtKES(s.amount)}</td></tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="card mt-4 overflow-hidden">
        <h3 className="font-bold px-5 py-3.5 border-b border-border">Repayment History ({loan.repayments.length})</h3>
        {loan.repayments.length === 0 ? <p className="text-sm text-gray-400 p-5">No repayments recorded yet.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Date</th><th className="th">Amount</th><th className="th">Principal</th>
                <th className="th">Interest</th><th className="th">Method</th><th className="th">M-Pesa Ref</th>
              </tr></thead>
              <tbody>{loan.repayments.map((r) => (
                <tr key={r.id} className="hover:bg-canvas/60">
                  <td className="td">{fmtDate(r.payment_date)}</td>
                  <td className="td font-semibold">{fmtKES(r.amount)}</td>
                  <td className="td">{fmtKES(r.principal_component)}</td>
                  <td className="td">{fmtKES(r.interest_component)}</td>
                  <td className="td">{r.method}</td>
                  <td className="td font-mono text-xs">{r.mpesa_ref || "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </div>

      {stk !== null && (
        <Modal title="Send STK Push (Collections)" onClose={() => setStk(null)}>
          <form onSubmit={sendStk} className="space-y-3">
            <p className="text-sm text-gray-500">
              Sends a mock Daraja STK-push payment prompt to <b>{loan.borrower_phone}</b>.
            </p>
            <div><label className="label">Amount (KES)</label>
              <input type="number" className="input" required value={stk} onChange={(e) => setStk(e.target.value)} /></div>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setStk(null)}>Cancel</button>
              <button className="btn-primary" disabled={busy}>Send prompt</button>
            </div>
          </form>
        </Modal>
      )}

      {reassign && (
        <ReassignModal staff={staff} current={loan.staff_id} busy={busy}
          onCancel={() => setReassign(false)} onSubmit={doReassign} />
      )}
    </div>
  );
}

function ReassignModal({ staff, current, busy, onCancel, onSubmit }) {
  const [staffId, setStaffId] = useState("");
  const [reason, setReason] = useState("");
  return (
    <Modal title="Reassign loan to another officer" onClose={onCancel}>
      <div className="space-y-3">
        <div><label className="label">New officer</label>
          <select className="input" value={staffId} onChange={(e) => setStaffId(e.target.value)}>
            <option value="">Select officer…</option>
            {staff.filter((s) => s.id !== current).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select></div>
        <div><label className="label">Reason</label>
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. portfolio rebalancing" /></div>
        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button className="btn-primary" disabled={!staffId || busy} onClick={() => onSubmit(staffId, reason)}>Reassign</button>
        </div>
      </div>
    </Modal>
  );
}
