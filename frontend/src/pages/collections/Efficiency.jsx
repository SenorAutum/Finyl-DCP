// Officer collection-efficiency scorecard — ranks officers by the share of their
// promises-to-pay that were honored in a given month, with amounts promised vs
// collected. Officer names are resolved from the lending org staff directory.
import { useEffect, useMemo, useState } from "react";
import { api, fmtKES } from "../../lib/api";
import { Empty, PageHeader, Spinner } from "../../components/ui";

const thisMonth = () => new Date().toISOString().slice(0, 7);

export default function Efficiency() {
  const [month, setMonth] = useState(thisMonth());
  const [data, setData] = useState(null);
  const [staffMap, setStaffMap] = useState({});
  const [err, setErr] = useState("");

  useEffect(() => {
    api("/api/v1/lending/org").then((org) => {
      const m = {};
      (org.staff || []).forEach((s) => { m[s.id] = s.name; });
      setStaffMap(m);
    }).catch(() => {});
  }, []);

  const load = () => {
    setData(null);
    api(`/api/v1/collections/efficiency?period_month=${month}`).then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(load, [month]);

  const rows = useMemo(() => {
    const items = (data?.items || []).slice();
    items.sort((a, b) => (b.efficiency_pct || 0) - (a.efficiency_pct || 0));
    return items;
  }, [data]);

  const bar = (pct) => {
    const v = Math.max(0, Math.min(100, Number(pct) || 0));
    const color = v >= 75 ? "bg-emerald-500" : v >= 50 ? "bg-amber-500" : "bg-red-500";
    return (
      <div className="flex items-center gap-2">
        <div className="flex-1 h-2 rounded-full bg-gray-100 overflow-hidden min-w-[80px]">
          <div className={`h-full ${color}`} style={{ width: `${v}%` }} />
        </div>
        <span className="tabnums text-xs font-semibold w-12 text-right">{v.toFixed(1)}%</span>
      </div>
    );
  };

  return (
    <div>
      <PageHeader title="Collection Efficiency" crumbs={["Field & Collections", "Efficiency"]} />

      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="min-w-[180px]">
          <label className="label">Period (month)</label>
          <input className="input" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-border font-bold text-base">Officer Leaderboard</div>
        {!data ? <Spinner /> : rows.length === 0 ? <Empty text="No efficiency data for this month." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">#</th><th className="th">Officer</th><th className="th w-56">Efficiency</th>
                <th className="th text-right">Logged</th><th className="th text-right">Honored</th><th className="th text-right">Broken</th>
                <th className="th text-right">Promised</th><th className="th text-right">Collected</th>
              </tr></thead>
              <tbody>
                {rows.map((r, i) => {
                  const medal = i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : null;
                  return (
                    <tr key={r.officer_user_id} className={`border-t border-border ${i < 3 ? "bg-emerald-50/40" : ""}`}>
                      <td className="td font-bold tabnums">{medal ? <span className="text-base">{medal}</span> : i + 1}</td>
                      <td className="td font-semibold">{staffMap[r.officer_user_id] || `User ${r.officer_user_id}`}</td>
                      <td className="td">{bar(r.efficiency_pct)}</td>
                      <td className="td text-right tabnums">{r.ptps_logged ?? 0}</td>
                      <td className="td text-right tabnums text-accent">{r.ptps_honored ?? 0}</td>
                      <td className="td text-right tabnums text-red-600">{r.ptps_broken ?? 0}</td>
                      <td className="td text-right tabnums">{fmtKES(r.amount_promised)}</td>
                      <td className="td text-right tabnums">{fmtKES(r.amount_collected)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
