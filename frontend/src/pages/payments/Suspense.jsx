// Suspense account: unmatched / overpayment / closed-loan receipts that could not
// be auto-applied. Officers with reconcile.execute can Allocate a receipt to a loan
// or Refund it. Both backend actions are idempotent.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Badge, Empty, PageHeader, Pagination, Spinner } from "../../components/ui";

const kes2 = (n) =>
  n == null ? "—" : `KES ${Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtDT = (d) => (d ? new Date(d).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }) : "—");

const STATUSES = ["", "open", "allocated", "refunded"];

export default function Suspense() {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("open");
  const [page, setPage] = useState(1);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(null);

  const load = () => {
    setData(null);
    api(`/api/v1/payments/suspense?status=${status}&page=${page}&page_size=50`)
      .then(setData).catch((e) => { setMsg(e.detail || "Failed to load suspense entries."); setData({ items: [], total: 0 }); });
  };
  useEffect(load, [status, page]);

  const allocate = async (row) => {
    const loanId = prompt(`Allocate ${kes2(row.amount)} (${row.mpesa_ref || row.phone || "receipt"}) to which loan ID?`);
    if (!loanId) return;
    setBusy(row.id); setMsg("");
    try {
      const r = await api(`/api/v1/payments/suspense/${row.id}/allocate`, { method: "POST", body: { loan_id: Number(loanId) } });
      setMsg(r.status === "already_resolved" ? "Entry was already resolved." : `Allocated to loan #${loanId}.`);
      load();
    } catch (e) { setMsg(e.detail || "Allocation failed."); }
    finally { setBusy(null); }
  };

  const refund = async (row) => {
    if (!confirm(`Refund ${kes2(row.amount)} to ${row.phone || "the payer"}?`)) return;
    const note = prompt("Refund note (optional):") || null;
    setBusy(row.id); setMsg("");
    try {
      const r = await api(`/api/v1/payments/suspense/${row.id}/refund`, { method: "POST", body: { note } });
      setMsg(r.status === "already_resolved" ? "Entry was already resolved." : "Refund recorded.");
      load();
    } catch (e) { setMsg(e.detail || "Refund failed."); }
    finally { setBusy(null); }
  };

  return (
    <div>
      <PageHeader title="Suspense Account" crumbs={["Transactions", "Payments", "Suspense"]} />

      {msg && <div className="mb-3 text-sm text-teal bg-teal-50 rounded-lg p-3">{msg}</div>}

      <div className="card overflow-hidden">
        <div className="p-3 border-b border-border flex flex-wrap items-center gap-3">
          <select className="input !w-auto" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            {STATUSES.map((s) => <option key={s} value={s}>{s ? s : "All statuses"}</option>)}
          </select>
          {data?.open_balance != null && (
            <span className="text-sm text-gray-500">Open balance: <span className="font-bold text-charcoal">{kes2(data.open_balance)}</span></span>
          )}
        </div>
        {!data ? <Spinner /> : data.items.length === 0 ? <Empty text="No suspense entries" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">When</th><th className="th">Source</th><th className="th">M-Pesa Ref</th>
                <th className="th">Phone</th><th className="th">Amount</th><th className="th">Reason</th>
                <th className="th">Status</th><th className="th text-right">Action</th>
              </tr></thead>
              <tbody>
                {data.items.map((s) => (
                  <tr key={s.id} className="border-t border-border hover:bg-canvas/60">
                    <td className="td whitespace-nowrap">{fmtDT(s.created_at)}</td>
                    <td className="td uppercase text-xs font-bold text-gray-500">{(s.source || "—").replace(/_/g, " ")}</td>
                    <td className="td font-mono text-xs">{s.mpesa_ref || "—"}</td>
                    <td className="td">{s.phone || "—"}</td>
                    <td className="td font-semibold">{kes2(s.amount)}</td>
                    <td className="td">{(s.reason || "—").replace(/_/g, " ")}</td>
                    <td className="td">
                      <Badge value={s.status} />
                      {s.matched_loan_id && <span className="ml-1 text-[11px] text-gray-400">#{s.matched_loan_id}</span>}
                    </td>
                    <td className="td text-right space-x-1 whitespace-nowrap">
                      {s.status === "open" ? (
                        <>
                          <button className="btn-primary !py-1 !px-2 text-xs" disabled={busy === s.id} onClick={() => allocate(s)}>Allocate</button>
                          <button className="btn-ghost !py-1 !px-2 text-xs text-red-600" disabled={busy === s.id} onClick={() => refund(s)}>Refund</button>
                        </>
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
