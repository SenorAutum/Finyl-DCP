// Sign-in codes — every one-time code issued in this DCP: who it was for, what
// it was for (sign-in, client consent...), whether it was used, wrong tries.
// WP7 (review 7.11): this page used to guess at activity-log event names that
// were never recorded, so it was always empty, and it looked names up by staff
// record instead of user. It now reads the codes themselves (never the code
// digits, which are only stored as a hash).
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Empty, PageHeader, Spinner } from "../../components/ui";

const fmtTime = (d) => (d ? new Date(d).toLocaleString("en-KE", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short", year: "numeric" }) : "—");
const PURPOSE_LABEL = { login: "Sign-in", consent: "Client consent", step_up: "Confirm an action" };
const STATE_TONE = {
  "used or replaced": "bg-emerald-100 text-emerald-700",
  waiting: "bg-blue-100 text-blue-700",
  expired: "bg-gray-100 text-gray-600",
  "locked after wrong tries": "bg-red-100 text-red-700",
};

export default function OtpHistory() {
  const [purpose, setPurpose] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setData(null); setErr("");
    const q = new URLSearchParams({ page: String(page), page_size: "50" });
    if (purpose) q.set("purpose", purpose);
    api(`/api/v1/security/otp-history?${q}`).then(setData)
      .catch((e) => { setErr(e.detail || "Could not load sign-in codes"); setData({ items: [], total: 0, purposes: [] }); });
  }, [purpose, page]);

  const pages = data ? Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 50))) : 1;

  return (
    <div>
      <PageHeader title="Sign-in Codes" crumbs={["Settings", "Sign-in Codes"]} />
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="card p-3 mb-4 flex flex-wrap gap-3 items-end">
        <div className="min-w-[200px]">
          <label className="label">What the code was for</label>
          <select className="input" value={purpose} onChange={(e) => { setPurpose(e.target.value); setPage(1); }}>
            <option value="">Everything</option>
            {(data?.purposes || []).map((p) => <option key={p} value={p}>{PURPOSE_LABEL[p] || p}</option>)}
          </select>
        </div>
        <div className="text-xs text-gray-400 pb-2">The codes themselves are never shown or stored in readable form.</div>
      </div>

      <div className="card overflow-hidden">
        {data === null ? <Spinner /> : (data.items || []).length === 0 ? <Empty text="No codes have been issued yet." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Issued</th><th className="th">Staff member</th><th className="th">For</th>
                <th className="th">Sent to</th><th className="th">Result</th><th className="th">Wrong tries</th>
              </tr></thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="td whitespace-nowrap">{fmtTime(r.created_at)}</td>
                    <td className="td font-semibold">{r.user_name || `User ${r.user_id}`}</td>
                    <td className="td">{PURPOSE_LABEL[r.purpose] || r.purpose}{r.client_id ? <span className="text-xs text-gray-400"> · client #{r.client_id}</span> : null}</td>
                    <td className="td tabnums">{r.recipient_last4 ? `…${r.recipient_last4}` : "staff phone / e-mail"}</td>
                    <td className="td"><span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold ${STATE_TONE[r.state] || "bg-gray-100 text-gray-600"}`}>{r.state}</span></td>
                    <td className="td tabnums">{r.wrong_tries}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && pages > 1 && (
          <div className="flex justify-between items-center px-5 py-3 border-t border-border text-sm">
            <button className="btn-ghost !py-1" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="text-gray-500">Page {page} of {pages}</span>
            <button className="btn-ghost !py-1" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        )}
      </div>
    </div>
  );
}
