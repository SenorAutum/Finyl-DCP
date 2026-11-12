// WP14 — budgets: an amount per income/expense account per month, for the
// whole DCP or one branch, and how the books compare with it.
import { useEffect, useState } from "react";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, notify } from "../../lib/api";
import { AccountSelect, BooksTabs, ErrorNote, Field, Note, Tabs, acc, amt, exportCsv, useBranches, useLoad } from "./common";

export default function Budgets() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useLoad("/api/v1/books/budgets");
  const [open, setOpen] = useState(null);
  const [creating, setCreating] = useState(false);
  const rows = data?.budgets || [];
  useEffect(() => { if (!open && rows.length) setOpen(rows[0].id); }, [rows.length]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div>
      <PageHeader title="Budgets" crumbs={["Accounting", "Budgets"]}
        actions={can("accounting.settings") && <button className="btn-primary" onClick={() => setCreating(true)}>New budget</button>} />
      <BooksTabs />
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No budgets yet. Create one — you can start from last year's actual figures." /> : (
        <>
          <div className="flex gap-2 flex-wrap mb-4">
            {rows.map((b) => (
              <button key={b.id} onClick={() => setOpen(b.id)}
                className={`px-3 py-1.5 rounded-lg text-sm font-semibold border ${open === b.id ? "bg-charcoal text-white border-charcoal" : "bg-surface border-border"}`}>
                {b.name} · {b.fiscal_year}{b.branch ? ` · ${b.branch}` : ""} {b.status === "approved" && "✓"}
              </button>
            ))}
          </div>
          {open && <BudgetView key={open} id={open} onChange={reload} />}
        </>
      )}
      {creating && <NewBudget onClose={() => setCreating(false)} onDone={(id) => { setCreating(false); reload(); setOpen(id); }} />}
    </div>
  );
}

function BudgetView({ id, onChange }) {
  const { can } = useAuth();
  const [tab, setTab] = useState("vs");
  const bud = useLoad(`/api/v1/books/budgets/${id}`);
  const vs = useLoad(`/api/v1/books/budgets/${id}/vs-actual`);
  const [lines, setLines] = useState(null);
  const [err, setErr] = useState("");
  const b = bud.data;
  const editable = b?.status === "draft" && can("accounting.settings");
  const startEdit = () => setLines(b.lines.map((l) => ({ account_id: l.account_id, amounts: [...l.amounts] })));
  const save = async () => {
    setErr("");
    try { await api(`/api/v1/books/budgets/${id}/lines`, { method: "PUT", body: { lines: lines.filter((l) => l.account_id) } }); setLines(null); bud.reload(); vs.reload(); notify("Budget saved", { tone: "success" }); }
    catch (e) { setErr(e.message); }
  };
  const approve = async () => {
    try { await api(`/api/v1/books/budgets/${id}/approve`, { method: "POST" }); bud.reload(); onChange(); }
    catch (e) { setErr(e.message); }
  };
  if (!b) return <Spinner />;
  return (
    <div className="card p-4">
      <div className="flex items-center gap-3 mb-3 flex-wrap">
        <div className="text-lg font-extrabold flex-1">{b.name} <Badge value={b.status === "approved" ? "approved" : "draft"}>{b.status}</Badge></div>
        {editable && !lines && <button className="btn-ghost" onClick={startEdit}>Edit amounts</button>}
        {b.status === "draft" && can("accounting.approve") && !lines && <button className="btn-primary" onClick={approve}>Approve budget</button>}
      </div>
      <ErrorNote text={err} />
      {lines ? (
        <div className="overflow-x-auto">
          <Note>Amounts per month of the financial year. Paste a figure into the first month and press "Spread" to copy it across.</Note>
          <table className="text-xs">
            <thead><tr><th className="th min-w-56">Account</th>{b.months.map((m) => <th key={m} className="th text-right">{m}</th>)}<th className="th" /></tr></thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={i}>
                  <td className="td"><AccountSelect value={l.account_id} filter={(a) => ["income", "expense"].includes(a.type)}
                    onChange={(v) => setLines(lines.map((x, j) => (j === i ? { ...x, account_id: v } : x)))} /></td>
                  {l.amounts.map((v, k) => (
                    <td key={k} className="td"><input type="number" className="input !w-24 text-right !px-1" value={v || ""}
                      onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, amounts: x.amounts.map((y, z) => (z === k ? Number(e.target.value || 0) : y)) } : x)))} /></td>
                  ))}
                  <td className="td whitespace-nowrap"><button className="text-xs text-accent font-semibold mr-2"
                    onClick={() => setLines(lines.map((x, j) => (j === i ? { ...x, amounts: x.amounts.map(() => x.amounts[0]) } : x)))}>Spread</button>
                    <button className="text-gray-400" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex gap-2 mt-3"><button className="btn-ghost" onClick={() => setLines([...lines, { account_id: null, amounts: Array(12).fill(0) }])}>+ Add an account</button>
            <div className="flex-1" /><button className="btn-ghost" onClick={() => setLines(null)}>Cancel</button><button className="btn-primary" onClick={save}>Save</button></div>
        </div>
      ) : (
        <>
          <Tabs tabs={[["vs", "Budget vs actual"], ["plan", "The budget"]]} value={tab} onChange={setTab} />
          {tab === "vs" && (!vs.data ? <Spinner /> : (
            <div className="overflow-x-auto">
              <div className="text-xs text-gray-500 mb-2">To date: the first {vs.data.months_to_date} month(s) of the financial year. Green is better than budget, red is worse.</div>
              <table className="min-w-full text-sm">
                <thead><tr><th className="th">Account</th><th className="th text-right">Budget to date</th><th className="th text-right">Actual to date</th>
                  <th className="th text-right">Difference</th><th className="th text-right">%</th><th className="th text-right">Budget for the year</th></tr></thead>
                <tbody>
                  {vs.data.rows.map((r) => (
                    <tr key={r.account_id}><td className="td">{r.code} {r.name}</td><td className="td text-right tabnums">{amt(r.budget_ytd)}</td>
                      <td className="td text-right tabnums">{amt(r.actual_ytd)}</td>
                      <td className={`td text-right tabnums font-semibold ${r.variance === 0 ? "" : r.favourable ? "text-emerald-700" : "text-red-600"}`}>{acc(r.variance)}</td>
                      <td className="td text-right tabnums">{r.variance_pct == null ? "" : `${r.variance_pct}%`}</td><td className="td text-right tabnums text-gray-500">{amt(r.budget_year)}</td></tr>
                  ))}
                  {["income", "expense"].map((k) => (
                    <tr key={k} className="font-bold border-t-2 border-charcoal"><td className="td">Total {k === "income" ? "income" : "expenses"}</td>
                      <td className="td text-right tabnums">{amt(vs.data.totals[k].budget_ytd)}</td><td className="td text-right tabnums">{amt(vs.data.totals[k].actual_ytd)}</td><td colSpan={3} /></tr>
                  ))}
                </tbody>
              </table>
              <button className="btn-ghost mt-3" onClick={() => exportCsv(`budget-vs-actual_${b.name}.csv`, [["Code", "code"], ["Account", "name"],
                ["Budget to date", "budget_ytd"], ["Actual to date", "actual_ytd"], ["Difference", "variance"], ["Budget year", "budget_year"]], vs.data.rows)}>Export CSV</button>
            </div>
          ))}
          {tab === "plan" && (!b.lines.length ? <Empty text="No amounts yet. Press 'Edit amounts'." /> : (
            <div className="overflow-x-auto"><table className="text-xs">
              <thead><tr><th className="th">Account</th>{b.months.map((m) => <th key={m} className="th text-right">{m}</th>)}<th className="th text-right">Year</th></tr></thead>
              <tbody>{b.lines.map((l) => <tr key={l.account_id}><td className="td whitespace-nowrap">{l.code} {l.name}</td>
                {l.amounts.map((v, k) => <td key={k} className="td text-right tabnums">{v ? amt(v) : ""}</td>)}<td className="td text-right tabnums font-semibold">{amt(l.total)}</td></tr>)}</tbody>
            </table></div>
          ))}
        </>
      )}
    </div>
  );
}

function NewBudget({ onClose, onDone }) {
  const branches = useBranches();
  const year = new Date().getFullYear();
  const [f, setF] = useState({ name: "Annual budget", fiscal_year: year, branch_id: "", copy_actuals: true, growth_pct: 10 });
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      const r = await api("/api/v1/books/budgets", { method: "POST", body: { ...f, fiscal_year: Number(f.fiscal_year), growth_pct: Number(f.growth_pct || 0),
        branch_id: f.branch_id ? Number(f.branch_id) : null } });
      notify("Budget created", { tone: "success" }); onDone(r.id);
    } catch (e) { setErr(e.message); }
  };
  return (
    <Modal title="New budget" onClose={onClose}>
      <ErrorNote text={err} />
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2"><Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field></div>
        <Field label="Financial year starting in"><input type="number" className="input" value={f.fiscal_year} onChange={(e) => setF({ ...f, fiscal_year: e.target.value })} /></Field>
        <Field label="Branch"><select className="input" value={f.branch_id} onChange={(e) => setF({ ...f, branch_id: e.target.value })}>
          <option value="">Whole company</option>{branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>
        <label className="col-span-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={f.copy_actuals} onChange={(e) => setF({ ...f, copy_actuals: e.target.checked })} />
          Start from last year's actual income and expenses</label>
        {f.copy_actuals && <Field label="…plus growth of (%)"><input type="number" className="input" value={f.growth_pct} onChange={(e) => setF({ ...f, growth_pct: e.target.value })} /></Field>}
      </div>
      <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={save}>Create</button></div>
    </Modal>
  );
}
