// Borrower registry: searchable list + create/edit modal with KYC status.
import { useEffect, useState } from "react";
import { api, fmtKES } from "../../lib/api";
import { Badge, Empty, Modal, PageHeader, Pagination, Spinner } from "../../components/ui";

const EMPTY_FORM = {
  first_name: "", middle_name: "", last_name: "", national_id: "", phone: "",
  gender: "female", date_of_birth: "", region_id: "", branch_id: "",
  business_sector: "", baseline_monthly_sales: 0, baseline_employees: 0, kyc_status: "draft",
};

export default function Borrowers() {
  const [data, setData] = useState(null);
  const [org, setOrg] = useState({ regions: [], branches: [] });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState(null); // null | {form, id?}
  const [err, setErr] = useState("");

  const load = () => {
    api(`/api/v1/lending/borrowers?search=${encodeURIComponent(search)}&page=${page}`)
      .then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(load, [search, page]);
  useEffect(() => { api("/api/v1/lending/org").then(setOrg).catch(() => {}); }, []);

  const save = async (e) => {
    e.preventDefault();
    setErr("");
    const body = { ...editing.form };
    ["region_id", "branch_id"].forEach((k) => (body[k] = body[k] ? Number(body[k]) : null));
    body.baseline_monthly_sales = Number(body.baseline_monthly_sales || 0);
    body.baseline_employees = Number(body.baseline_employees || 0);
    if (!body.date_of_birth) body.date_of_birth = null;
    if (!body.middle_name) body.middle_name = null;
    try {
      if (editing.id) await api(`/api/v1/lending/borrowers/${editing.id}`, { method: "PUT", body });
      else await api("/api/v1/lending/borrowers", { method: "POST", body });
      setEditing(null); load();
    } catch (e2) { setErr(e2.detail); }
  };

  const openEdit = (b) => setEditing({
    id: b.id,
    form: { ...EMPTY_FORM, ...Object.fromEntries(Object.entries(b).filter(([k]) => k in EMPTY_FORM)),
            date_of_birth: b.date_of_birth || "" },
  });

  const set = (k) => (e) => setEditing((s) => ({ ...s, form: { ...s.form, [k]: e.target.value } }));

  return (
    <div>
      <PageHeader title="Borrowers" crumbs={["Lending", "Borrowers"]}
        actions={<button className="btn-primary" onClick={() => setEditing({ form: { ...EMPTY_FORM } })}>+ New Borrower</button>} />

      <div className="card overflow-hidden">
        <div className="p-3 border-b border-border">
          <input className="input max-w-sm" placeholder="Search name, ID or phone…"
            value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
        </div>
        {!data ? <Spinner /> : data.items.length === 0 ? <Empty /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Name</th><th className="th">National ID</th><th className="th">Phone</th>
                <th className="th">Sector</th><th className="th">Baseline Sales</th><th className="th">KYC</th>
                <th className="th">Score</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {data.items.map((b) => (
                  <tr key={b.id} className="hover:bg-canvas/60">
                    <td className="td font-semibold">{b.full_name}</td>
                    <td className="td">{b.national_id}</td>
                    <td className="td">{b.phone}</td>
                    <td className="td capitalize">{b.business_sector || "—"}</td>
                    <td className="td">{fmtKES(b.baseline_monthly_sales)}</td>
                    <td className="td"><Badge value={b.kyc_status} /></td>
                    <td className="td">{b.credit_score ?? "—"}</td>
                    <td className="td text-right">
                      <button className="btn-ghost !py-1 !px-2.5 text-xs" onClick={() => openEdit(b)}>Edit</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && <Pagination page={page} total={data.total} onPage={setPage} />}
      </div>

      {editing && (
        <Modal title={editing.id ? "Edit Borrower" : "New Borrower"} onClose={() => setEditing(null)} wide>
          <form onSubmit={save} className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {err && <div className="sm:col-span-2 text-sm text-red-600 bg-red-50 rounded-lg p-2">{err}</div>}
            <div><label className="label">First name *</label><input className="input" required value={editing.form.first_name} onChange={set("first_name")} /></div>
            <div><label className="label">Last name *</label><input className="input" required value={editing.form.last_name} onChange={set("last_name")} /></div>
            <div><label className="label">Middle name</label><input className="input" value={editing.form.middle_name || ""} onChange={set("middle_name")} /></div>
            <div><label className="label">National ID *</label><input className="input" required value={editing.form.national_id} onChange={set("national_id")} /></div>
            <div><label className="label">Phone *</label><input className="input" required placeholder="2547XXXXXXXX" value={editing.form.phone} onChange={set("phone")} /></div>
            <div><label className="label">Gender</label>
              <select className="input" value={editing.form.gender || ""} onChange={set("gender")}>
                <option value="female">Female</option><option value="male">Male</option>
              </select></div>
            <div><label className="label">Date of birth</label><input type="date" className="input" value={editing.form.date_of_birth || ""} onChange={set("date_of_birth")} /></div>
            <div><label className="label">Business sector</label>
              <select className="input" value={editing.form.business_sector || ""} onChange={set("business_sector")}>
                <option value="">—</option>
                {["retail", "agriculture", "food", "transport", "services", "manufacturing"].map((s) => <option key={s} value={s}>{s}</option>)}
              </select></div>
            <div><label className="label">Region</label>
              <select className="input" value={editing.form.region_id || ""} onChange={set("region_id")}>
                <option value="">—</option>{org.regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select></div>
            <div><label className="label">Branch</label>
              <select className="input" value={editing.form.branch_id || ""} onChange={set("branch_id")}>
                <option value="">—</option>
                {org.branches.filter((b) => !editing.form.region_id || b.region_id === Number(editing.form.region_id)).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select></div>
            <div><label className="label">Baseline monthly sales (KES)</label><input type="number" className="input" value={editing.form.baseline_monthly_sales} onChange={set("baseline_monthly_sales")} /></div>
            <div><label className="label">Baseline employees</label><input type="number" className="input" value={editing.form.baseline_employees} onChange={set("baseline_employees")} /></div>
            <div><label className="label">KYC status</label>
              <select className="input" value={editing.form.kyc_status} onChange={set("kyc_status")}>
                {["draft", "pending", "validated", "rejected"].map((s) => <option key={s} value={s}>{s}</option>)}
              </select></div>
            <div className="sm:col-span-2 flex justify-end gap-2 pt-2">
              <button type="button" className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn-primary">{editing.id ? "Save changes" : "Create borrower"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
