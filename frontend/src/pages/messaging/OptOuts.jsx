// SMS opt-out register: STOP-list of phones suppressed from non-transactional SMS.
// Shows the verified sender ID + opt-out enforcement flag, and lets staff with
// messaging.manage add or remove opt-outs. Purely additive to the messaging module.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Badge, Empty, PageHeader, Spinner } from "../../components/ui";

const fmtDT = (d) => (d ? new Date(d).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }) : "—");

export default function OptOuts() {
  const [status, setStatus] = useState(null);
  const [list, setList] = useState(null);
  const [activeOnly, setActiveOnly] = useState(true);
  const [phone, setPhone] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const loadStatus = () => api("/api/v1/messaging/status").then(setStatus).catch(() => {});
  const loadList = () => {
    setList(null);
    api(`/api/v1/messaging/opt-outs?active_only=${activeOnly}`)
      .then(setList).catch((e) => { setMsg(e.detail || "Failed to load opt-outs."); setList({ items: [], count: 0 }); });
  };
  useEffect(loadStatus, []);
  useEffect(loadList, [activeOnly]);

  const add = async (e) => {
    e.preventDefault();
    if (!phone.trim()) return;
    setBusy(true); setMsg("");
    try {
      await api("/api/v1/messaging/opt-outs", { method: "POST", body: { phone: phone.trim(), source: "manual" } });
      setMsg(`${phone.trim()} added to the opt-out register.`);
      setPhone(""); loadList(); loadStatus();
    } catch (e2) { setMsg(e2.detail || "Could not add opt-out."); }
    finally { setBusy(false); }
  };

  const remove = async (p) => {
    if (!confirm(`Remove ${p} from the opt-out register? They will receive SMS again.`)) return;
    setBusy(true); setMsg("");
    try {
      await api(`/api/v1/messaging/opt-outs/${encodeURIComponent(p)}`, { method: "DELETE" });
      setMsg(`${p} removed from the opt-out register.`);
      loadList(); loadStatus();
    } catch (e2) { setMsg(e2.detail || "Could not remove opt-out."); }
    finally { setBusy(false); }
  };

  return (
    <div>
      <PageHeader title="SMS Opt-Outs" crumbs={["Administration", "Messaging", "Opt-Outs"]} />

      {msg && <div className="mb-3 text-sm text-teal bg-teal-50 rounded-lg p-3">{msg}</div>}

      {/* Sender configuration */}
      <div className="card p-4 mb-5">
        <h3 className="font-bold mb-3">Sender configuration</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
          <div>
            <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Sender ID</div>
            <div className="font-medium">{status?.sender_id || "Not configured"}</div>
          </div>
          <div>
            <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Provider</div>
            <div className="font-medium">{status?.provider || "Not configured"}</div>
          </div>
          <div>
            <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Opt-out enforcement</div>
            <div className="font-medium">
              {status == null ? "—" : status.opt_out_enforcement
                ? <span className="text-accent font-semibold">Enforced</span>
                : <span className="text-amber-600 font-semibold">Not enforced</span>}
            </div>
          </div>
          <div>
            <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Active opt-outs</div>
            <div className="font-medium">{status?.active_opt_out_count ?? "—"}</div>
          </div>
        </div>
        {status?.suppressible_events?.length > 0 && (
          <div className="mt-3 text-xs text-gray-500">
            Suppressed for opted-out numbers: {status.suppressible_events.map((s) => s.replace(/_/g, " ")).join(", ")}
          </div>
        )}
      </div>

      {/* Add opt-out */}
      <div className="card p-4 mb-5">
        <h3 className="font-bold mb-3">Add opt-out</h3>
        <form onSubmit={add} className="flex flex-wrap items-end gap-2">
          <div>
            <label className="label">Phone</label>
            <input className="input max-w-[220px]" placeholder="2547XXXXXXXX" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </div>
          <button className="btn-primary" disabled={busy || !phone.trim()}>Add to register</button>
        </form>
      </div>

      {/* Register */}
      <div className="card overflow-hidden">
        <div className="p-3 border-b border-border flex items-center gap-2">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" className="w-4 h-4 accent-emerald-500" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} />
            Active opt-outs only
          </label>
          {list && <span className="text-xs text-gray-400 ml-2">{list.count ?? list.items?.length ?? 0} total</span>}
        </div>
        {!list ? <Spinner /> : (list.items || []).length === 0 ? <Empty text="No opt-outs" /> : (
          <table className="w-full text-sm">
            <thead><tr>
              <th className="th">Phone</th><th className="th">Source</th><th className="th">Opted out</th>
              <th className="th">Active</th><th className="th text-right">Action</th>
            </tr></thead>
            <tbody>
              {list.items.map((o) => (
                <tr key={o.id} className="border-t border-border hover:bg-canvas/60">
                  <td className="td font-medium">{o.phone}</td>
                  <td className="td capitalize">{(o.source || "—").replace(/_/g, " ")}</td>
                  <td className="td">{fmtDT(o.opted_out_at)}</td>
                  <td className="td"><Badge value={o.active ? "active" : "inactive"} /></td>
                  <td className="td text-right">
                    {o.active && (
                      <button className="btn-ghost !py-1 !px-2 text-xs text-red-600" disabled={busy} onClick={() => remove(o.phone)}>Remove</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
