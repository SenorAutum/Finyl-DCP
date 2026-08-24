// Approvals inbox: loan approvals (threshold + escalation), client-profile
// approvals, and maker-checker sign-off for disbursements / refunds.
import { Fragment, useEffect, useState } from "react";
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

const kes2 = (n) =>
  n == null ? "—" : `KES ${Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// Inline, on-demand risk summary for a loan awaiting approval. Fetched only when
// the row is expanded; read-only — the approve/escalate/reject workflow is untouched.
function RiskSummary({ loanId }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let ok = true;
    api(`/api/v1/approvals/loans/${loanId}/risk-summary`)
      .then((d) => ok && setData(d))
      .catch((e) => ok && setErr(e.detail || "Could not load risk summary."));
    return () => { ok = false; };
  }, [loanId]);
  if (err) return <div className="text-xs text-red-600 p-2">{err}</div>;
  if (!data) return <div className="p-2"><Spinner /></div>;

  const scoreLabel = data.credit_score_source === "not_configured" || data.credit_score == null
    ? "Not configured"
    : `${data.credit_score} (${data.credit_score_source})`;

  const Item = ({ label, value, flag }) => (
    <div>
      <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`text-sm font-medium ${flag ? "text-amber-700" : ""}`}>{value}</div>
    </div>
  );

  return (
    <div className="bg-canvas/70 p-4">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        <Item label="Borrower" value={data.borrower_name} />
        <Item label="Requested principal" value={kes2(data.requested_principal)} />
        <Item label="Current exposure" value={kes2(data.current_exposure)} />
        <Item label="Active loans" value={data.active_loan_count} />
        <Item label="PAR flag" value={data.par_flag ? "Yes" : "No"} flag={data.par_flag} />
        <Item label="Arrears amount" value={kes2(data.arrears_amount)} flag={Number(data.arrears_amount) > 0} />
        <Item label="Credit score" value={scoreLabel} />
        <Item label="Approval limit" value={data.approval_limit == null ? "∞" : kes2(data.approval_limit)}
          flag={data.over_approval_limit} />
        {data.suggested_limit && (
          <Item label="Suggested limit" value={kes2(data.suggested_limit.suggested_limit)}
            flag={data.over_suggested_limit} />
        )}
      </div>
      {data.credit_score_note && <div className="text-[11px] text-gray-400 mt-2">{data.credit_score_note}</div>}
      {(data.over_approval_limit || data.over_suggested_limit) && (
        <div className="mt-2 text-xs text-amber-700 bg-amber-50 rounded p-2">
          ⚠ {data.over_approval_limit && "Above your approval limit. "}
          {data.over_suggested_limit && "Above the suggested limit for this borrower."}
        </div>
      )}
    </div>
  );
}

function LoanApprovals() {
  const [rows, reload] = useList("/api/v1/approvals/loans");
  const [msg, setMsg] = useState("");
  const [expanded, setExpanded] = useState(null);
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
              <Fragment key={l.id}>
                <tr className="border-b border-border/60">
                  <td className="td font-mono text-xs">{l.account_number}</td>
                  <td className="td">{l.client_name}</td>
                  <td className="td font-semibold">{fmtKES(l.principal)}</td>
                  <td className="td">{l.approval_limit == null ? "∞" : fmtKES(l.approval_limit)}
                    {l.over_limit && <span className="ml-1 text-[10px] text-amber-600 font-bold">OVER</span>}</td>
                  <td className="td"><Badge value={l.escalation_level ? "underwriting" : l.status}>{l.escalation_level ? `esc→${l.escalation_level}` : l.status}</Badge></td>
                  <td className="td text-right space-x-1 whitespace-nowrap">
                    <button className="btn-ghost !py-1 !px-2 text-xs" onClick={() => setExpanded(expanded === l.id ? null : l.id)}>
                      {expanded === l.id ? "Hide risk" : "Risk ▾"}
                    </button>
                    <button className="btn-primary !py-1 !px-2 text-xs" onClick={() => act(l.id, "approve")}>Approve</button>
                    <button className="btn-ghost !py-1 !px-2 text-xs" onClick={() => act(l.id, "escalate")}>Escalate</button>
                    <button className="btn-ghost !py-1 !px-2 text-xs text-red-600" onClick={() => act(l.id, "reject")}>Reject</button>
                  </td>
                </tr>
                {expanded === l.id && (
                  <tr className="border-b border-border/60">
                    <td className="p-0" colSpan={6}><RiskSummary loanId={l.id} /></td>
                  </tr>
                )}
              </Fragment>
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
