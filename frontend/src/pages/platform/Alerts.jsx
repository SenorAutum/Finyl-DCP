// WP13 — alerts when something fails anywhere on Finyl, and who is told
// (email for every severity they choose; SMS for critical ones).
import { useState } from "react";
import { Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { api, notify } from "../../lib/api";
import { ErrorNote, Field, PlatformTabs, Severity, Tabs, useLoad, usePlatform } from "./common";

export default function Alerts() {
  const { can } = usePlatform();
  const manage = can("platform.alerts_manage");
  const [tab, setTab] = useState("alerts");
  return (
    <div>
      <PageHeader title="Alerts" crumbs={["Finyl", "Alerts"]} />
      <PlatformTabs />
      {manage && <Tabs tabs={[["alerts", "Alerts"], ["recipients", "Who is told"]]} value={tab} onChange={setTab} />}
      {tab === "alerts" ? <AlertList manage={manage} /> : <Recipients />}
    </div>
  );
}

function AlertList({ manage }) {
  const [status, setStatus] = useState("open");
  const [kind, setKind] = useState("");
  const { data, error, loading, reload } = useLoad(`/api/v1/platform/alerts?status=${status}`);
  const [openId, setOpenId] = useState(null);
  const [note, setNote] = useState("");
  const items = (data?.items || []).filter((a) => !kind || a.kind === kind);
  const act = async (a, what) => {
    try {
      await api(`/api/v1/platform/alerts/${a.id}/${what}`, { method: "POST", body: what === "resolve" ? { note } : undefined });
      setNote(""); setOpenId(null); reload();
    } catch (e) { notify(e.message); }
  };
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-border">
        <select className="input !w-auto text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="open">Open</option><option value="acknowledged">Being handled</option><option value="resolved">Resolved</option><option value="all">All</option>
        </select>
        <select className="input !w-auto text-sm" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Every kind</option>
          {Object.entries(data?.kinds || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !items.length ? <Empty text={status === "open" ? "No open alerts. All quiet." : "Nothing here"} /> : (
        <div className="divide-y divide-border">
          {items.map((a) => (
            <div key={a.id} className="px-4 py-3">
              <div className="flex flex-wrap items-start gap-3">
                <Severity value={a.severity} />
                <div className="flex-1 min-w-[220px]">
                  <div className="font-semibold text-sm">{a.title}</div>
                  <div className="text-xs text-gray-500">{a.tenant} · {a.kind_label} · seen {a.count}× · last {new Date(a.last_seen).toLocaleString("en-KE")}
                    {a.notified ? " · people told" : ""}{a.status !== "open" ? ` · ${a.status === "acknowledged" ? "being handled" : "resolved"}` : ""}</div>
                </div>
                <button className="text-xs underline" onClick={() => setOpenId(openId === a.id ? null : a.id)}>{openId === a.id ? "Hide" : "Details"}</button>
                {manage && a.status === "open" && <button className="btn-ghost !py-1 text-xs" onClick={() => act(a, "acknowledge")}>I'm on it</button>}
              </div>
              {openId === a.id && (
                <div className="mt-2 ml-1">
                  {a.detail && <pre className="text-xs bg-canvas border border-border rounded-lg p-2 whitespace-pre-wrap">{a.detail}</pre>}
                  {a.resolved_note && <div className="text-xs text-gray-500 mt-1">Resolution: {a.resolved_note}</div>}
                  {manage && a.status !== "resolved" && (
                    <div className="flex gap-2 mt-2">
                      <input className="input text-sm" placeholder="What was done (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
                      <button className="btn-primary !py-1" onClick={() => act(a, "resolve")}>Resolve</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Recipients() {
  const { data, error, loading, reload } = useLoad("/api/v1/platform/alert-recipients");
  const tenants = useLoad("/api/v1/platform/overview?days=7");
  const [edit, setEdit] = useState(null);
  const names = Object.fromEntries((tenants.data?.tenants || []).map((t) => [t.id, t.name]));
  const test = async (r) => {
    try { const res = await api(`/api/v1/platform/alert-recipients/${r.id}/test`, { method: "POST" });
      notify(res.sent ? `Test email sent to ${r.email}` : "The email server did not accept the message", { tone: res.sent ? "success" : "error" }); }
    catch (e) { notify(e.message); }
  };
  const remove = async (r) => {
    if (!window.confirm(`Stop sending alerts to ${r.name}?`)) return;
    await api(`/api/v1/platform/alert-recipients/${r.id}`, { method: "DELETE" }).catch((e) => notify(e.message));
    reload();
  };
  return (
    <div className="card overflow-hidden">
      <div className="flex items-center px-4 py-3 border-b border-border">
        <p className="text-sm text-gray-500 flex-1">Everyone here is emailed new alerts that match their filters (and a summary every Monday if chosen). A phone number also gets an SMS for critical alerts.</p>
        <button className="btn-primary" onClick={() => setEdit({})}>Add person</button>
      </div>
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !data?.items.length ? <Empty text="Nobody is told yet" /> : (
        <table className="min-w-full text-sm">
          <thead><tr><th className="th">Name</th><th className="th">Email / phone</th><th className="th">From severity</th><th className="th">Kinds</th><th className="th">DCPs</th><th className="th">Weekly</th><th className="th" /></tr></thead>
          <tbody>
            {data.items.map((r) => (
              <tr key={r.id} className={r.active ? "" : "opacity-50"}>
                <td className="td font-semibold">{r.name}</td>
                <td className="td">{[r.email, r.phone].filter(Boolean).join(" · ")}</td>
                <td className="td"><Severity value={r.min_severity} /></td>
                <td className="td whitespace-normal text-xs">{r.kinds.length ? r.kinds.map((k) => data.kinds[k] || k).join(", ") : "All"}</td>
                <td className="td whitespace-normal text-xs">{r.tenant_ids.length ? r.tenant_ids.map((t) => names[t] || t).join(", ") : "All"}</td>
                <td className="td">{r.weekly_summary ? "Yes" : "No"}</td>
                <td className="td text-right whitespace-nowrap">
                  {r.email && <button className="btn-ghost !py-1 text-xs" onClick={() => test(r)}>Send test</button>}
                  <button className="btn-ghost !py-1 text-xs" onClick={() => setEdit(r)}>Edit</button>
                  <button className="btn-ghost !py-1 text-xs text-red-600" onClick={() => remove(r)}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {edit && <RecipientModal r={edit} kinds={data?.kinds || {}} tenants={tenants.data?.tenants || []}
        onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
    </div>
  );
}

function RecipientModal({ r, kinds, tenants, onClose, onSaved }) {
  const [f, setF] = useState({ name: r.name || "", email: r.email || "", phone: r.phone || "", min_severity: r.min_severity || "warning",
    kinds: r.kinds || [], tenant_ids: r.tenant_ids || [], weekly_summary: r.weekly_summary ?? true, active: r.active ?? true });
  const [err, setErr] = useState("");
  const toggle = (k, v) => setF({ ...f, [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v] });
  const save = async () => {
    setErr("");
    try {
      if (r.id) await api(`/api/v1/platform/alert-recipients/${r.id}`, { method: "PUT", body: f });
      else await api("/api/v1/platform/alert-recipients", { method: "POST", body: f });
      notify("Saved", { tone: "success" });
      onSaved();
    } catch (e) { setErr(e.message); }
  };
  return (
    <Modal title={r.id ? `Edit ${r.name}` : "Add a person to the alert list"} onClose={onClose} wide>
      <div className="space-y-3">
        <ErrorNote text={err} />
        <div className="grid sm:grid-cols-3 gap-3">
          <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Email"><input className="input" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Phone (SMS for critical)"><input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} placeholder="2547…" /></Field>
        </div>
        <Field label="Tell them about">
          <select className="input !w-auto" value={f.min_severity} onChange={(e) => setF({ ...f, min_severity: e.target.value })}>
            <option value="info">Everything (info and above)</option><option value="warning">Warnings and critical</option><option value="critical">Critical only</option>
          </select>
        </Field>
        <div>
          <div className="label">Kinds of alert (none ticked = all)</div>
          <div className="grid sm:grid-cols-2 gap-1">
            {Object.entries(kinds).map(([k, l]) => (
              <label key={k} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.kinds.includes(k)} onChange={() => toggle("kinds", k)} />{l}</label>
            ))}
          </div>
        </div>
        <div>
          <div className="label">DCPs (none ticked = all, including platform-wide alerts)</div>
          <div className="grid sm:grid-cols-3 gap-1">
            {tenants.map((t) => (
              <label key={t.id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.tenant_ids.includes(t.id)} onChange={() => toggle("tenant_ids", t.id)} />{t.name}</label>
            ))}
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.weekly_summary} onChange={(e) => setF({ ...f, weekly_summary: e.target.checked })} />Monday summary email</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} />Active</label>
        <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={save}>Save</button></div>
      </div>
    </Modal>
  );
}
