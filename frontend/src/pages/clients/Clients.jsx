// Client registry — searchable list with KYC and M-Pesa validation badges.
// Rows open the client profile; "+ New Client" opens the full KYC screen.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmtKES } from "../../lib/api";
import { Badge, Empty, PageHeader, Pagination, Spinner } from "../../components/ui";
import ClientForm from "./ClientForm";

export default function Clients() {
  const nav = useNavigate();
  const [data, setData] = useState(null);
  const [search, setSearch] = useState("");
  const [kyc, setKyc] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState("");

  const load = () => {
    api(`/api/v1/clients?search=${encodeURIComponent(search)}&kyc_status=${kyc}&page=${page}`)
      .then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(load, [search, kyc, page]);

  return (
    <div>
      <PageHeader title="Clients" crumbs={["Lending", "Clients"]}
        actions={<button className="btn-primary" onClick={() => setCreating(true)}>+ New Client</button>} />

      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="card overflow-hidden">
        <div className="p-3 border-b border-border flex flex-wrap gap-2">
          <input className="input max-w-sm" placeholder="Search name, National ID or phone..."
            value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
          <select className="input max-w-[180px]" value={kyc} onChange={(e) => { setKyc(e.target.value); setPage(1); }}>
            <option value="">All KYC statuses</option>
            {["draft", "pending", "validated", "failed", "rejected"].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>

        {!data ? <Spinner /> : data.items.length === 0 ? <Empty text="No clients found" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Name</th><th className="th">National ID</th><th className="th">Mobile</th>
                <th className="th">M-Pesa</th><th className="th">Sector</th><th className="th">Baseline Sales</th>
                <th className="th">KYC</th><th className="th">eKYC</th><th className="th">Score</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.id} className="hover:bg-canvas/60 cursor-pointer" onClick={() => nav(`/clients/${c.id}`)}>
                    <td className="td font-semibold">{c.full_name}</td>
                    <td className="td">{c.national_id}</td>
                    <td className="td">{c.phone || "—"}</td>
                    <td className="td">
                      {c.mpesa_validated
                        ? <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-100 text-emerald-700">✓ validated</span>
                        : <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-gray-200 text-gray-600">unvalidated</span>}
                    </td>
                    <td className="td capitalize">{c.business_sector || "—"}</td>
                    <td className="td">{fmtKES(c.baseline_monthly_sales)}</td>
                    <td className="td"><Badge value={c.kyc_status} /></td>
                    <td className="td">
                      {c.ekyc_status
                        ? <Badge value={c.ekyc_status === "verified" ? "validated" : "pending"}>{c.ekyc_status.replace(/_/g, " ")}</Badge>
                        : <span className="text-gray-400">—</span>}
                    </td>
                    <td className="td">{c.credit_score ?? "—"}</td>
                    <td className="td text-right">
                      <button className="btn-ghost !py-1 !px-2.5 text-xs"
                        onClick={(e) => { e.stopPropagation(); nav(`/clients/${c.id}`); }}>Open</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && <Pagination page={page} total={data.total} onPage={setPage} />}
      </div>

      {creating && (
        <ClientForm
          onClose={() => setCreating(false)}
          onSaved={(c) => { setCreating(false); nav(`/clients/${c.id}`); }}
        />
      )}
    </div>
  );
}
