// Search results — full-page view for the global multi-entity search. Reads the
// `q` query param, calls the search API and groups hits by entity type; each
// result navigates to the target record's route.
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../lib/api";
import { Empty, PageHeader, Spinner } from "../../components/ui";

const TYPE_META = {
  command: { label: "Commands", icon: "⚡" },
  client: { label: "Clients", icon: "👥" },
  loan: { label: "Loans", icon: "📋" },
  lead: { label: "Leads", icon: "🧭" },
  staff: { label: "Staff", icon: "👤" },
  product: { label: "Products", icon: "⚙" },
};
const TYPE_ORDER = ["command", "client", "loan", "lead", "staff", "product"];

export default function SearchResults() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const q = params.get("q") || "";
  const [term, setTerm] = useState(q);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState("");

  useEffect(() => { setTerm(q); }, [q]);

  useEffect(() => {
    if (!q.trim()) { setData({ query: q, total: 0, hits: [] }); return; }
    setData(null); setErr("");
    api(`/api/v1/search?q=${encodeURIComponent(q)}&limit=50`).then(setData).catch((e) => setErr(e.detail));
  }, [q]);

  const grouped = useMemo(() => {
    const g = {};
    (data?.hits || []).forEach((h) => { (g[h.entity_type] = g[h.entity_type] || []).push(h); });
    return g;
  }, [data]);

  const availableTypes = TYPE_ORDER.filter((t) => grouped[t]?.length);
  const activeTab = tab && grouped[tab] ? tab : "";
  const visible = activeTab ? { [activeTab]: grouped[activeTab] } : grouped;

  const submit = (e) => {
    e.preventDefault();
    setParams(term.trim() ? { q: term.trim() } : {});
  };

  return (
    <div>
      <PageHeader title="Search" crumbs={["Search"]} />
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <form onSubmit={submit} className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="flex-1 min-w-[240px]">
          <label className="label">Search clients, loans, leads, staff, products…</label>
          <input className="input" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Type a name, account number or phone…" autoFocus />
        </div>
        <button className="btn-primary" disabled={!term.trim()}>Search</button>
      </form>

      {!q.trim() ? (
        <div className="card"><Empty text="Enter a search term to begin." /></div>
      ) : !data ? <Spinner /> : (data.hits || []).length === 0 ? (
        <div className="card"><Empty text={`No results for “${q}”.`} /></div>
      ) : (
        <>
          <div className="flex gap-1 border-b border-border mb-5 flex-wrap">
            {[["", `All (${data.total})`], ...availableTypes.map((t) => [t, `${TYPE_META[t].label} (${grouped[t].length})`])].map(([key, label]) => (
              <button key={key} onClick={() => setTab(key)}
                className={`px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition-colors ${
                  activeTab === key ? "border-accent text-accent" : "border-transparent text-gray-500 hover:text-charcoal"}`}>
                {label}
              </button>
            ))}
          </div>

          <div className="space-y-6">
            {TYPE_ORDER.filter((t) => visible[t]?.length).map((t) => (
              <div key={t}>
                <div className="text-[11px] font-bold uppercase tracking-widest text-gray-400 mb-2 flex items-center gap-1.5">
                  <span>{TYPE_META[t].icon}</span> {TYPE_META[t].label}
                </div>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {visible[t].map((h) => (
                    <button key={`${t}-${h.id}-${h.route}`} onClick={() => navigate(h.route)}
                      className="card p-4 text-left hover:border-accent/40 transition-colors">
                      <div className="font-semibold text-sm truncate">{h.label || "—"}</div>
                      <div className="text-xs text-gray-400 truncate mt-0.5">{h.sublabel || h.route}</div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
