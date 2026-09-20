// Device management — bound-device registry per user. Pick a staff member to list
// their registered devices (platform, status, last seen) and revoke any device to
// force re-binding on next login.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Badge, Empty, PageHeader, Spinner } from "../../components/ui";

const fmtTime = (d) => (d ? new Date(d).toLocaleString("en-KE", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short", year: "numeric" }) : "—");

export default function DeviceManagement() {
  const [staff, setStaff] = useState([]);
  const [userId, setUserId] = useState("");
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(0);
  const [err, setErr] = useState("");

  useEffect(() => {
    api("/api/v1/lending/org").then((org) => setStaff(org.staff || [])).catch(() => {});
  }, []);

  const load = () => {
    if (!userId) { setData(null); return; }
    setData(null); setErr("");
    api(`/api/v1/users/${userId}/devices`).then(setData).catch((e) => setErr(e.detail));
  };
  useEffect(load, [userId]);

  const revoke = async (deviceId) => {
    setBusy(deviceId); setErr("");
    try { await api(`/api/v1/users/${userId}/devices/${deviceId}`, { method: "DELETE" }); load(); }
    catch (ex) { setErr(ex.detail || "Could not revoke device"); }
    finally { setBusy(0); }
  };

  return (
    <div>
      <PageHeader title="Device Management" crumbs={["Security", "Devices"]} />
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

      <div className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="min-w-[240px] flex-1">
          <label className="label">Staff member</label>
          <select className="input" value={userId} onChange={(e) => setUserId(e.target.value)}>
            <option value="">Select a staff member…</option>
            {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
      </div>

      {!userId ? (
        <div className="card"><Empty text="Select a staff member to view their bound devices." /></div>
      ) : !data ? <Spinner /> : (data.items || []).length === 0 ? (
        <div className="card"><Empty text="No devices registered for this user." /></div>
      ) : (
        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b border-border font-bold text-base">Registered Devices</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Device</th><th className="th">Platform</th><th className="th">Status</th>
                <th className="th">Registered</th><th className="th">Last seen</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {data.items.map((d) => (
                  <tr key={d.id} className="border-t border-border">
                    <td className="td font-semibold">{d.device_name || `Device ${d.id}`}</td>
                    <td className="td capitalize">{d.platform || "—"}</td>
                    <td className="td"><Badge value={d.active ? "active" : "closed"}>{d.active ? "active" : "revoked"}</Badge></td>
                    <td className="td whitespace-nowrap">{fmtTime(d.registered_at)}</td>
                    <td className="td whitespace-nowrap">{fmtTime(d.last_seen_at)}</td>
                    <td className="td text-right">
                      {d.active && (
                        <button className="btn-ghost !py-1 !px-2.5 text-xs text-red-600" disabled={busy === d.id}
                          onClick={() => revoke(d.id)}>{busy === d.id ? "…" : "Revoke"}</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
