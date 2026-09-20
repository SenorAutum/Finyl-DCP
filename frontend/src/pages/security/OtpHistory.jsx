// OTP history — one-time-password issuance/verification audit. There is no
// dedicated OTP-log endpoint; OTP lifecycle events are recorded in the staff
// activity log, so this view surfaces OTP-tagged activity events (best-effort)
// and degrades to an informational empty state when none are available.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Empty, PageHeader, Spinner } from "../../components/ui";

const fmtTime = (d) => (d ? new Date(d).toLocaleString("en-KE", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short", year: "numeric" }) : "—");
const OTP_EVENTS = ["otp_request", "otp_verify", "otp_sent", "otp_failed", "otp"];

export default function OtpHistory() {
  const [staffMap, setStaffMap] = useState({});
  const [rows, setRows] = useState(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    api("/api/v1/lending/org").then((org) => {
      const m = {}; (org.staff || []).forEach((s) => { m[s.id] = s.name; });
      setStaffMap(m);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    // Best-effort: pull OTP-tagged events from the activity log. Each event_type
    // is queried independently since the API filters on a single type.
    Promise.allSettled(
      OTP_EVENTS.map((t) => api(`/api/v1/activity/logs?event_type=${t}&page=1&page_size=100`))
    ).then((results) => {
      const anyOk = results.some((r) => r.status === "fulfilled");
      if (!anyOk) {
        setRows([]);
        setNote("OTP activity could not be loaded. Ask your administrator to enable activity logging.");
        return;
      }
      const merged = [];
      const seen = new Set();
      results.forEach((r) => {
        if (r.status !== "fulfilled") return;
        (r.value.items || []).forEach((it) => {
          if (seen.has(it.id)) return;
          seen.add(it.id); merged.push(it);
        });
      });
      merged.sort((a, b) => new Date(b.recorded_at) - new Date(a.recorded_at));
      setRows(merged);
      if (merged.length === 0) setNote("No OTP events have been recorded yet.");
    });
  }, []);

  return (
    <div>
      <PageHeader title="OTP History" crumbs={["Security", "OTP History"]} />
      <div className="mb-4 text-xs text-gray-500 bg-surface2 border border-border rounded-lg p-3">
        OTP issuance and verification are captured as activity-log events. This page surfaces those OTP-tagged events.
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-border font-bold text-base">OTP Events</div>
        {rows === null ? <Spinner /> : rows.length === 0 ? <Empty text={note || "No OTP events found."} /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Time</th><th className="th">User</th><th className="th">Event</th>
                <th className="th">Detail</th><th className="th">IP</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="td whitespace-nowrap">{fmtTime(r.recorded_at)}</td>
                    <td className="td font-semibold">{staffMap[r.user_id] || `User ${r.user_id}`}</td>
                    <td className="td"><span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-blue-100 text-blue-700 capitalize">{(r.event_type || "").replace(/_/g, " ")}</span></td>
                    <td className="td max-w-[320px] truncate" title={r.event_detail || ""}>{r.event_detail || "—"}</td>
                    <td className="td tabnums">{r.ip || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
