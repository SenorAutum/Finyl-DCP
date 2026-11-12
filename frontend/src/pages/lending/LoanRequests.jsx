// WP16 — the inbox for write-off, waiver and reschedule requests.
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, PageHeader, Spinner } from "../../components/ui";
import { api, fmtDate, notify } from "../../lib/api";
import { ErrorNote, Tabs, amt, useLoad } from "../books/common";
import { ACTION_BADGE, ACTION_LABEL, actionSummary } from "./LoanExtras";

export default function LoanRequests() {
  const [status, setStatus] = useState("pending");
  const { data, error, loading, reload } = useLoad(`/api/v1/lending-plus/actions${status ? `?status=${status}` : "?status="}`);
  const [err, setErr] = useState("");
  const decide = async (a, approve) => {
    setErr("");
    const note = approve ? null : window.prompt("Why is it rejected?");
    if (!approve && !note) return;
    try { await api(`/api/v1/lending-plus/actions/${a.id}/decide`, { method: "POST", body: { approve, note } }); notify(approve ? "Approved and carried out" : "Rejected", { tone: "success" }); reload(); }
    catch (e) { setErr(e.message); }
  };
  const rows = data?.actions || [];
  return (
    <div>
      <PageHeader title="Loan requests" crumbs={["Lending", "Loan requests"]} />
      <Tabs tabs={[["pending", "Waiting for approval"], ["done", "Done"], ["rejected", "Rejected"], ["", "All"]]} value={status} onChange={setStatus} />
      <div className="card overflow-hidden">
        <ErrorNote text={error || err} />
        {loading && !data ? <Spinner /> : !rows.length ? <Empty text={status === "pending" ? "Nothing is waiting." : "None."} /> : (
          <table className="min-w-full text-sm"><thead><tr><th className="th">Asked</th><th className="th">Loan</th><th className="th">Client</th><th className="th">Request</th>
            <th className="th text-right">Owed now</th><th className="th">Reason</th><th className="th">Status</th><th className="th" /></tr></thead>
            <tbody>{rows.map((a) => (
              <tr key={a.id}><td className="td whitespace-nowrap">{fmtDate(a.created_at)}<div className="text-xs text-gray-400">{a.requested_by}</div></td>
                <td className="td"><Link className="text-accent font-mono text-xs" to={`/loans/${a.loan_id}`}>{a.account_number}</Link><div className="text-xs text-gray-400">{a.loan_status}</div></td>
                <td className="td">{a.borrower}</td><td className="td"><b>{ACTION_LABEL[a.kind]}</b><div className="text-xs">{actionSummary(a)}</div></td>
                <td className="td text-right tabnums">{amt(a.outstanding)}</td><td className="td whitespace-normal text-gray-600">{a.reason}{a.decision_note && <div className="text-xs">Decision: {a.decision_note}</div>}</td>
                <td className="td"><Badge value={ACTION_BADGE[a.status]}>{a.status}</Badge>{a.decided_by && <div className="text-xs text-gray-400">{a.decided_by}</div>}</td>
                <td className="td whitespace-nowrap">{a.can_decide && <><button className="text-xs font-semibold text-accent mr-3" onClick={() => decide(a, true)}>Approve</button>
                  <button className="text-xs text-gray-500" onClick={() => decide(a, false)}>Reject</button></>}</td></tr>
            ))}</tbody></table>
        )}
      </div>
    </div>
  );
}
