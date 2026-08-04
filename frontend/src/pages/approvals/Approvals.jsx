// Approvals inbox: loan approvals (threshold + escalation), client-profile
// approvals, and maker-checker sign-off for disbursements / refunds.
import { useEffect, useState } from "react";
import { api, fmtKES, fmtDate } from "../../lib/api";
import { useAuth } from "../../hooks/useAuth";
import { PageHeader, Badge, Empty, Spinner } from "../../components/ui";

export default function Approvals() {
  const { can } = useAuth();
  const tabs = [];
  if (can("loans.approve")) tabs.push(["loans", "Loan Approvals"]);
  if (can("clients.approve")) tabs.push(["clients", "Client Profiles"]);
  if (can("disburse.approve") || can("refund.approve")) tabs.push(["actions", "Maker-Checker"]);
  const [tab, setTab] = useState(tabs[0]?.[0] || "loans");

  return (
    <div>
      <PageHeader title="Approvals" crumbs={["Approvals"]} />
      <div className="flex gap-2 mb-4 flex-wrap">
        {tabs.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-3 py-1.5 rounded-lg text-sm font-semibold ${tab === k ? "bg-accent text-white" : "bg-surface border border-border text-gray-600"}`}>
            {label}
          </button>
        ))}
      </div>
      {tab === "loans" && <LoanApprovals />}
      {tab === "clients" && <ClientApprovals />}
      {tab === "actions" && <PendingActions />}
    </div>
  );
}

function useList(path, deps = []) {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  const load = () => { setRows(null); api(path).then(setRows).catch((e) => { setErr(e.detail); setRows([]); }); };
  useEffect(load, deps);            // eslint-disable-line
  return [rows, load, err];
}

function LoanApprovals() {
  const [rows, reload] = useList("/api/v1/approvals/loans");
  const [msg, setMsg] = useState("");
  const act = async (id, action) => {
    setMsg("");
    let note = null;
    if (action === "reject") { note = prompt("Reason for rejection?") || ""; }
    try {
      const r = await api(`/api/v1/approvals/loans/${id}`, { method: "POST", body: { action, note } });
      setMsg(r.message || "Done"); reload();
    } catch (e) { setMsg(e.detail); }
  };
  if (!rows) return <Spinner />;
  return (
    <div className="card overflow-hidden">
      {msg && <div className="px-4 py-2 text-sm bg-teal-50 text-teal-700 border-b border-border">{msg}</div>}
      {rows.length === 0 ? <Empty text="No loans awaiting your approval" /> : (
        <table className="w-full text-sm">
          <thead><tr className="text-left border-b border-border bg-canvas">
            <th className="th">Account</th><th className="th">Client</th><th className="th">Amount</th>
            <th className="th">Your limit</th><th className="th">Status</th><th className="th text-right">Action</th>
          </tr></thead>
          <tbody>
            {rows.map((l) => (
              <tr key={l.id} className="border-b border-border/60">
                <td className="td font-mono text-xs">{l.account_number}</td>
                <td className="td">{l.client_name}</td>
                <td className="td font-semibold">{fmtKES(l.principal)}</td>
                <td className="td">{l.approval_limit == null ? "∞" : fmtKES(l.approval_limit)}
                  {l.over_limit && <span className="ml-1 text-[10px] text-amber-600 font-bold">OVER</span>}</td>
                <td className="td"><Badge value={l.escalation_level ? "underwriting" : l.status}>{l.escalation_level ? `esc→${l.escalation_level}` : l.status}</Badge></td>
                <td className="td text-right space-x-1 whitespace-nowrap">
                  <button className="btn-primary !py-1 !px-2 text-xs" onClick={() => act(l.id, "approve")}>Approve</button>
                  <button className="btn-ghost !py-1 !px-2 text-xs" onClick={() => act(l.id, "escalate")}>Escalate</button>
                  <button className="btn-ghost !py-1 !px-2 text-xs text-red-600" onClick={() => act(l.id, "reject")}>Reject</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ClientApprovals() {
  const [rows, reload] = useList("/api/v1/approvals/clients");
  const [msg, setMsg] = useState("");
  const act = async (id, action) => {
    const note = action === "reject" ? (prompt("Reason?") || "") : null;
    try { await api(`/api/v1/approvals/clients/${id}`, { method: "POST", body: { action, note } }); setMsg(`Client ${action}d`); reload(); }
    catch (e) { setMsg(e.detail); }
  };
  if (!rows) return <Spinner />;
  return (
    <div className="card overflow-hidden">
      {msg && <div className="px-4 py-2 text-sm bg-teal-50 text-teal-700 border-b border-border">{msg}</div>}
      {rows.length === 0 ? <Empty text="No client profiles awaiting approval" /> : (
        <table className="w-full text-sm">
          <thead><tr className="text-left border-b border-border bg-canvas">
            <th className="th">Name</th><th className="th">National ID</th><th className="th">Phone</th>
            <th className="th">KYC</th><th className="th text-right">Action</th>
          </tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="border-b border-border/60">
                <td className="td font-medium">{c.name}</td>
                <td className="td">{c.national_id}</td><td className="td">{c.phone}</td>
                <td className="td"><Badge value={c.kyc_status} /></td>
                <td className="td text-right space-x-1">
                  <button className="btn-primary !py-1 !px-2 text-xs" onClick={() => act(c.id, "approve")}>Approve</button>
                  <button className="btn-ghost !py-1 !px-2 text-xs text-red-600" onClick={() => act(c.id, "reject")}>Reject</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function PendingActions() {
  const [rows, reload] = useList("/api/v1/approvals/pending-actions");
  const [msg, setMsg] = useState("");
  const act = async (id, action) => {
    try { const r = await api(`/api/v1/approvals/pending-actions/${id}`, { method: "POST", body: { action } }); setMsg(`Action ${r.status}`); reload(); }
    catch (e) { setMsg(e.detail); }
  };
  if (!rows) return <Spinner />;
  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-2 text-xs text-gray-500 border-b border-border">Maker-checker: the user who initiated an action cannot approve it.</div>
      {msg && <div className="px-4 py-2 text-sm bg-teal-50 text-teal-700 border-b border-border">{msg}</div>}
      {rows.length === 0 ? <Empty text="No pending money-movement actions" /> : (
        <table className="w-full text-sm">
          <thead><tr className="text-left border-b border-border bg-canvas">
            <th className="th">Type</th><th className="th">Loan</th><th className="th">Amount</th>
            <th className="th">Initiated by</th><th className="th text-right">Action</th>
          </tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id} className="border-b border-border/60">
                <td className="td capitalize">{p.action_type}</td>
                <td className="td">#{p.loan_id || "—"}</td>
                <td className="td font-semibold">{fmtKES(p.amount)}</td>
                <td className="td text-xs">{p.maker_email}</td>
                <td className="td text-right space-x-1">
                  {p.is_own ? <span className="text-xs text-gray-400 italic">your request</span> : (
                    <>
                      <button className="btn-primary !py-1 !px-2 text-xs" onClick={() => act(p.id, "approve")}>Approve</button>
                      <button className="btn-ghost !py-1 !px-2 text-xs text-red-600" onClick={() => act(p.id, "reject")}>Reject</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
