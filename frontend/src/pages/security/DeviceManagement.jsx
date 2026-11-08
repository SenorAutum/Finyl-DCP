// Device management — devices each staff member may sign in from (used when the
// DCP turns on "registered devices only" in Security Config).
// WP7: (1) a sign-in from a new device now leaves a request here, shown at the
// top, which another administrator approves; (2) the person picker lists USER
// accounts (it used to list staff records, whose ids are a different number);
// (3) revoking a device also ends that person's open sessions.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Badge, Empty, PageHeader, Spinner } from "../../components/ui";

const fmtTime = (d) => (d ? new Date(d).toLocaleString("en-KE", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short", year: "numeric" }) : "—");

export default function DeviceManagement() {
  const [people, setPeople] = useState([]);
  const [userId, setUserId] = useState("");
  const [data, setData] = useState(null);
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(0);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api("/api/v1/lending/org").then((org) => setPeople(org.users || []))
      .catch((e) => setErr(e.detail || "Could not load staff list"));
  }, []);

  const loadPending = () => api("/api/v1/devices/pending").then((r) => setPending(r.items || []))
    .catch((e) => { setPending([]); setErr(e.detail || "Could not load device requests"); });
  useEffect(() => { loadPending(); }, []);

  const load = () => {
    if (!userId) { setData(null); return; }
    setData(null); setErr("");
    api(`/api/v1/users/${userId}/devices`).then(setData).catch((e) => { setData({ items: [] }); setErr(e.detail); });
  };
  useEffect(() => { load(); }, [userId]);

  const act = async (uid, deviceId, kind) => {
    setBusy(deviceId); setErr(""); setMsg("");
    try {
      if (kind === "approve") {
        await api(`/api/v1/users/${uid}/devices/${deviceId}/approve`, { method: "POST", body: {} });
        setMsg("Device approved. The person can now sign in from it.");
      } else {
        await api(`/api/v1/users/${uid}/devices/${deviceId}`, { method: "DELETE" });
        setMsg("Device revoked, and the person's open sessions were ended.");
      }
      loadPending(); load();
    } catch (ex) { setErr(ex.detail || "Could not update the device"); }
    finally { setBusy(0); }
  };

  return (
    <div>
      <PageHeader title="Device Management" crumbs={["Settings", "Devices"]} />
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      {msg && <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg p-3">{msg}</div>}

      <div className="card overflow-hidden mb-5">
        <div className="px-5 py-3 border-b border-border">
          <div className="font-bold text-base">Waiting for approval</div>
          <div className="text-xs text-gray-400">Sign-ins from new devices. Check the code with the person before approving.</div>
        </div>
        {pending === null ? <Spinner /> : pending.length === 0 ? <Empty text="No devices are waiting for approval." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr><th className="th">Person</th><th className="th">Device code</th><th className="th">Browser</th><th className="th">Requested</th><th className="th"></th></tr></thead>
              <tbody>
                {pending.map((d) => (
                  <tr key={d.id} className="border-t border-border">
                    <td className="td font-semibold">{d.user_name}<div className="text-xs text-gray-400 font-normal">{d.user_email}</div></td>
                    <td className="td font-mono">{d.code}</td>
                    <td className="td max-w-[260px] truncate" title={d.device_name}>{d.device_name || "—"}</td>
                    <td className="td whitespace-nowrap">{fmtTime(d.last_seen_at || d.registered_at)}</td>
                    <td className="td text-right whitespace-nowrap">
                      <button className="btn-primary !py-1 !px-2.5 text-xs" disabled={busy === d.id} onClick={() => act(d.user_id, d.id, "approve")}>Approve</button>
                      <button className="btn-ghost !py-1 !px-2.5 text-xs text-red-600 ml-1" disabled={busy === d.id} onClick={() => act(d.user_id, d.id, "revoke")}>Refuse</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card p-3 mb-5 flex flex-wrap gap-2 items-end">
        <div className="min-w-[240px] flex-1">
          <label className="label">Staff member</label>
          <select className="input" value={userId} onChange={(e) => setUserId(e.target.value)}>
            <option value="">Select a staff member…</option>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </div>

      {!userId ? (
        <div className="card"><Empty text="Select a staff member to see their devices." /></div>
      ) : !data ? <Spinner /> : (data.items || []).length === 0 ? (
        <div className="card"><Empty text="No devices registered for this person." /></div>
      ) : (
        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b border-border font-bold text-base">Devices</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Device</th><th className="th">Code</th><th className="th">Status</th>
                <th className="th">Registered</th><th className="th">Last seen</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {data.items.map((d) => (
                  <tr key={d.id} className="border-t border-border">
                    <td className="td font-semibold max-w-[260px] truncate" title={d.device_name}>{d.device_name || `Device ${d.id}`}</td>
                    <td className="td font-mono">{d.code}</td>
                    <td className="td"><Badge value={d.active ? "active" : d.pending ? "pending" : "closed"}>{d.status}</Badge></td>
                    <td className="td whitespace-nowrap">{fmtTime(d.registered_at)}</td>
                    <td className="td whitespace-nowrap">{fmtTime(d.last_seen_at)}</td>
                    <td className="td text-right whitespace-nowrap">
                      {d.pending && (
                        <button className="btn-primary !py-1 !px-2.5 text-xs" disabled={busy === d.id}
                          onClick={() => act(userId, d.id, "approve")}>Approve</button>
                      )}
                      {(d.active || d.pending) && (
                        <button className="btn-ghost !py-1 !px-2.5 text-xs text-red-600 ml-1" disabled={busy === d.id}
                          onClick={() => act(userId, d.id, "revoke")}>{busy === d.id ? "…" : "Revoke"}</button>
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
