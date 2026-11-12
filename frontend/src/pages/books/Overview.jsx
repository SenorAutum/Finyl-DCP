// WP14 — the books at a glance: money in each account, loan book, payables,
// this month and this year, what waits for approval, and the last six months.
import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { KpiCard, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { BooksTabs, ErrorNote, kes, useLoad } from "./common";

export default function BooksOverview() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useLoad("/api/v1/books/overview");
  const [busy, setBusy] = useState(false);
  const postNow = async () => {
    setBusy(true);
    try {
      const r = await api("/api/v1/books/sync", { method: "POST" });
      notify(`${r.posted} new record${r.posted === 1 ? "" : "s"} posted to the books`, { tone: "success" });
      if (r.errors?.length) notify(`${r.errors.length} could not be posted: ${r.errors[0]}`);
      reload();
    } catch (e) { notify(e.message); } finally { setBusy(false); }
  };
  const d = data;
  const waiting = d ? (d.waiting_to_post.loans + d.waiting_to_post.repayments) : 0;
  const pend = d?.pending || {};
  return (
    <div>
      <PageHeader title="Accounting" crumbs={["Accounting", "Overview"]}
        actions={can("accounting.post") && <button className="btn-ghost" disabled={busy} onClick={postNow}>{busy ? "Posting…" : "Post now"}</button>} />
      <BooksTabs />
      <ErrorNote text={error} />
      {loading && !d ? <Spinner /> : d && (
        <>
          <div className="text-xs text-gray-500 mb-4 flex flex-wrap gap-x-4 gap-y-1">
            <span>Books updated {d.last_auto_post_at ? fmtDate(d.last_auto_post_at) : "never"} {d.auto_post ? "(automatic every 10 minutes)" : "(automatic posting is off)"}</span>
            {waiting > 0 && <span className="text-amber-600 font-semibold">{waiting} loans/payments waiting to be posted</span>}
            <span>{d.lock_date ? `Closed up to ${fmtDate(d.lock_date)}` : "No period closed yet"}</span>
            {d.books_start && <span>Books start {fmtDate(d.books_start)}</span>}
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
            <KpiCard label="Money in bank, M-Pesa and cash" value={kes(d.cash_total)} />
            <KpiCard label="Loan book (principal)" value={kes(d.loan_book)} sub={`Provision ${kes(d.provision)}`} />
            <KpiCard label="Owed to suppliers" value={kes(d.payables)} sub={d.payables_overdue ? `${kes(d.payables_overdue)} overdue` : "nothing overdue"}
              tone={d.payables_overdue ? "warning" : "default"} />
            <KpiCard label="Profit this month" value={kes(d.month.profit)} sub={`Year to date ${kes(d.year.profit)}`}
              tone={d.month.profit < 0 ? "danger" : "success"} />
          </div>
          <div className="grid lg:grid-cols-3 gap-4 mb-5">
            <div className="card p-4 lg:col-span-2">
              <div className="font-bold mb-2">Income and expenses, last 6 months</div>
              <div className="h-64">
                <ResponsiveContainer>
                  <BarChart data={d.trend}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => (Math.abs(v) >= 1000 ? `${Math.round(v / 1000)}k` : v)} />
                    <Tooltip formatter={(v) => kes(v)} />
                    <Legend />
                    <Bar dataKey="income" name="Income" fill="#10B981" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="expenses" name="Expenses" fill="#334155" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div className="card p-4">
              <div className="font-bold mb-2">Money accounts</div>
              <table className="w-full text-sm">
                <tbody>
                  {d.cash.map((c) => (
                    <tr key={c.account_id}>
                      <td className="py-1.5"><Link className="hover:underline" to={`/books/reports?tab=ledger&account=${c.account_id}`}>{c.code} {c.name}</Link></td>
                      <td className={`py-1.5 text-right tabnums ${c.balance < 0 ? "text-red-600" : ""}`}>{kes(c.balance)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="font-bold mt-5 mb-2">Waiting for a second person</div>
              <ul className="text-sm space-y-1">
                <li><Link className="hover:underline" to="/books/journals?status=pending">Journals: <b>{pend.journals}</b></Link></li>
                <li><Link className="hover:underline" to="/books/payables?status=pending">Bills: <b>{pend.bills}</b></Link></li>
                <li><Link className="hover:underline" to="/books/payables?tab=payments">Payments: <b>{pend.payments}</b></Link></li>
                <li><Link className="hover:underline" to="/books/claims">Expense claims: <b>{pend.claims}</b></Link></li>
              </ul>
            </div>
          </div>
          <div className="grid sm:grid-cols-2 gap-3">
            <KpiCard label="Income this financial year" value={kes(d.year.income)} />
            <KpiCard label="Expenses this financial year" value={kes(d.year.expenses)} />
          </div>
        </>
      )}
    </div>
  );
}
