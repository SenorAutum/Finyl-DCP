// WP16 — the collateral register: what secures each loan, its value and
// insurance, and whether it is held, released, seized or sold.
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { ErrorNote, Field, amt, exportCsv, kes, today, useLoad } from "../books/common";

const KIND = { logbook: "Vehicle logbook", title_deed: "Title deed", household: "Household items", business_equipment: "Business equipment",
  stock: "Stock", savings: "Savings / deposit", chattel: "Chattels", guarantee: "Guarantee", other: "Other" };
const ST = { held: "approved", released: "closed", seized: "overdue", sold: "rejected" };

export function ClientPicker({ value, onPick }) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState([]);
  useEffect(() => {
    if (q.length < 2) { setRows([]); return; }
    const t = setTimeout(() => api(`/api/v1/lending-plus/clients/search?q=${encodeURIComponent(q)}`).then((d) => setRows(d.clients)).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <div>
      <input className="input" placeholder={value ? value.name : "Type a client's name or phone"} value={q} onChange={(e) => setQ(e.target.value)} />
      {rows.length > 0 && <div className="border border-border rounded-lg mt-1 max-h-40 overflow-y-auto">{rows.map((c) => (
        <button key={c.id} type="button" className="block w-full text-left px-3 py-1.5 text-sm hover:bg-canvas" onClick={() => { onPick(c); setQ(""); setRows([]); }}>{c.name} · {c.phone}</button>
      ))}</div>}
      {value && <div className="text-xs text-gray-500 mt-1">Chosen: {value.name}</div>}
    </div>
  );
}

export default function Collateral() {
  const { can } = useAuth();
  const [params] = useSearchParams();
  const [status, setStatus] = useState("held");
  const [q, setQ] = useState("");
  const { data, error, loading, reload } = useLoad(`/api/v1/lending-plus/collateral?status=${status}${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  const [form, setForm] = useState(null);
  const [change, setChange] = useState(null);
  const [err, setErr] = useState("");
  const loanParam = params.get("loan");
  useEffect(() => {
    if (loanParam && can("collateral.manage")) {
      api(`/api/v1/lending/loans/${loanParam}`).then((l) => setForm({ ...blank(), loan_id: Number(loanParam), client: { id: l.borrower_id, name: l.borrower_name || "the loan's client" } })).catch(() => {});
    }
  }, [loanParam]); // eslint-disable-line react-hooks/exhaustive-deps
  function blank() {
    return { client: null, loan_id: "", kind: "logbook", description: "", identifier: "", estimated_value: "", forced_sale_value: "", valued_by: "", valued_on: "",
      location: "", insured: false, insurer: "", policy_no: "", insurance_expiry: "", received_on: today() };
  }
  const save = async () => {
    setErr("");
    try {
      const body = { ...form, borrower_id: form.client?.id, loan_id: form.loan_id ? Number(form.loan_id) : null, estimated_value: Number(form.estimated_value || 0),
        forced_sale_value: form.forced_sale_value === "" ? null : Number(form.forced_sale_value) };
      delete body.client;
      ["valued_on", "insurance_expiry"].forEach((k) => { if (!body[k]) body[k] = null; });
      await api(form.id ? `/api/v1/lending-plus/collateral/${form.id}` : "/api/v1/lending-plus/collateral", { method: form.id ? "PUT" : "POST", body });
      notify("Collateral saved", { tone: "success" }); setForm(null); reload();
    } catch (e) { setErr(e.message); }
  };
  const rows = data?.collateral || [];
  return (
    <div>
      <PageHeader title="Collateral" crumbs={["Lending", "Collateral"]}
        actions={can("collateral.manage") && <button className="btn-primary" onClick={() => setForm(blank())}>Record collateral</button>} />
      <div className="card overflow-hidden">
        <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-border items-center">
          <select className="input !w-auto text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="held">Held</option><option value="released">Released</option><option value="seized">Seized</option><option value="sold">Sold</option><option value="">All</option></select>
          <input className="input !w-64 text-sm" placeholder="Search description or number" value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="text-sm text-gray-500">Total value {kes(data?.total_value)}</span>
          <div className="flex-1" />
          <button className="btn-ghost" disabled={!rows.length} onClick={() => exportCsv("collateral.csv", [["Client", "borrower"], ["Loan", "account_number"], ["Kind", "kind"],
            ["Description", "description"], ["Number", "identifier"], ["Value", "estimated_value"], ["Forced sale", "forced_sale_value"], ["Insurer", "insurer"],
            ["Insurance expiry", "insurance_expiry"], ["Status", "status"]], rows)}>Export CSV</button>
        </div>
        <ErrorNote text={error} />
        {loading && !data ? <Spinner /> : !rows.length ? <Empty text="Nothing here." /> : (
          <table className="min-w-full text-sm"><thead><tr><th className="th">Client</th><th className="th">Loan</th><th className="th">Collateral</th><th className="th text-right">Value</th>
            <th className="th text-right">Forced sale</th><th className="th">Insurance</th><th className="th">Status</th><th className="th" /></tr></thead>
            <tbody>{rows.map((c) => (
              <tr key={c.id}><td className="td font-semibold">{c.borrower}</td>
                <td className="td">{c.loan_id ? <Link className="text-accent font-mono text-xs" to={`/loans/${c.loan_id}`}>{c.account_number}</Link> : "—"}<div className="text-xs text-gray-400">{c.loan_status}</div></td>
                <td className="td whitespace-normal">{KIND[c.kind]}: {c.description}<div className="text-xs text-gray-400">{c.identifier} {c.location && `· ${c.location}`}</div></td>
                <td className="td text-right tabnums">{amt(c.estimated_value)}</td><td className="td text-right tabnums">{c.forced_sale_value != null ? amt(c.forced_sale_value) : ""}</td>
                <td className={`td ${c.insurance_lapsed ? "text-red-600 font-semibold" : ""}`}>{c.insured ? `${c.insurer || ""} to ${fmtDate(c.insurance_expiry)}${c.insurance_lapsed ? " (lapsed)" : ""}` : "—"}</td>
                <td className="td"><Badge value={ST[c.status]}>{c.status}</Badge>{c.release_due && <div className="text-xs text-emerald-700">loan repaid: release it</div>}</td>
                <td className="td whitespace-nowrap">{can("collateral.manage") && c.status === "held" && <>
                  <button className="text-xs font-semibold text-accent mr-2" onClick={() => setForm({ ...c, client: { id: c.borrower_id, name: c.borrower }, valued_on: c.valued_on || "", insurance_expiry: c.insurance_expiry || "", forced_sale_value: c.forced_sale_value ?? "" })}>Edit</button>
                  <button className="text-xs text-gray-500" onClick={() => setChange({ id: c.id, status: "released", date: today(), note: "" })}>Release / seize</button></>}
                  {can("collateral.manage") && c.status === "seized" && <button className="text-xs text-gray-500" onClick={() => setChange({ id: c.id, status: "sold", date: today(), note: "" })}>Sold / returned</button>}</td></tr>
            ))}</tbody></table>
        )}
      </div>
      {form && (
        <Modal title={form.id ? "Edit collateral" : "Record collateral"} onClose={() => setForm(null)} wide>
          <ErrorNote text={err} />
          <div className="grid sm:grid-cols-3 gap-3">
            <div className="sm:col-span-2"><Field label="Client">{form.id ? <div className="text-sm">{form.client?.name}</div> : <ClientPicker value={form.client} onPick={(c) => setForm({ ...form, client: c })} />}</Field></div>
            <Field label="Loan number (id, optional)"><input className="input" value={form.loan_id || ""} onChange={(e) => setForm({ ...form, loan_id: e.target.value })} /></Field>
            <Field label="Kind"><select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <div className="sm:col-span-2"><Field label="Description"><input className="input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="e.g. Toyota Probox 2012, white" /></Field></div>
            <Field label="Registration / title / serial no."><input className="input" value={form.identifier || ""} onChange={(e) => setForm({ ...form, identifier: e.target.value })} /></Field>
            <Field label="Estimated value (KES)"><input type="number" className="input" value={form.estimated_value} onChange={(e) => setForm({ ...form, estimated_value: e.target.value })} /></Field>
            <Field label="Forced-sale value (KES)"><input type="number" className="input" value={form.forced_sale_value} onChange={(e) => setForm({ ...form, forced_sale_value: e.target.value })} /></Field>
            <Field label="Valued by"><input className="input" value={form.valued_by || ""} onChange={(e) => setForm({ ...form, valued_by: e.target.value })} /></Field>
            <Field label="Valued on"><input type="date" className="input" value={form.valued_on || ""} onChange={(e) => setForm({ ...form, valued_on: e.target.value })} /></Field>
            <Field label="Where it is kept"><input className="input" value={form.location || ""} onChange={(e) => setForm({ ...form, location: e.target.value })} /></Field>
            <Field label="Received on"><input type="date" className="input" value={form.received_on} onChange={(e) => setForm({ ...form, received_on: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!form.insured} onChange={(e) => setForm({ ...form, insured: e.target.checked })} /> Insured</label>
            {form.insured && <>
              <Field label="Insurer"><input className="input" value={form.insurer || ""} onChange={(e) => setForm({ ...form, insurer: e.target.value })} /></Field>
              <Field label="Policy no."><input className="input" value={form.policy_no || ""} onChange={(e) => setForm({ ...form, policy_no: e.target.value })} /></Field>
              <Field label="Insurance expires"><input type="date" className="input" value={form.insurance_expiry || ""} onChange={(e) => setForm({ ...form, insurance_expiry: e.target.value })} /></Field></>}
          </div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn-primary" disabled={!form.client || form.description.length < 3} onClick={save}>Save</button></div>
        </Modal>
      )}
      {change && (
        <Modal title="Change collateral status" onClose={() => setChange(null)}>
          <ErrorNote text={err} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Now"><select className="input" value={change.status} onChange={(e) => setChange({ ...change, status: e.target.value })}>
              <option value="released">Released to the client</option><option value="seized">Seized</option><option value="sold">Sold</option></select></Field>
            <Field label="Date"><input type="date" className="input" value={change.date} onChange={(e) => setChange({ ...change, date: e.target.value })} /></Field>
          </div>
          <div className="mt-3"><Field label="Note"><input className="input" value={change.note} onChange={(e) => setChange({ ...change, note: e.target.value })} placeholder="e.g. Logbook handed back, signed for" /></Field></div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setChange(null)}>Cancel</button>
            <button className="btn-primary" disabled={change.note.length < 5} onClick={async () => {
              setErr("");
              try { await api(`/api/v1/lending-plus/collateral/${change.id}/status`, { method: "POST", body: { status: change.status, date: change.date, note: change.note } }); setChange(null); reload(); }
              catch (e) { setErr(e.message); }
            }}>Save</button></div>
        </Modal>
      )}
    </div>
  );
}
