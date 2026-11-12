// WP14 — what the DCP owes: suppliers, their bills (with VAT and withholding
// tax), and payments out. Above the DCP's limit a second person approves.
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { AccountSelect, Attachments, BooksTabs, ErrorNote, Field, MoneyAccountSelect, Note, Tabs, amt, exportCsv,
  kes2, today, useBranches, useLoad } from "./common";

const BILL_BADGE = { pending: ["pending", "waiting approval"], approved: ["approved", "unpaid"], part_paid: ["overdue", "part paid"],
  paid: ["paid", "paid"], rejected: ["rejected", "rejected"], void: ["closed", "void"] };

export default function Payables() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "bills";
  const [newBill, setNewBill] = useState(false);
  const [newSup, setNewSup] = useState(null);
  const open = params.get("bill");
  return (
    <div>
      <PageHeader title="Bills & payments" crumbs={["Accounting", "Payables"]}
        actions={can("accounting.post") && <>
          <button className="btn-ghost" onClick={() => setNewSup({})}>New supplier</button>
          <button className="btn-primary" onClick={() => setNewBill(true)}>Enter a bill</button></>} />
      <BooksTabs />
      <Tabs tabs={[["bills", "Bills"], ["payments", "Payments out"], ["suppliers", "Suppliers"]]} value={tab} onChange={(t) => setParams({ tab: t })} />
      {tab === "bills" && <Bills initial={params.get("status") || "unpaid"} onOpen={(id) => setParams({ tab, bill: id })} key={newBill ? 1 : 0} />}
      {tab === "payments" && <Payments />}
      {tab === "suppliers" && <Suppliers onEdit={setNewSup} key={newSup ? 1 : 0} />}
      {newBill && <BillForm onClose={() => setNewBill(false)} onDone={(id) => { setNewBill(false); setParams({ tab: "bills", bill: id }); }} />}
      {newSup && <SupplierForm s={newSup} onClose={() => setNewSup(null)} />}
      {open && <BillModal id={open} onClose={() => setParams({ tab })} />}
    </div>
  );
}

function Bills({ initial, onOpen }) {
  const [status, setStatus] = useState(initial);
  const { data, error, loading } = useLoad(`/api/v1/books/bills${status ? `?status=${status}` : ""}`);
  const rows = data?.bills || [];
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-border">
        <select className="input !w-auto text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="unpaid">Unpaid</option><option value="pending">Waiting for approval</option><option value="paid">Paid</option>
          <option value="void">Void</option><option value="">All bills</option>
        </select>
        <div className="flex-1" />
        <button className="btn-ghost" disabled={!rows.length} onClick={() => exportCsv("bills.csv", [["Number", "number"], ["Supplier", "supplier"],
          ["Their ref", "supplier_ref"], ["Date", "bill_date"], ["Due", "due_date"], ["Before VAT", "subtotal"], ["VAT", "vat"], ["WHT", "wht"],
          ["Total", "total"], ["Paid", "paid"], ["Balance", "balance"], ["Status", "status"]], rows)}>Export CSV</button>
      </div>
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No bills here." /> : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead><tr><th className="th">Number</th><th className="th">Supplier</th><th className="th">Date</th><th className="th">Due</th>
              <th className="th text-right">Total</th><th className="th text-right">Balance</th><th className="th">Status</th></tr></thead>
            <tbody>
              {rows.map((b) => (
                <tr key={b.id} className="hover:bg-canvas cursor-pointer" onClick={() => onOpen(b.id)}>
                  <td className="td font-mono text-xs">{b.number}</td>
                  <td className="td font-semibold">{b.supplier}{b.supplier_ref && <span className="text-gray-400 font-normal"> · {b.supplier_ref}</span>}</td>
                  <td className="td">{fmtDate(b.bill_date)}</td><td className={`td ${b.overdue ? "text-red-600 font-semibold" : ""}`}>{fmtDate(b.due_date)}</td>
                  <td className="td text-right tabnums">{amt(b.total)}</td><td className="td text-right tabnums">{amt(b.balance)}</td>
                  <td className="td"><Badge value={(BILL_BADGE[b.status] || [b.status])[0]}>{(BILL_BADGE[b.status] || [0, b.status])[1]}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function BillForm({ onClose, onDone }) {
  const branches = useBranches();
  const { data: sups } = useLoad("/api/v1/books/suppliers");
  const [f, setF] = useState({ supplier_id: "", bill_date: today(), due_date: "", supplier_ref: "", description: "", branch_id: "", wht_rate: "" });
  const [lines, setLines] = useState([{ account_id: null, description: "", amount: "", vat_rate: "0" }]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const sup = (sups?.suppliers || []).find((s) => String(s.id) === String(f.supplier_id));
  const sub = lines.reduce((s, l) => s + Number(l.amount || 0), 0);
  const vat = lines.reduce((s, l) => s + Math.round(Number(l.amount || 0) * Number(l.vat_rate || 0)) / 100, 0);
  const whtRate = f.wht_rate !== "" ? Number(f.wht_rate) : Number(sup?.wht_rate || 0);
  const wht = Math.round(sub * whtRate) / 100;
  const pickSup = (id) => {
    const s = (sups?.suppliers || []).find((x) => String(x.id) === String(id));
    setF({ ...f, supplier_id: id, wht_rate: "" });
    if (s?.default_expense_account_id && !lines[0].account_id) setLines([{ ...lines[0], account_id: s.default_expense_account_id }, ...lines.slice(1)]);
  };
  const save = async () => {
    setErr(""); setBusy(true);
    try {
      const r = await api("/api/v1/books/bills", { method: "POST", body: {
        supplier_id: Number(f.supplier_id), bill_date: f.bill_date, due_date: f.due_date || null, supplier_ref: f.supplier_ref || null,
        description: f.description || null, branch_id: f.branch_id ? Number(f.branch_id) : null,
        wht_rate: f.wht_rate !== "" ? Number(f.wht_rate) : null,
        lines: lines.filter((l) => l.account_id && Number(l.amount) > 0).map((l) => ({ account_id: l.account_id, description: l.description || null,
          amount: Number(l.amount), vat_rate: Number(l.vat_rate || 0) })) } });
      notify(r.status === "pending" ? `${r.number} saved: a second person approves it before it can be paid` : `${r.number} entered`, { tone: "success" });
      onDone(r.id);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal title="Enter a supplier bill" onClose={onClose} wide>
      <ErrorNote text={err} />
      <div className="grid sm:grid-cols-3 gap-3 mb-3">
        <Field label="Supplier"><select className="input" value={f.supplier_id} onChange={(e) => pickSup(e.target.value)}>
          <option value="">Choose…</option>{(sups?.suppliers || []).filter((s) => s.active).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        <Field label="Their invoice number"><input className="input" value={f.supplier_ref} onChange={(e) => setF({ ...f, supplier_ref: e.target.value })} /></Field>
        <Field label="Bill date"><input type="date" className="input" value={f.bill_date} onChange={(e) => setF({ ...f, bill_date: e.target.value })} /></Field>
        <Field label="Due date" hint="Empty = 30 days"><input type="date" className="input" value={f.due_date} onChange={(e) => setF({ ...f, due_date: e.target.value })} /></Field>
        <Field label="Description"><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        {branches.length > 1 ? (
          <Field label="Branch"><select className="input" value={f.branch_id} onChange={(e) => setF({ ...f, branch_id: e.target.value })}>
            <option value="">Head office</option>{branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>
        ) : <div />}
      </div>
      <table className="w-full text-sm">
        <thead><tr><th className="th">Expense or asset account</th><th className="th">Description</th><th className="th text-right w-32">Amount before VAT</th>
          <th className="th w-24">VAT %</th><th className="th w-8" /></tr></thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td className="td"><AccountSelect value={l.account_id} filter={(a) => (a.type === "expense" || a.type === "asset") && !a.is_cash}
                onChange={(v) => setLines(lines.map((x, j) => (j === i ? { ...x, account_id: v } : x)))} /></td>
              <td className="td"><input className="input" value={l.description} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))} /></td>
              <td className="td"><input type="number" min="0" step="0.01" className="input text-right" value={l.amount}
                onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} /></td>
              <td className="td"><select className="input" value={l.vat_rate} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, vat_rate: e.target.value } : x)))}>
                <option value="0">0 / exempt</option><option value="16">16</option><option value="8">8 (fuel)</option></select></td>
              <td className="td">{lines.length > 1 && <button className="text-gray-400 hover:text-red-600" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="text-sm text-accent font-semibold mt-2" onClick={() => setLines([...lines, { account_id: null, description: "", amount: "", vat_rate: "0" }])}>+ Add a line</button>
      <div className="grid sm:grid-cols-2 gap-4 mt-4">
        <Field label="Withholding tax %" hint={sup ? `Supplier default ${sup.wht_rate}% (professional & consultancy fees are usually 5%)` : ""}>
          <input type="number" min="0" max="30" step="0.01" className="input" placeholder={sup ? String(sup.wht_rate) : "0"} value={f.wht_rate}
            onChange={(e) => setF({ ...f, wht_rate: e.target.value })} /></Field>
        <table className="text-sm self-end">
          <tbody>
            <tr><td className="pr-6">Before VAT</td><td className="text-right tabnums">{amt(sub)}</td></tr>
            <tr><td className="pr-6">VAT</td><td className="text-right tabnums">{amt(vat)}</td></tr>
            <tr><td className="pr-6">Withholding tax (kept, paid to KRA)</td><td className="text-right tabnums">({amt(wht)})</td></tr>
            <tr className="font-bold"><td className="pr-6">Owed to the supplier</td><td className="text-right tabnums">{kes2(sub + vat - wht)}</td></tr>
          </tbody>
        </table>
      </div>
      <div className="flex justify-end gap-2 mt-4">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={busy || !f.supplier_id || !sub} onClick={save}>{busy ? "Saving…" : "Save bill"}</button>
      </div>
    </Modal>
  );
}

function BillModal({ id, onClose }) {
  const { can, user } = useAuth();
  const { data: b, error, reload } = useLoad(`/api/v1/books/bills/${id}`);
  const [mode, setMode] = useState(null);
  const [reason, setReason] = useState("");
  const [p, setP] = useState({ amount: "", paid_on: today(), account_id: null, method: "bank", reference: "" });
  const [err, setErr] = useState("");
  const act = async (path, body) => {
    setErr("");
    try { await api(`/api/v1/books/bills/${id}/${path}`, { method: "POST", body }); setMode(null); reload(); notify("Done", { tone: "success" }); }
    catch (e) { setErr(e.message); }
  };
  const payNow = async () => {
    setErr("");
    try {
      const r = await api("/api/v1/books/payments", { method: "POST", body: { kind: "bill", target_id: Number(id), amount: Number(p.amount),
        paid_on: p.paid_on, account_id: p.account_id, method: p.method, reference: p.reference || null } });
      notify(r.status === "pending" ? `${r.number} waits for approval` : `${r.number} posted`, { tone: "success" });
      setMode(null); reload();
    } catch (e) { setErr(e.message); }
  };
  return (
    <Modal title={b ? `Bill ${b.number}` : "Bill"} onClose={onClose} wide>
      <ErrorNote text={error || err} />
      {!b ? <Spinner /> : (
        <>
          <div className="grid sm:grid-cols-4 gap-3 text-sm mb-4">
            <div className="sm:col-span-2"><div className="label">Supplier</div><b>{b.supplier}</b>{b.supplier_ref && ` · their ref ${b.supplier_ref}`}</div>
            <div><div className="label">Bill date</div>{fmtDate(b.bill_date)}</div>
            <div><div className="label">Due</div><span className={b.overdue ? "text-red-600 font-semibold" : ""}>{fmtDate(b.due_date)}</span></div>
            <div><div className="label">Status</div><Badge value={(BILL_BADGE[b.status] || [b.status])[0]}>{(BILL_BADGE[b.status] || [0, b.status])[1]}</Badge></div>
            <div><div className="label">Entered by</div>{b.created_by || "the system"}</div>
            <div><div className="label">Approved by</div>{b.approved_by || "—"}</div>
            <div><div className="label">Left to pay</div><b>{kes2(b.outstanding)}</b></div>
          </div>
          {b.description && <div className="text-sm mb-3">{b.description}</div>}
          {b.void_reason && <div className="text-sm text-red-600 mb-3">{b.status === "void" ? "Voided" : "Rejected"}: {b.void_reason}</div>}
          <table className="w-full text-sm mb-3">
            <thead><tr><th className="th">Account</th><th className="th">Description</th><th className="th text-right">Amount</th><th className="th text-right">VAT</th></tr></thead>
            <tbody>
              {b.lines.map((l, i) => <tr key={i}><td className="td">{l.account}</td><td className="td whitespace-normal">{l.description}</td>
                <td className="td text-right tabnums">{amt(l.amount)}</td><td className="td text-right tabnums">{amt(l.vat)}</td></tr>)}
              <tr><td className="td text-right" colSpan={2}>Withholding tax deducted</td><td className="td text-right tabnums" colSpan={2}>({amt(b.wht)})</td></tr>
              <tr className="font-bold"><td className="td text-right" colSpan={2}>Owed to the supplier</td><td className="td text-right tabnums" colSpan={2}>{kes2(b.total)}</td></tr>
            </tbody>
          </table>
          {!!b.payments.length && (
            <>
              <div className="label">Payments</div>
              <table className="w-full text-sm mb-3"><tbody>
                {b.payments.map((x) => <tr key={x.id}><td className="td font-mono text-xs">{x.number}</td><td className="td">{fmtDate(x.paid_on)}</td>
                  <td className="td">{x.method} {x.reference}</td><td className="td text-right tabnums">{amt(x.amount)}</td>
                  <td className="td"><Badge value={x.status === "posted" ? "success" : x.status}>{x.status === "pending" ? "waiting" : x.status}</Badge></td></tr>)}
              </tbody></table>
            </>
          )}
          <Attachments entity="bill" id={b.id} items={b.attachments} onDone={reload} canAdd={can("accounting.post")} />
          {mode === "pay" && (
            <div className="mt-4 grid sm:grid-cols-5 gap-3 items-end">
              <Field label="Amount"><input type="number" className="input" value={p.amount} onChange={(e) => setP({ ...p, amount: e.target.value })} /></Field>
              <Field label="Paid on"><input type="date" className="input" value={p.paid_on} onChange={(e) => setP({ ...p, paid_on: e.target.value })} /></Field>
              <div className="sm:col-span-2"><Field label="Paid from"><MoneyAccountSelect value={p.account_id} onChange={(v) => setP({ ...p, account_id: v })} /></Field></div>
              <Field label="Reference"><input className="input" value={p.reference} onChange={(e) => setP({ ...p, reference: e.target.value })} placeholder="Cheque / M-Pesa code" /></Field>
              <div className="sm:col-span-5 flex justify-end gap-2">
                <button className="btn-ghost" onClick={() => setMode(null)}>Back</button>
                <button className="btn-primary" disabled={!Number(p.amount) || !p.account_id} onClick={payNow}>Record payment</button>
              </div>
            </div>
          )}
          {(mode === "reject" || mode === "void") && (
            <div className="mt-4">
              <Field label="Reason"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
              <div className="flex justify-end gap-2 mt-2"><button className="btn-ghost" onClick={() => setMode(null)}>Back</button>
                <button className="btn-primary" disabled={reason.length < 5} onClick={() => act(mode, { reason })}>{mode === "void" ? "Void bill" : "Reject bill"}</button></div>
            </div>
          )}
          {!mode && (
            <div className="flex justify-end gap-2 mt-4 flex-wrap">
              {b.status === "pending" && can("accounting.approve") && <>
                <button className="btn-ghost" onClick={() => setMode("reject")}>Reject</button>
                <button className="btn-primary" disabled={b.created_by_user_id === user?.id && user?.role !== "super_admin"} onClick={() => act("approve")}>Approve</button></>}
              {["approved", "part_paid"].includes(b.status) && can("accounting.approve") && !b.paid && b.source_type !== "finyl_invoice" &&
                <button className="btn-ghost" onClick={() => setMode("void")}>Void</button>}
              {["approved", "part_paid"].includes(b.status) && can("accounting.post") && b.outstanding > 0 &&
                <button className="btn-primary" onClick={() => { setP({ ...p, amount: String(b.outstanding) }); setMode("pay"); }}>Pay this bill</button>}
            </div>
          )}
        </>
      )}
    </Modal>
  );
}

function Payments() {
  const { can, user } = useAuth();
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(`/api/v1/books/payments${status ? `?status=${status}` : ""}`);
  const [err, setErr] = useState("");
  const act = async (id, path, body) => {
    setErr("");
    try { await api(`/api/v1/books/payments/${id}/${path}`, { method: "POST", body }); reload(); notify("Done", { tone: "success" }); }
    catch (e) { setErr(e.message); }
  };
  const rows = data?.payments || [];
  return (
    <div className="card overflow-hidden">
      <div className="flex gap-2 px-4 py-3 border-b border-border">
        <select className="input !w-auto text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All payments</option><option value="pending">Waiting for approval</option><option value="posted">Posted</option><option value="rejected">Rejected</option>
        </select>
      </div>
      <ErrorNote text={error || err} />
      {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No payments yet. Pay a bill or an approved expense claim to see it here." /> : (
        <table className="min-w-full text-sm">
          <thead><tr><th className="th">Number</th><th className="th">Date</th><th className="th">To</th><th className="th">For</th><th className="th">Reference</th>
            <th className="th text-right">Amount</th><th className="th">Status</th><th className="th" /></tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id}><td className="td font-mono text-xs">{p.number}</td><td className="td">{fmtDate(p.paid_on)}</td><td className="td">{p.payee}</td>
                <td className="td text-xs text-gray-500">{p.kind === "bill" ? "Bill" : "Expense claim"}</td><td className="td">{p.method} {p.reference}</td>
                <td className="td text-right tabnums">{amt(p.amount)}</td>
                <td className="td"><Badge value={p.status === "posted" ? "success" : p.status}>{p.status === "pending" ? "waiting" : p.status}</Badge></td>
                <td className="td whitespace-nowrap">{p.status === "pending" && can("accounting.approve") && <>
                  <button className="text-xs font-semibold text-accent mr-2" disabled={p.created_by_user_id === user?.id && user?.role !== "super_admin"}
                    onClick={() => act(p.id, "approve")}>Approve</button>
                  <button className="text-xs text-gray-500" onClick={() => { const r = window.prompt("Why reject this payment?"); if (r) act(p.id, "reject", { reason: r }); }}>Reject</button></>}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Suppliers({ onEdit }) {
  const { can } = useAuth();
  const { data, error, loading } = useLoad("/api/v1/books/suppliers");
  const rows = data?.suppliers || [];
  return (
    <div className="card overflow-hidden">
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No suppliers yet." /> : (
        <table className="min-w-full text-sm">
          <thead><tr><th className="th">Supplier</th><th className="th">KRA PIN</th><th className="th">Pay by</th><th className="th">Contact</th>
            <th className="th text-right">WHT %</th><th className="th text-right">Owed</th><th className="th" /></tr></thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} className={s.active ? "" : "opacity-50"}><td className="td font-semibold">{s.name}</td><td className="td">{s.kra_pin}</td>
                <td className="td">{s.payment_method}{s.mpesa_number && ` ${s.mpesa_number}`}{s.bank_account && ` ${s.bank_name || ""} ${s.bank_account}`}</td>
                <td className="td">{s.phone} {s.email}</td><td className="td text-right">{s.wht_rate}</td><td className="td text-right tabnums">{amt(s.owed)}</td>
                <td className="td">{can("accounting.post") && <button className="text-xs font-semibold text-accent" onClick={() => onEdit(s)}>Edit</button>}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function SupplierForm({ s, onClose }) {
  const [f, setF] = useState({ name: "", kra_pin: "", phone: "", email: "", payment_method: "bank", mpesa_number: "", bank_name: "",
    bank_account: "", default_expense_account_id: null, wht_rate: 0, notes: "", active: true, ...s });
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      const body = { ...f, wht_rate: Number(f.wht_rate || 0) };
      delete body.id; delete body.owed;
      await api(s.id ? `/api/v1/books/suppliers/${s.id}` : "/api/v1/books/suppliers", { method: s.id ? "PUT" : "POST", body });
      notify("Supplier saved", { tone: "success" }); onClose();
    } catch (e) { setErr(e.message); }
  };
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal title={s.id ? `Edit ${s.name}` : "New supplier"} onClose={onClose}>
      <ErrorNote text={err} />
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2"><Field label="Name"><input className="input" value={f.name} onChange={set("name")} /></Field></div>
        <Field label="KRA PIN"><input className="input" value={f.kra_pin || ""} onChange={set("kra_pin")} /></Field>
        <Field label="Withholding tax %" hint="5% for professional/consultancy fees; 0 otherwise">
          <input type="number" className="input" value={f.wht_rate} onChange={set("wht_rate")} /></Field>
        <Field label="Phone"><input className="input" value={f.phone || ""} onChange={set("phone")} /></Field>
        <Field label="Email"><input className="input" value={f.email || ""} onChange={set("email")} /></Field>
        <Field label="Pay by"><select className="input" value={f.payment_method || "bank"} onChange={set("payment_method")}>
          <option value="bank">Bank transfer</option><option value="mpesa">M-Pesa</option><option value="cheque">Cheque</option><option value="cash">Cash</option></select></Field>
        {f.payment_method === "mpesa" ? <Field label="M-Pesa number / till / paybill#account"><input className="input" value={f.mpesa_number || ""} onChange={set("mpesa_number")} /></Field>
          : <Field label="Bank and account"><div className="flex gap-2"><input className="input" placeholder="Bank" value={f.bank_name || ""} onChange={set("bank_name")} />
            <input className="input" placeholder="Account no." value={f.bank_account || ""} onChange={set("bank_account")} /></div></Field>}
        <div className="col-span-2"><Field label="Usual expense account (fills in new bills)">
          <AccountSelect value={f.default_expense_account_id} filter={(a) => a.type === "expense" || (a.type === "asset" && !a.is_cash)}
            onChange={(v) => setF({ ...f, default_expense_account_id: v })} /></Field></div>
        <div className="col-span-2"><Field label="Notes"><input className="input" value={f.notes || ""} onChange={set("notes")} /></Field></div>
        {s.id && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label>}
      </div>
      <Note>Bills from Finyl arrive here automatically each month as bills from "Finyl (Afridon IT Solutions)".</Note>
      <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={f.name.length < 2} onClick={save}>Save</button></div>
    </Modal>
  );
}
