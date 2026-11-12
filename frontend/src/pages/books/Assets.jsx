// WP14 — fixed assets: the register (cost, depreciation, value), monthly
// depreciation, disposal, and the categories that set accounts and useful life.
import { useState } from "react";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { AccountSelect, Attachments, BooksTabs, ErrorNote, Field, MoneyAccountSelect, Note, Tabs, amt, exportCsv, kes2, today,
  useBranches, useLoad } from "./common";

function lastMonthStr() {
  const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function Assets() {
  const { can } = useAuth();
  const [tab, setTab] = useState("register");
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState(null);
  const reg = useLoad("/api/v1/books/assets");
  return (
    <div>
      <PageHeader title="Fixed assets" crumbs={["Accounting", "Fixed assets"]}
        actions={can("accounting.post") && <button className="btn-primary" onClick={() => setAdding(true)}>Register an asset</button>} />
      <BooksTabs />
      <Tabs tabs={[["register", "Register"], ["depreciation", "Depreciation"], ["categories", "Categories"]]} value={tab} onChange={setTab} />
      {tab === "register" && <Register reg={reg} onOpen={setOpen} />}
      {tab === "depreciation" && <Depreciation onDone={reg.reload} />}
      {tab === "categories" && <Categories />}
      {adding && <AssetForm onClose={() => setAdding(false)} onDone={() => { setAdding(false); reg.reload(); }} />}
      {open && <AssetModal id={open} onClose={() => { setOpen(null); reg.reload(); }} />}
    </div>
  );
}

function Register({ reg, onOpen }) {
  const { data, error, loading } = reg;
  const rows = data?.assets || [];
  return (
    <div className="card overflow-hidden">
      <div className="flex items-center gap-4 px-4 py-3 border-b border-border text-sm flex-wrap">
        <span>Cost <b>{kes2(data?.totals?.cost)}</b></span><span>Depreciation <b>{kes2(data?.totals?.accumulated)}</b></span>
        <span>Value now <b>{kes2(data?.totals?.nbv)}</b></span>
        <span className="text-gray-500">Depreciated up to {data?.last_depreciation ? fmtDate(data.last_depreciation) : "— (not run yet)"}</span>
        <div className="flex-1" />
        <button className="btn-ghost" disabled={!rows.length} onClick={() => exportCsv("fixed-assets.csv", [["Number", "number"], ["Asset", "name"], ["Category", "category"],
          ["Branch", "branch"], ["Custodian", "custodian"], ["Serial", "serial_no"], ["Bought", "purchase_date"], ["Cost", "cost"],
          ["Depreciation", "accumulated_depreciation"], ["Value now", "net_book_value"], ["Status", "status"]], rows)}>Export CSV</button>
      </div>
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No assets registered yet." /> : (
        <table className="min-w-full text-sm">
          <thead><tr><th className="th">Number</th><th className="th">Asset</th><th className="th">Category</th><th className="th">Bought</th>
            <th className="th text-right">Cost</th><th className="th text-right">Depreciation</th><th className="th text-right">Value now</th><th className="th">Status</th></tr></thead>
          <tbody>{rows.map((a) => (
            <tr key={a.id} className="hover:bg-canvas cursor-pointer" onClick={() => onOpen(a.id)}>
              <td className="td font-mono text-xs">{a.number}</td><td className="td font-semibold">{a.name}<div className="text-xs text-gray-400 font-normal">{[a.branch, a.custodian, a.serial_no].filter(Boolean).join(" · ")}</div></td>
              <td className="td">{a.category}</td><td className="td">{fmtDate(a.purchase_date)}</td>
              <td className="td text-right tabnums">{amt(a.cost)}</td><td className="td text-right tabnums">{amt(a.accumulated_depreciation)}</td>
              <td className="td text-right tabnums font-semibold">{amt(a.net_book_value)}</td>
              <td className="td"><Badge value={a.status === "active" ? "active" : a.status === "disposed" ? "closed" : "approved"}>{a.status.replace("_", " ")}</Badge></td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  );
}

function AssetForm({ onClose, onDone }) {
  const branches = useBranches();
  const { data: cats } = useLoad("/api/v1/books/asset-categories");
  const [f, setF] = useState({ name: "", category_id: "", purchase_date: today(), in_service_date: "", cost: "", residual_value: "0", funding: "paid",
    paid_from_account_id: null, accumulated_depreciation: "0", branch_id: "", custodian: "", serial_no: "" });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    setErr("");
    try {
      const r = await api("/api/v1/books/assets", { method: "POST", body: { ...f, category_id: Number(f.category_id), cost: Number(f.cost),
        residual_value: Number(f.residual_value || 0), accumulated_depreciation: Number(f.accumulated_depreciation || 0),
        in_service_date: f.in_service_date || null, branch_id: f.branch_id ? Number(f.branch_id) : null } });
      notify(`${r.number} registered`, { tone: "success" }); onDone();
    } catch (e) { setErr(e.message); }
  };
  return (
    <Modal title="Register an asset" onClose={onClose} wide>
      <ErrorNote text={err} />
      <div className="grid sm:grid-cols-3 gap-3">
        <div className="sm:col-span-2"><Field label="What is it?"><input className="input" value={f.name} onChange={set("name")} placeholder="e.g. HP laptop for the CBD branch" /></Field></div>
        <Field label="Category"><select className="input" value={f.category_id} onChange={set("category_id")}>
          <option value="">Choose…</option>{(cats?.categories || []).map((c) => <option key={c.id} value={c.id}>{c.name} ({c.method === "straight_line" ? `${c.useful_life_months} months` : `${c.rate_pct}% a year`})</option>)}</select></Field>
        <Field label="Bought on"><input type="date" className="input" value={f.purchase_date} onChange={set("purchase_date")} /></Field>
        <Field label="In use from" hint="Empty = the day it was bought"><input type="date" className="input" value={f.in_service_date} onChange={set("in_service_date")} /></Field>
        <Field label="Cost (KES)"><input type="number" className="input" value={f.cost} onChange={set("cost")} /></Field>
        <Field label="Value left at the end of its life" hint="Usually 0"><input type="number" className="input" value={f.residual_value} onChange={set("residual_value")} /></Field>
        <Field label="How was it paid for?"><select className="input" value={f.funding} onChange={set("funding")}>
          <option value="paid">Paid from a bank / M-Pesa / cash account</option><option value="bill">On a supplier bill (already entered)</option>
          <option value="opening">Owned before the books started</option></select></Field>
        {f.funding === "paid" && <Field label="Paid from"><MoneyAccountSelect value={f.paid_from_account_id} onChange={(v) => setF({ ...f, paid_from_account_id: v })} /></Field>}
        {f.funding === "opening" && <Field label="Depreciation already charged"><input type="number" className="input" value={f.accumulated_depreciation} onChange={set("accumulated_depreciation")} /></Field>}
        {branches.length > 0 && <Field label="Branch"><select className="input" value={f.branch_id} onChange={set("branch_id")}>
          <option value="">Head office</option>{branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>}
        <Field label="Kept by (custodian)"><input className="input" value={f.custodian} onChange={set("custodian")} /></Field>
        <Field label="Serial / registration no."><input className="input" value={f.serial_no} onChange={set("serial_no")} /></Field>
      </div>
      {f.funding === "bill" && <Note>Choose the asset account (e.g. 1510 Computers) on the bill line, then register the asset here so it depreciates. No second entry is made.</Note>}
      <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={f.name.length < 2 || !f.category_id || !Number(f.cost)} onClick={save}>Register</button></div>
    </Modal>
  );
}

function AssetModal({ id, onClose }) {
  const { can } = useAuth();
  const { data: a, error, reload } = useLoad(`/api/v1/books/assets/${id}`);
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const dispose = async () => {
    setErr("");
    try {
      await api(`/api/v1/books/assets/${id}/dispose`, { method: "POST", body: { date: d.date, proceeds: Number(d.proceeds || 0),
        received_into_account_id: d.account || null, note: d.note || null } });
      notify("Asset disposed", { tone: "success" }); setD(null); reload();
    } catch (e) { setErr(e.message); }
  };
  return (
    <Modal title={a ? `${a.number} ${a.name}` : "Asset"} onClose={onClose} wide>
      <ErrorNote text={error || err} />
      {!a ? <Spinner /> : (
        <>
          <div className="grid sm:grid-cols-4 gap-3 text-sm mb-4">
            <div><div className="label">Category</div>{a.category}</div><div><div className="label">Bought</div>{fmtDate(a.purchase_date)}</div>
            <div><div className="label">Cost</div>{kes2(a.cost)}</div><div><div className="label">Value now</div><b>{kes2(a.net_book_value)}</b></div>
            <div><div className="label">Method</div>{a.method === "straight_line" ? `Straight line, ${a.useful_life_months} months` : `Reducing balance, ${a.rate_pct}% a year`}</div>
            <div><div className="label">Per month now</div>{kes2(a.monthly_charge)}</div><div><div className="label">Status</div>{a.status.replace("_", " ")}</div>
            {a.disposed_on && <div><div className="label">Disposed</div>{fmtDate(a.disposed_on)} for {kes2(a.disposal_proceeds)}</div>}
          </div>
          {!!a.depreciation.length && (
            <table className="w-full text-sm mb-3"><thead><tr><th className="th">Month</th><th className="th text-right">Depreciation</th></tr></thead>
              <tbody>{a.depreciation.map((x) => <tr key={x.period}><td className="td">{fmtDate(x.period)}</td><td className="td text-right tabnums">{amt(x.amount)}</td></tr>)}</tbody></table>
          )}
          <Attachments entity="asset" id={a.id} items={a.attachments} onDone={reload} canAdd={can("accounting.post")} />
          {d && (
            <div className="grid sm:grid-cols-4 gap-3 mt-4 items-end">
              <Field label="Date"><input type="date" className="input" value={d.date} onChange={(e) => setD({ ...d, date: e.target.value })} /></Field>
              <Field label="Sold for (0 if scrapped)"><input type="number" className="input" value={d.proceeds} onChange={(e) => setD({ ...d, proceeds: e.target.value })} /></Field>
              {Number(d.proceeds) > 0 && <Field label="Money went into"><MoneyAccountSelect value={d.account} onChange={(v) => setD({ ...d, account: v })} /></Field>}
              <Field label="Note"><input className="input" value={d.note} onChange={(e) => setD({ ...d, note: e.target.value })} /></Field>
              <div className="sm:col-span-4 flex justify-end gap-2"><button className="btn-ghost" onClick={() => setD(null)}>Back</button>
                <button className="btn-primary" onClick={dispose}>Dispose</button></div>
            </div>
          )}
          {!d && a.status !== "disposed" && can("accounting.approve") && (
            <div className="flex justify-end mt-4"><button className="btn-ghost" onClick={() => setD({ date: today(), proceeds: "0", account: null, note: "" })}>Sell or scrap</button></div>
          )}
        </>
      )}
    </Modal>
  );
}

function Depreciation({ onDone }) {
  const { can } = useAuth();
  const [period, setPeriod] = useState(lastMonthStr());
  const prev = useLoad(`/api/v1/books/depreciation/preview?period=${period}`);
  const runs = useLoad("/api/v1/books/depreciation/runs");
  const [err, setErr] = useState("");
  const run = async () => {
    setErr("");
    try {
      const r = await api("/api/v1/books/depreciation/run", { method: "POST", body: { period } });
      notify(`Depreciation of ${kes2(r.total)} posted for ${fmtDate(r.period)}`, { tone: "success" }); prev.reload(); runs.reload(); onDone();
    } catch (e) { setErr(e.message); }
  };
  return (
    <div className="grid lg:grid-cols-3 gap-4">
      <div className="card p-4 lg:col-span-2">
        <div className="flex items-end gap-3 mb-3">
          <Field label="Month"><input type="month" className="input" value={period} onChange={(e) => setPeriod(e.target.value)} /></Field>
          {can("accounting.close") && <button className="btn-primary" disabled={!prev.data?.rows?.length} onClick={run}>Post depreciation for this month</button>}
        </div>
        <ErrorNote text={err || prev.error} />
        {!prev.data ? <Spinner /> : !prev.data.rows.length ? <Empty text="Nothing to depreciate for this month." /> : (
          <table className="w-full text-sm"><thead><tr><th className="th">Asset</th><th className="th text-right">Value before</th>
            <th className="th text-right">This month</th><th className="th text-right">Value after</th></tr></thead>
            <tbody>{prev.data.rows.map((r) => <tr key={r.asset_id}><td className="td">{r.number} {r.name}</td><td className="td text-right tabnums">{amt(r.nbv_before)}</td>
              <td className="td text-right tabnums">{amt(r.amount)}</td><td className="td text-right tabnums">{amt(r.nbv_after)}</td></tr>)}
              <tr className="font-bold"><td className="td" colSpan={2}>Total</td><td className="td text-right tabnums">{amt(prev.data.total)}</td><td /></tr></tbody></table>
        )}
      </div>
      <div className="card p-4">
        <div className="font-bold mb-2">Months already done</div>
        {!runs.data?.runs?.length ? <Empty text="None yet" /> : (
          <table className="w-full text-sm"><tbody>{runs.data.runs.map((r) => <tr key={r.id}><td className="py-1">{fmtDate(r.period)}</td><td className="text-right tabnums">{amt(r.total)}</td></tr>)}</tbody></table>
        )}
        <div className="text-xs text-gray-500 mt-3">Run each month in order, after the month ends. Each month can be run once.</div>
      </div>
    </div>
  );
}

function Categories() {
  const { can } = useAuth();
  const { data, error, reload } = useLoad("/api/v1/books/asset-categories");
  const [edit, setEdit] = useState(null);
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      const body = { ...edit, useful_life_months: Number(edit.useful_life_months || 0), rate_pct: Number(edit.rate_pct || 0) };
      await api(edit.id ? `/api/v1/books/asset-categories/${edit.id}` : "/api/v1/books/asset-categories", { method: edit.id ? "PUT" : "POST", body });
      setEdit(null); reload();
    } catch (e) { setErr(e.message); }
  };
  return (
    <div className="card p-4">
      <ErrorNote text={error} />
      <table className="w-full text-sm mb-3"><thead><tr><th className="th">Category</th><th className="th">Asset account</th><th className="th">Method</th><th className="th" /></tr></thead>
        <tbody>{(data?.categories || []).map((c) => <tr key={c.id}><td className="td font-semibold">{c.name}</td><td className="td">{c.asset_account}</td>
          <td className="td">{c.method === "straight_line" ? `Straight line over ${c.useful_life_months} months` : `Reducing balance ${c.rate_pct}% a year`}</td>
          <td className="td">{can("accounting.settings") && <button className="text-xs font-semibold text-accent" onClick={() => setEdit(c)}>Edit</button>}</td></tr>)}</tbody></table>
      {can("accounting.settings") && !edit && <button className="btn-ghost" onClick={() => setEdit({ name: "", method: "straight_line", useful_life_months: 36, rate_pct: 0 })}>New category</button>}
      {edit && (
        <div className="grid sm:grid-cols-3 gap-3 border-t border-border pt-3">
          <ErrorNote text={err} />
          <Field label="Name"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
          <Field label="Asset account"><AccountSelect value={edit.asset_account_id} filter={(a) => a.subtype === "fixed_asset"} onChange={(v) => setEdit({ ...edit, asset_account_id: v })} /></Field>
          <Field label="Accumulated depreciation account"><AccountSelect value={edit.accum_account_id} filter={(a) => a.subtype === "accum_dep"} onChange={(v) => setEdit({ ...edit, accum_account_id: v })} /></Field>
          <Field label="Depreciation expense account"><AccountSelect value={edit.expense_account_id} filter={(a) => a.type === "expense"} onChange={(v) => setEdit({ ...edit, expense_account_id: v })} /></Field>
          <Field label="Method"><select className="input" value={edit.method} onChange={(e) => setEdit({ ...edit, method: e.target.value })}>
            <option value="straight_line">Straight line (same each month)</option><option value="reducing_balance">Reducing balance (% of value left)</option></select></Field>
          {edit.method === "straight_line" ? <Field label="Useful life (months)"><input type="number" className="input" value={edit.useful_life_months} onChange={(e) => setEdit({ ...edit, useful_life_months: e.target.value })} /></Field>
            : <Field label="Yearly rate %"><input type="number" className="input" value={edit.rate_pct} onChange={(e) => setEdit({ ...edit, rate_pct: e.target.value })} /></Field>}
          <div className="sm:col-span-3 flex justify-end gap-2"><button className="btn-ghost" onClick={() => setEdit(null)}>Cancel</button><button className="btn-primary" onClick={save}>Save</button></div>
        </div>
      )}
    </div>
  );
}
