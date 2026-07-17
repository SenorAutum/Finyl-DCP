// Loan product configuration: rates, tenure, eligibility rules per tenant.
import { useEffect, useState } from "react";
import { api, fmtKES } from "../../lib/api";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";

const EMPTY = {
  name: "", code: "", interest_rate: 10, interest_method: "flat", tenure_value: 4,
  tenure_unit: "weeks", repayment_frequency: "weekly", min_amount: 1000, max_amount: 100000,
  min_age: 18, max_age: 65, penalty_rate: 1.0, active: true,
};

export default function Products() {
  const [products, setProducts] = useState(null);
  const [editing, setEditing] = useState(null);
  const [err, setErr] = useState("");

  const load = () => api("/api/v1/lending/products").then(setProducts).catch(() => {});
  useEffect(load, []);

  const set = (k) => (e) => setEditing((s) => ({ ...s, form: { ...s.form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value } }));

  const save = async (e) => {
    e.preventDefault();
    setErr("");
    const f = editing.form;
    const body = {
      ...f, interest_rate: Number(f.interest_rate), tenure_value: Number(f.tenure_value),
      min_amount: Number(f.min_amount), max_amount: Number(f.max_amount),
      min_age: Number(f.min_age), max_age: Number(f.max_age), penalty_rate: Number(f.penalty_rate),
      rules: {}, active: !!f.active,
    };
    try {
      if (editing.id) await api(`/api/v1/lending/products/${editing.id}`, { method: "PUT", body });
      else await api("/api/v1/lending/products", { method: "POST", body });
      setEditing(null); load();
    } catch (e2) { setErr(e2.detail); }
  };

  return (
    <div>
      <PageHeader title="Loan Products" crumbs={["Configuration", "Products"]}
        actions={<button className="btn-primary" onClick={() => setEditing({ form: { ...EMPTY } })}>+ New Product</button>} />

      {!products ? <Spinner /> : products.length === 0 ? <div className="card"><Empty /></div> : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {products.map((p) => (
            <div key={p.id} className="card p-5">
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-bold">{p.name}</div>
                  <div className="text-xs text-gray-400 font-mono">{p.code}</div>
                </div>
                <Badge value={p.active ? "active" : "closed"}>{p.active ? "active" : "inactive"}</Badge>
              </div>
              <div className="mt-3 space-y-1.5 text-sm">
                <div className="flex justify-between"><span className="text-gray-400">Interest</span><span className="font-semibold">{p.interest_rate}% {p.interest_method}</span></div>
                <div className="flex justify-between"><span className="text-gray-400">Tenure</span><span className="font-semibold">{p.tenure_value} {p.tenure_unit} · {p.repayment_frequency}</span></div>
                <div className="flex justify-between"><span className="text-gray-400">Amount</span><span className="font-semibold">{fmtKES(p.min_amount)} – {fmtKES(p.max_amount)}</span></div>
                <div className="flex justify-between"><span className="text-gray-400">Age eligibility</span><span className="font-semibold">{p.min_age}–{p.max_age} yrs</span></div>
                <div className="flex justify-between"><span className="text-gray-400">Penalty</span><span className="font-semibold">{p.penalty_rate}% / period</span></div>
              </div>
              <button className="btn-ghost w-full mt-4 !py-1.5 text-sm"
                onClick={() => setEditing({ id: p.id, form: { ...EMPTY, ...Object.fromEntries(Object.entries(p).filter(([k]) => k in EMPTY)) } })}>
                Edit
              </button>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <Modal title={editing.id ? "Edit Product" : "New Product"} onClose={() => setEditing(null)} wide>
          <form onSubmit={save} className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {err && <div className="col-span-full text-sm text-red-600 bg-red-50 rounded-lg p-2">{err}</div>}
            <div className="col-span-2"><label className="label">Name *</label><input className="input" required value={editing.form.name} onChange={set("name")} /></div>
            <div><label className="label">Code *</label><input className="input" required value={editing.form.code} onChange={set("code")} /></div>
            <div><label className="label">Interest rate %</label><input type="number" step="0.1" className="input" value={editing.form.interest_rate} onChange={set("interest_rate")} /></div>
            <div><label className="label">Method</label>
              <select className="input" value={editing.form.interest_method} onChange={set("interest_method")}>
                <option value="flat">Flat</option><option value="reducing">Reducing balance</option>
              </select></div>
            <div><label className="label">Penalty % / period</label><input type="number" step="0.1" className="input" value={editing.form.penalty_rate} onChange={set("penalty_rate")} /></div>
            <div><label className="label">Tenure</label><input type="number" className="input" value={editing.form.tenure_value} onChange={set("tenure_value")} /></div>
            <div><label className="label">Unit</label>
              <select className="input" value={editing.form.tenure_unit} onChange={set("tenure_unit")}>
                <option value="weeks">Weeks</option><option value="months">Months</option>
              </select></div>
            <div><label className="label">Frequency</label>
              <select className="input" value={editing.form.repayment_frequency} onChange={set("repayment_frequency")}>
                <option value="weekly">Weekly</option><option value="monthly">Monthly</option>
              </select></div>
            <div><label className="label">Min amount</label><input type="number" className="input" value={editing.form.min_amount} onChange={set("min_amount")} /></div>
            <div><label className="label">Max amount</label><input type="number" className="input" value={editing.form.max_amount} onChange={set("max_amount")} /></div>
            <div className="flex items-end pb-2">
              <label className="flex items-center gap-2 text-sm font-medium">
                <input type="checkbox" checked={!!editing.form.active} onChange={set("active")} /> Active
              </label>
            </div>
            <div><label className="label">Min age</label><input type="number" className="input" value={editing.form.min_age} onChange={set("min_age")} /></div>
            <div><label className="label">Max age</label><input type="number" className="input" value={editing.form.max_age} onChange={set("max_age")} /></div>
            <div className="col-span-full flex justify-end gap-2 pt-2">
              <button type="button" className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn-primary">{editing.id ? "Save changes" : "Create product"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
