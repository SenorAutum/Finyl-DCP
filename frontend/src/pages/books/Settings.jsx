// WP14 — the chart of accounts and the accounting settings: financial year,
// when the books start, automatic posting, approval limits, and which account
// the system posts each kind of transaction to.
import { useState } from "react";
import { Badge, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, notify } from "../../lib/api";
import { BooksTabs, ErrorNote, Field, Note, Tabs, acc, exportCsv, forgetAccounts, useLoad } from "./common";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export default function BooksSettings() {
  const [tab, setTab] = useState("accounts");
  return (
    <div>
      <PageHeader title="Accounts & settings" crumbs={["Accounting", "Settings"]} />
      <BooksTabs />
      <Tabs tabs={[["accounts", "Chart of accounts"], ["settings", "Settings"], ["posting", "Posting accounts"]]} value={tab} onChange={setTab} />
      {tab === "accounts" && <Chart />}
      {tab === "settings" && <General />}
      {tab === "posting" && <Posting />}
    </div>
  );
}

function Chart() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useLoad("/api/v1/books/accounts");
  const [edit, setEdit] = useState(null);
  const [showOff, setShowOff] = useState(false);
  if (loading && !data) return <Spinner />;
  const rows = (data?.accounts || []).filter((a) => showOff || a.active);
  const T = { asset: "Assets", liability: "Liabilities", equity: "Equity", income: "Income", expense: "Expenses" };
  return (
    <div className="card overflow-hidden">
      <div className="flex gap-2 items-center px-4 py-3 border-b border-border">
        <label className="text-sm flex items-center gap-2"><input type="checkbox" checked={showOff} onChange={(e) => setShowOff(e.target.checked)} /> Show switched-off accounts</label>
        <div className="flex-1" />
        <button className="btn-ghost" onClick={() => exportCsv("chart-of-accounts.csv", [["Code", "code"], ["Name", "name"], ["Type", "type"], ["Group", "subtype_label"],
          ["Balance", "balance"], ["Active", "active"]], data.accounts)}>Export CSV</button>
        {can("accounting.settings") && <button className="btn-primary" onClick={() => setEdit({ code: "", name: "", type: "expense", subtype: "", is_cash: false, description: "" })}>New account</button>}
      </div>
      <ErrorNote text={error} />
      <table className="min-w-full text-sm">
        <thead><tr><th className="th">Code</th><th className="th">Account</th><th className="th">Group</th><th className="th text-right">Balance</th><th className="th" /></tr></thead>
        <tbody>
          {Object.keys(T).map((t) => (
            [<tr key={t}><td colSpan={5} className="pt-4 pb-1 px-4 font-extrabold text-gray-600">{T[t]}</td></tr>,
              ...rows.filter((a) => a.type === t).map((a) => (
                <tr key={a.id} className={a.active ? "" : "opacity-50"}>
                  <td className="td font-mono text-xs">{a.code}</td>
                  <td className="td">{a.name} {a.is_cash && <Badge value="approved">money</Badge>} {a.roles.length > 0 && <span className="text-[11px] text-gray-400" title={a.roles.join(", ")}>· system posts here</span>}</td>
                  <td className="td text-gray-500">{a.subtype_label}</td>
                  <td className="td text-right tabnums">{acc(a.balance)}</td>
                  <td className="td">{can("accounting.settings") && <button className="text-xs font-semibold text-accent" onClick={() => setEdit(a)}>Edit</button>}</td>
                </tr>
              ))]
          ))}
        </tbody>
      </table>
      {edit && <AccountForm a={edit} meta={data} onClose={() => setEdit(null)} onDone={() => { setEdit(null); forgetAccounts(); reload(); }} />}
    </div>
  );
}

function AccountForm({ a, meta, onClose, onDone }) {
  const [f, setF] = useState({ ...a });
  const [err, setErr] = useState("");
  const subs = (meta?.subtypes || []).filter((s) => s.type === f.type);
  const save = async () => {
    setErr("");
    try {
      if (a.id) {
        await api(`/api/v1/books/accounts/${a.id}`, { method: "PUT", body: { name: f.name, subtype: f.subtype || null, is_cash: f.is_cash, active: f.active, description: f.description || null } });
      } else {
        await api("/api/v1/books/accounts", { method: "POST", body: { code: f.code, name: f.name, type: f.type, subtype: f.subtype || null, is_cash: f.is_cash, description: f.description || null } });
      }
      notify("Account saved", { tone: "success" }); onDone();
    } catch (e) { setErr(e.message); }
  };
  return (
    <Modal title={a.id ? `Edit ${a.code} ${a.name}` : "New account"} onClose={onClose}>
      <ErrorNote text={err} />
      <div className="grid grid-cols-2 gap-3">
        <Field label="Code" hint="Assets 1xxx, liabilities 2xxx, equity 3xxx, income 4xxx, expenses 5xxx–6xxx">
          <input className="input" value={f.code} disabled={!!a.id} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Type"><select className="input" value={f.type} disabled={!!a.id} onChange={(e) => setF({ ...f, type: e.target.value, subtype: "" })}>
          {(meta?.types || []).map((t) => <option key={t} value={t}>{t}</option>)}</select></Field>
        <Field label="Group (where it shows in the statements)"><select className="input" value={f.subtype || ""} onChange={(e) => setF({ ...f, subtype: e.target.value, is_cash: e.target.value === "cash" || (f.is_cash && e.target.value === "") })}>
          <option value="">Default</option>{subs.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select></Field>
        {f.type === "asset" && <label className="col-span-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={!!f.is_cash} onChange={(e) => setF({ ...f, is_cash: e.target.checked })} />
          Money is kept in this account (bank, M-Pesa, cash) — it can be reconciled and paid from</label>}
        <div className="col-span-2"><Field label="Description"><input className="input" value={f.description || ""} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field></div>
        {a.id && <label className="col-span-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label>}
      </div>
      <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={save}>Save</button></div>
    </Modal>
  );
}

function General() {
  const { can } = useAuth();
  const { data, error, setData } = useLoad("/api/v1/books/settings");
  const [err, setErr] = useState("");
  if (!data) return error ? <ErrorNote text={error} /> : <Spinner />;
  const save = async () => {
    setErr("");
    try {
      const r = await api("/api/v1/books/settings", { method: "PUT", body: { fiscal_year_start_month: Number(data.fiscal_year_start_month),
        books_start: data.books_start || null, auto_post: data.auto_post, journal_approval_limit: Number(data.journal_approval_limit || 0),
        payment_approval_limit: Number(data.payment_approval_limit || 0) } });
      setData(r); notify("Settings saved", { tone: "success" });
    } catch (e) { setErr(e.message); }
  };
  const off = !can("accounting.settings");
  return (
    <div className="card p-5 max-w-2xl">
      <ErrorNote text={err} />
      <div className="grid sm:grid-cols-2 gap-4">
        <Field label="Financial year starts in"><select className="input" disabled={off} value={data.fiscal_year_start_month}
          onChange={(e) => setData({ ...data, fiscal_year_start_month: e.target.value })}>{MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}</select></Field>
        <Field label="Books start on" hint="Loans and payments before this date are not posted: enter opening balances with a journal instead">
          <input type="date" className="input" disabled={off} value={data.books_start || ""} onChange={(e) => setData({ ...data, books_start: e.target.value })} /></Field>
        <Field label="Manual journals up to this amount post without a second person" hint="0 = every manual journal needs a second person">
          <input type="number" className="input" disabled={off} value={data.journal_approval_limit} onChange={(e) => setData({ ...data, journal_approval_limit: e.target.value })} /></Field>
        <Field label="Bills and payments up to this amount need no second person" hint="0 = every bill and payment is approved by someone else">
          <input type="number" className="input" disabled={off} value={data.payment_approval_limit} onChange={(e) => setData({ ...data, payment_approval_limit: e.target.value })} /></Field>
        <label className="sm:col-span-2 flex items-center gap-2 text-sm"><input type="checkbox" disabled={off} checked={data.auto_post} onChange={(e) => setData({ ...data, auto_post: e.target.checked })} />
          Post loans, repayments and Finyl invoices to the books automatically (every 10 minutes)</label>
      </div>
      {!off && <div className="flex justify-end mt-4"><button className="btn-primary" onClick={save}>Save settings</button></div>}
    </div>
  );
}

function Posting() {
  const { can } = useAuth();
  const { data, error, reload } = useLoad("/api/v1/books/accounts");
  const [map, setMap] = useState({});
  const [err, setErr] = useState("");
  if (!data) return error ? <ErrorNote text={error} /> : <Spinner />;
  const save = async () => {
    setErr("");
    try { await api("/api/v1/books/settings", { method: "PUT", body: { posting_map: map } }); setMap({}); forgetAccounts(); reload(); notify("Posting accounts saved", { tone: "success" }); }
    catch (e) { setErr(e.message); }
  };
  return (
    <div className="card p-5 max-w-3xl">
      <Note>Where the system posts each kind of transaction. Change one only if you keep, for example, two banks and want payouts from the second.
        Changes apply to new entries; what is already posted stays where it is.</Note>
      <ErrorNote text={err} />
      <table className="w-full text-sm">
        <tbody>
          {data.roles.map((r) => (
            <tr key={r.key}><td className="py-1.5 pr-3">{r.label}</td>
              <td className="py-1.5 w-80"><select className="input" disabled={!can("accounting.settings")} value={map[r.key] ?? r.code}
                onChange={(e) => setMap({ ...map, [r.key]: e.target.value })}>
                {data.accounts.filter((a) => a.type === r.type && a.active).map((a) => <option key={a.id} value={a.code}>{a.code} {a.name}</option>)}
              </select></td></tr>
          ))}
        </tbody>
      </table>
      {can("accounting.settings") && <div className="flex justify-end mt-4"><button className="btn-primary" disabled={!Object.keys(map).length} onClick={save}>Save</button></div>}
    </div>
  );
}
