// WP14 — financial statements: profit and loss, balance sheet, cash flow, trial
// balance, any account's ledger and aged payables. Each prints (or saves as PDF
// from the print window) and exports to Excel (CSV).
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Empty, PageHeader, Spinner } from "../../components/ui";
import { download, fmtDate } from "../../lib/api";
import { AccountSelect, BooksTabs, DateRange, ErrorNote, Tabs, acc, amt, exportCsv, today, useBranches, useLoad, yearStart } from "./common";
import { JournalModal } from "./Journals";

const TABS = [["pl", "Profit and loss"], ["bs", "Balance sheet"], ["cf", "Cash flow"], ["tb", "Trial balance"],
  ["ledger", "Account ledger"], ["aged", "Aged payables"]];

export default function Reports() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "pl";
  const [range, setRange] = useState({ from: yearStart(), to: today() });
  const [branch, setBranch] = useState("");
  const branches = useBranches();
  const setTab = (t) => setParams({ tab: t });
  return (
    <div>
      <PageHeader title="Financial statements" crumbs={["Accounting", "Statements"]}
        actions={<>
          <button className="btn-ghost print:hidden" onClick={() => window.print()}>Print / PDF</button>
          <button className="btn-ghost print:hidden" onClick={() => download(`/api/v1/books/export/ledger.csv?from=${range.from}&to=${range.to}`, `ledger_${range.from}_${range.to}.csv`)}>
            Export all entries (CSV)</button>
        </>} />
      <BooksTabs />
      <div className="print:hidden"><Tabs tabs={TABS} value={tab} onChange={setTab} /></div>
      <div className="flex flex-wrap items-end gap-3 mb-4 print:hidden">
        <DateRange from={range.from} to={range.to} onChange={setRange} showFrom={!["bs", "aged"].includes(tab)} />
        {["pl", "tb"].includes(tab) && branches.length > 1 && (
          <label className="block"><span className="label">Branch</span>
            <select className="input !w-auto" value={branch} onChange={(e) => setBranch(e.target.value)}>
              <option value="">All branches</option>{branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select></label>
        )}
      </div>
      {tab === "pl" && <ProfitLoss range={range} branch={branch} />}
      {tab === "bs" && <BalanceSheet asAt={range.to} />}
      {tab === "cf" && <CashFlow range={range} />}
      {tab === "tb" && <TrialBalance range={range} branch={branch} />}
      {tab === "ledger" && <Ledger range={range} initial={params.get("account")} />}
      {tab === "aged" && <Aged asAt={range.to} />}
    </div>
  );
}

function Title({ children, sub }) {
  return <div className="mb-3"><div className="text-lg font-extrabold">{children}</div>{sub && <div className="text-xs text-gray-500">{sub}</div>}</div>;
}

function Section({ s, prev, only = false }) {
  const prevRows = Object.fromEntries((prev?.rows || []).map((r) => [r.code, r.amount]));
  return (
    <>
      <tr><td className="pt-3 pb-1 font-bold text-gray-600" colSpan={3}>{s.label}</td></tr>
      {s.rows.map((r) => (
        <tr key={`${r.code}${r.name}`}><td className="py-0.5 pl-4">{r.code && <span className="text-gray-400 mr-2">{r.code}</span>}{r.name}</td>
          <td className="py-0.5 text-right tabnums">{acc(r.amount)}</td>
          {prev !== undefined && <td className="py-0.5 text-right tabnums text-gray-400">{acc(prevRows[r.code] ?? 0)}</td>}</tr>
      ))}
      {!only && <tr className="border-t border-border"><td className="py-1 pl-4 font-semibold">Total {s.label.toLowerCase()}</td>
        <td className="py-1 text-right tabnums font-semibold">{acc(s.total)}</td>
        {prev !== undefined && <td className="py-1 text-right tabnums text-gray-400">{acc(prev?.total ?? 0)}</td>}</tr>}
    </>
  );
}

function ProfitLoss({ range, branch }) {
  const { data, error, loading } = useLoad(`/api/v1/books/reports/profit-loss?from=${range.from}&to=${range.to}&compare=true${branch ? `&branch_id=${branch}` : ""}`);
  if (loading && !data) return <Spinner />;
  if (!data) return <ErrorNote text={error} />;
  const prev = data.previous;
  const find = (list, key) => (list || []).find((s) => s.key === key);
  const csv = () => exportCsv(`profit-and-loss_${data.from}_${data.to}.csv`, [["Section", "s"], ["Code", "code"], ["Account", "name"], ["Amount", "amount"]],
    [...data.income.flatMap((s) => s.rows.map((r) => ({ ...r, s: s.label }))), ...data.expenses.flatMap((s) => s.rows.map((r) => ({ ...r, s: s.label }))),
      { s: "Net profit", amount: data.net_profit }]);
  return (
    <div className="card p-5 max-w-3xl">
      <div className="flex justify-between"><Title sub={`${fmtDate(data.from)} to ${fmtDate(data.to)} · previous period in grey`}>Profit and loss</Title>
        <button className="btn-ghost print:hidden h-fit" onClick={csv}>Excel</button></div>
      <table className="w-full text-sm">
        <tbody>
          <tr><td className="font-extrabold pt-2" colSpan={3}>Income</td></tr>
          {data.income.map((s) => <Section key={s.key} s={s} only={data.income.length === 1} prev={find(prev?.income, s.key) || null} />)}
          <tr className="border-t-2 border-charcoal"><td className="py-1.5 font-bold">Total income</td><td className="text-right tabnums font-bold">{acc(data.total_income)}</td>
            <td className="text-right tabnums text-gray-400">{acc(prev?.total_income)}</td></tr>
          <tr><td className="font-extrabold pt-4" colSpan={3}>Expenses</td></tr>
          {data.expenses.map((s) => <Section key={s.key} s={s} only={data.expenses.length === 1} prev={find(prev?.expenses, s.key) || null} />)}
          <tr className="border-t-2 border-charcoal"><td className="py-1.5 font-bold">Total expenses</td><td className="text-right tabnums font-bold">{acc(data.total_expenses)}</td>
            <td className="text-right tabnums text-gray-400">{acc(prev?.total_expenses)}</td></tr>
          <tr className="border-t-4 border-double border-charcoal"><td className="py-2 font-extrabold">{data.net_profit >= 0 ? "Profit" : "Loss"}</td>
            <td className={`text-right tabnums font-extrabold ${data.net_profit < 0 ? "text-red-600" : "text-emerald-700"}`}>{acc(data.net_profit)}</td>
            <td className="text-right tabnums text-gray-400">{acc(prev?.net_profit)}</td></tr>
        </tbody>
      </table>
      {!data.income.length && !data.expenses.length && <Empty text="Nothing in the books for these dates yet." />}
    </div>
  );
}

function BalanceSheet({ asAt }) {
  const { data, error, loading } = useLoad(`/api/v1/books/reports/balance-sheet?as_at=${asAt}`);
  if (loading && !data) return <Spinner />;
  if (!data) return <ErrorNote text={error} />;
  const csv = () => exportCsv(`balance-sheet_${data.as_at}.csv`, [["Section", "s"], ["Code", "code"], ["Account", "name"], ["Amount", "amount"]],
    ["assets", "liabilities", "equity"].flatMap((k) => data[k].flatMap((s) => s.rows.map((r) => ({ ...r, s: s.label })))));
  return (
    <div className="card p-5 max-w-3xl">
      <div className="flex justify-between"><Title sub={`As at ${fmtDate(data.as_at)}`}>Balance sheet</Title>
        <button className="btn-ghost print:hidden h-fit" onClick={csv}>Excel</button></div>
      {!data.balanced && <ErrorNote text="Assets do not equal liabilities plus equity. Check the trial balance." />}
      <table className="w-full text-sm">
        <tbody>
          <tr><td className="font-extrabold pt-2" colSpan={2}>Assets</td></tr>
          {data.assets.map((s) => <Section key={s.key} s={s} only={data.assets.length === 1} />)}
          <tr className="border-t-2 border-charcoal"><td className="py-1.5 font-bold">Total assets</td><td className="text-right tabnums font-bold">{acc(data.total_assets)}</td></tr>
          <tr><td className="font-extrabold pt-4" colSpan={2}>Liabilities</td></tr>
          {data.liabilities.map((s) => <Section key={s.key} s={s} only={data.liabilities.length === 1} />)}
          <tr className="border-t-2 border-charcoal"><td className="py-1.5 font-bold">Total liabilities</td><td className="text-right tabnums font-bold">{acc(data.total_liabilities)}</td></tr>
          <tr><td className="font-extrabold pt-4" colSpan={2}>Equity</td></tr>
          {data.equity.map((s) => <Section key={s.key} s={s} only={data.equity.length === 1} />)}
          <tr className="border-t-2 border-charcoal"><td className="py-1.5 font-bold">Total equity</td><td className="text-right tabnums font-bold">{acc(data.total_equity)}</td></tr>
          <tr className="border-t-4 border-double border-charcoal"><td className="py-2 font-extrabold">Liabilities and equity</td>
            <td className="text-right tabnums font-extrabold">{acc(data.total_liabilities + data.total_equity)}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

function CashFlow({ range }) {
  const { data, error, loading } = useLoad(`/api/v1/books/reports/cash-flow?from=${range.from}&to=${range.to}`);
  if (loading && !data) return <Spinner />;
  if (!data) return <ErrorNote text={error} />;
  return (
    <div className="card p-5 max-w-3xl">
      <Title sub={`${fmtDate(data.from)} to ${fmtDate(data.to)} · money in bank, M-Pesa and cash accounts`}>Cash flow</Title>
      <table className="w-full text-sm">
        <tbody>
          <tr><td className="py-1 font-semibold">Money at the start</td><td className="text-right tabnums font-semibold">{acc(data.opening_cash)}</td></tr>
          {data.sections.map((s) => (
            <SectionCF key={s.key} s={s} />
          ))}
          <tr className="border-t-2 border-charcoal"><td className="py-1.5 font-bold">Net change</td><td className="text-right tabnums font-bold">{acc(data.net_change)}</td></tr>
          <tr className="border-t-4 border-double border-charcoal"><td className="py-2 font-extrabold">Money at the end</td><td className="text-right tabnums font-extrabold">{acc(data.closing_cash)}</td></tr>
        </tbody>
      </table>
      {data.check !== 0 && <div className="text-xs text-amber-600 mt-2">Rounding difference {amt(data.check)}</div>}
    </div>
  );
}
function SectionCF({ s }) {
  return (
    <>
      <tr><td className="pt-3 pb-1 font-bold text-gray-600" colSpan={2}>{s.label}</td></tr>
      {s.rows.map((r) => <tr key={r.label}><td className="py-0.5 pl-4">{r.label}</td><td className="text-right tabnums">{acc(r.amount)}</td></tr>)}
      {!s.rows.length && <tr><td className="py-0.5 pl-4 text-gray-400" colSpan={2}>None</td></tr>}
      <tr className="border-t border-border"><td className="py-1 pl-4 font-semibold">Net from {s.label.toLowerCase()}</td><td className="text-right tabnums font-semibold">{acc(s.total)}</td></tr>
    </>
  );
}

function TrialBalance({ range, branch }) {
  const { data, error, loading } = useLoad(`/api/v1/books/reports/trial-balance?from=${range.from}&to=${range.to}${branch ? `&branch_id=${branch}` : ""}`);
  if (loading && !data) return <Spinner />;
  if (!data) return <ErrorNote text={error} />;
  return (
    <div className="card overflow-hidden">
      <div className="flex justify-between px-4 pt-4"><Title sub={`${fmtDate(data.from)} to ${fmtDate(data.to)}`}>Trial balance</Title>
        <button className="btn-ghost print:hidden h-fit" onClick={() => exportCsv(`trial-balance_${data.from}_${data.to}.csv`,
          [["Code", "code"], ["Account", "name"], ["Type", "type"], ["Opening", "opening"], ["Debits", "debits"], ["Credits", "credits"],
            ["Closing debit", "closing_debit"], ["Closing credit", "closing_credit"]], data.rows)}>Excel</button></div>
      {!data.balanced && <ErrorNote text="Debits and credits differ — this should never happen; tell Finyl support." />}
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead><tr><th className="th">Code</th><th className="th">Account</th><th className="th text-right">Opening</th><th className="th text-right">Debits</th>
            <th className="th text-right">Credits</th><th className="th text-right">Closing debit</th><th className="th text-right">Closing credit</th></tr></thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.account_id}><td className="td font-mono text-xs">{r.code}</td><td className="td">{r.name}</td>
                <td className="td text-right tabnums text-gray-500">{acc(r.opening)}</td><td className="td text-right tabnums">{amt(r.debits)}</td>
                <td className="td text-right tabnums">{amt(r.credits)}</td><td className="td text-right tabnums">{r.closing_debit ? amt(r.closing_debit) : ""}</td>
                <td className="td text-right tabnums">{r.closing_credit ? amt(r.closing_credit) : ""}</td></tr>
            ))}
            <tr className="border-t-2 border-charcoal font-bold"><td className="td" colSpan={5}>Totals</td>
              <td className="td text-right tabnums">{amt(data.total_debit)}</td><td className="td text-right tabnums">{amt(data.total_credit)}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Ledger({ range, initial }) {
  const [account, setAccount] = useState(initial ? Number(initial) : null);
  const [open, setOpen] = useState(null);
  const { data, error, loading } = useLoad(account ? `/api/v1/books/reports/ledger/${account}?from=${range.from}&to=${range.to}` : null);
  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex flex-wrap gap-3 items-end print:hidden">
        <div className="w-96"><span className="label">Account</span><AccountSelect value={account} onChange={setAccount} /></div>
        {data && <button className="btn-ghost" onClick={() => exportCsv(`ledger_${data.account.code}_${data.from}_${data.to}.csv`,
          [["Date", "date"], ["Journal", "number"], ["Description", "description"], ["Reference", "reference"], ["Debit", "debit"], ["Credit", "credit"], ["Balance", "balance"]],
          data.rows)}>Excel</button>}
      </div>
      <ErrorNote text={error} />
      {!account ? <Empty text="Choose an account to see every entry in it." /> : loading && !data ? <Spinner /> : data && (
        <div className="overflow-x-auto">
          <div className="px-4 pt-3"><Title sub={`${fmtDate(data.from)} to ${fmtDate(data.to)}`}>{data.account.code} {data.account.name}</Title></div>
          <table className="min-w-full text-sm">
            <thead><tr><th className="th">Date</th><th className="th">Journal</th><th className="th">Description</th><th className="th text-right">Debit</th>
              <th className="th text-right">Credit</th><th className="th text-right">Balance</th></tr></thead>
            <tbody>
              <tr><td className="td" colSpan={5}>Opening balance</td><td className="td text-right tabnums">{acc(data.opening)}</td></tr>
              {data.rows.map((r) => (
                <tr key={r.line_id} className="hover:bg-canvas cursor-pointer" onClick={() => setOpen(r.journal_id)}>
                  <td className="td whitespace-nowrap">{fmtDate(r.date)}</td><td className="td font-mono text-xs">{r.number}</td>
                  <td className="td">{r.description}{r.reference && <span className="text-gray-400"> · {r.reference}</span>}</td>
                  <td className="td text-right tabnums">{r.debit ? amt(r.debit) : ""}</td><td className="td text-right tabnums">{r.credit ? amt(r.credit) : ""}</td>
                  <td className="td text-right tabnums">{acc(r.balance)}</td>
                </tr>
              ))}
              <tr className="font-bold border-t-2 border-charcoal"><td className="td" colSpan={5}>Closing balance (debit positive)</td><td className="td text-right tabnums">{acc(data.closing)}</td></tr>
            </tbody>
          </table>
          {data.truncated && <div className="text-xs text-amber-600 px-4 py-2">Showing the first 2,000 entries: narrow the dates or export everything.</div>}
        </div>
      )}
      {open && <JournalModal id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Aged({ asAt }) {
  const { data, error, loading } = useLoad(`/api/v1/books/reports/aged-payables?as_at=${asAt}`);
  if (loading && !data) return <Spinner />;
  if (!data) return <ErrorNote text={error} />;
  const B = { current: "Not yet due", "1-30": "1–30 days late", "31-60": "31–60", "61-90": "61–90", "90+": "Over 90" };
  return (
    <div className="card overflow-hidden">
      <div className="flex justify-between px-4 pt-4"><Title sub={`What is owed to suppliers as at ${fmtDate(data.as_at)}, by how late it is`}>Aged payables</Title>
        <button className="btn-ghost print:hidden h-fit" onClick={() => exportCsv(`aged-payables_${data.as_at}.csv`,
          [["Supplier", "supplier"], ...data.buckets.map((b) => [B[b], b]), ["Total", "total"]], data.rows)}>Excel</button></div>
      {!data.rows.length ? <Empty text="Nothing is owed to suppliers." /> : (
        <table className="min-w-full text-sm">
          <thead><tr><th className="th">Supplier</th>{data.buckets.map((b) => <th key={b} className="th text-right">{B[b]}</th>)}<th className="th text-right">Total</th></tr></thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.supplier_id}><td className="td font-semibold">{r.supplier}</td>
                {data.buckets.map((b) => <td key={b} className={`td text-right tabnums ${b !== "current" && r[b] ? "text-amber-700" : ""}`}>{r[b] ? amt(r[b]) : ""}</td>)}
                <td className="td text-right tabnums font-semibold">{amt(r.total)}</td></tr>
            ))}
            <tr className="font-bold border-t-2 border-charcoal"><td className="td">Total</td>
              {data.buckets.map((b) => <td key={b} className="td text-right tabnums">{amt(data.totals[b])}</td>)}
              <td className="td text-right tabnums">{amt(data.totals.total)}</td></tr>
          </tbody>
        </table>
      )}
    </div>
  );
}
