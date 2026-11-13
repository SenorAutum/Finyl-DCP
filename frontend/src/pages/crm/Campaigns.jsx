// WP17 — SMS campaigns to a segment. One person drafts, another approves; on
// approval it goes out (now or at the time set). Offers go only to clients who
// agreed to them and always end with "Reply STOP to opt out"; anyone who opted
// out is skipped.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Badge, Empty, KpiCard, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify } from "../../lib/api";
import { CrmTabs, ErrorNote, Field, exportCsv, num, useLoad } from "./common";

const ST = { draft: "pending", pending: "pending", approved: "approved", sending: "active", sent: "closed", rejected: "rejected", cancelled: "rejected" };
const ST_LABEL = { draft: "draft", pending: "waiting for approval", approved: "approved, waiting to send", sending: "sending", sent: "sent", rejected: "rejected", cancelled: "cancelled" };
const when = (v) => (v ? new Date(v.endsWith("Z") ? v : `${v}Z`).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }) : "");

function CampaignForm({ initial, segments, onClose, onSaved }) {
  const [f, setF] = useState(initial);
  const [err, setErr] = useState("");
  const [prev, setPrev] = useState(null);
  useEffect(() => {
    const t = setTimeout(() => api("/api/v1/crm-plus/campaigns/preview", { method: "POST", body: { message: f.message, purpose: f.purpose } }).then(setPrev).catch(() => {}), 300);
    return () => clearTimeout(t);
  }, [f.message, f.purpose]);
  const save = async () => {
    setErr("");
    try {
      const body = { name: f.name, segment_id: Number(f.segment_id), purpose: f.purpose, message: f.message, send_at: f.later && f.send_at ? new Date(f.send_at).toISOString() : null };
      const r = await api(f.id ? `/api/v1/crm-plus/campaigns/${f.id}` : "/api/v1/crm-plus/campaigns", { method: f.id ? "PUT" : "POST", body });
      notify("Draft saved", { tone: "success" }); onSaved(r.id);
    } catch (e) { setErr(e.message); }
  };
  return (
    <Modal title={f.id ? "Edit campaign" : "New SMS campaign"} onClose={onClose} wide>
      <ErrorNote text={err} />
      <div className="grid sm:grid-cols-3 gap-3">
        <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. December top-up offer" /></Field>
        <Field label="Send to (segment)"><select className="input" value={f.segment_id} onChange={(e) => setF({ ...f, segment_id: e.target.value })}>
          <option value="">Choose…</option>{segments.map((s) => <option key={s.id} value={s.id}>{s.name} ({num(s.count)})</option>)}</select></Field>
        <Field label="Kind of message" hint={f.purpose === "marketing" ? "Goes only to clients who agreed to offers; adds the opt-out line" : "Account or service news; still skips anyone who opted out"}>
          <select className="input" value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })}><option value="marketing">Offer / promotion</option><option value="service">Service notice</option></select></Field>
        <div className="sm:col-span-3"><Field label="Message" hint="You can use {{first_name}}, {{last_name}}, {{dcp_name}} and {{paybill}}">
          <textarea className="input font-mono text-sm" rows={4} maxLength={600} value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} /></Field></div>
      </div>
      {prev && <div className="mt-3 rounded-lg bg-canvas p-3 text-sm"><div className="text-xs text-gray-500 mb-1">What a client sees ({prev.length} characters, {prev.parts} SMS)</div>{prev.text}</div>}
      <label className="flex items-center gap-2 text-sm mt-3"><input type="checkbox" checked={!!f.later} onChange={(e) => setF({ ...f, later: e.target.checked })} /> Send at a set time after approval</label>
      {f.later && <div className="mt-2 max-w-xs"><input type="datetime-local" className="input" value={f.send_at || ""} onChange={(e) => setF({ ...f, send_at: e.target.value })} /></div>}
      <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={f.name.length < 3 || !f.segment_id || f.message.length < 5} onClick={save}>Save draft</button></div>
    </Modal>
  );
}

export default function Campaigns() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(`/api/v1/crm-plus/campaigns${status ? `?status=${status}` : ""}`);
  const segs = useLoad("/api/v1/crm-plus/segments");
  const [form, setForm] = useState(null);
  useEffect(() => {
    const s = params.get("segment");
    if (s && can("crm.campaigns")) setForm({ name: "", segment_id: s, purpose: "marketing", message: "Hi {{first_name}}, ", later: false });
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps
  const rows = data?.campaigns || [];
  return (
    <div>
      <PageHeader title="SMS campaigns" crumbs={["Engagement", "CRM", "Campaigns"]}
        actions={can("crm.campaigns") && <button className="btn-primary" onClick={() => setForm({ name: "", segment_id: "", purpose: "marketing", message: "Hi {{first_name}}, ", later: false })}>New campaign</button>} />
      <CrmTabs />
      <div className="card overflow-hidden">
        <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-border items-center">
          <select className="input !w-auto text-sm" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">All</option><option value="pending">Waiting for approval</option><option value="draft">Drafts</option><option value="approved">Approved</option><option value="sent">Sent</option><option value="rejected">Rejected</option></select>
        </div>
        <ErrorNote text={error} />
        {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No campaigns yet." /> : (
          <table className="min-w-full text-sm"><thead><tr><th className="th">Campaign</th><th className="th">To</th><th className="th">Status</th>
            <th className="th text-right">Sent</th><th className="th text-right">Skipped</th><th className="th text-right">Failed</th><th className="th">By</th></tr></thead>
            <tbody>{rows.map((c) => (
              <tr key={c.id} className="cursor-pointer hover:bg-canvas" onClick={() => nav(`/crm/campaigns/${c.id}`)}>
                <td className="td font-semibold">{c.name}<div className="text-xs font-normal text-gray-500">{c.purpose === "marketing" ? "Offer" : "Service notice"}{c.send_at && ` · for ${when(c.send_at)}`}</div></td>
                <td className="td">{c.segment}</td>
                <td className="td"><Badge value={ST[c.status]}>{ST_LABEL[c.status]}</Badge></td>
                <td className="td text-right tabnums">{c.status === "sent" ? num(c.sent) : ""}</td>
                <td className="td text-right tabnums">{c.status === "sent" ? num(c.skipped) : ""}</td>
                <td className="td text-right tabnums">{c.status === "sent" ? num(c.failed) : ""}</td>
                <td className="td text-xs">{c.created_by}{c.approved_by && <div className="text-gray-400">approved by {c.approved_by}</div>}</td></tr>
            ))}</tbody></table>
        )}
      </div>
      {form && <CampaignForm initial={form} segments={segs.data?.segments || []} onClose={() => setForm(null)} onSaved={(id) => { setForm(null); reload(); nav(`/crm/campaigns/${id}`); }} />}
    </div>
  );
}

export function CampaignDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const { data: c, error, reload } = useLoad(`/api/v1/crm-plus/campaigns/${id}`);
  const segs = useLoad("/api/v1/crm-plus/segments");
  const opts = useLoad("/api/v1/crm-plus/options");
  const [editing, setEditing] = useState(false);
  const [rejecting, setRejecting] = useState(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  if (error) return <ErrorNote text={error} />;
  if (!c) return <Spinner />;
  const me = opts.data?.me;
  const act = async (path, body, msg) => {
    setBusy(true);
    try { const r = await api(`/api/v1/crm-plus/campaigns/${id}/${path}`, { method: "POST", body: body || {} }); notify(msg(r), { tone: "success" }); setRejecting(null); reload(); }
    catch (e) { notify(e.message); } finally { setBusy(false); }
  };
  const list = (c.recipient_list || []).filter((r) => !filter || r.status === filter);
  return (
    <div>
      <PageHeader title={c.name} crumbs={["Engagement", "CRM", "Campaigns", c.name]}
        actions={<>
          <Link className="btn-ghost" to="/crm/campaigns">← All campaigns</Link>
          {can("crm.campaigns") && ["draft", "rejected"].includes(c.status) && <>
            <button className="btn-ghost" onClick={() => setEditing(true)}>Edit</button>
            <button className="btn-primary" disabled={busy} onClick={() => act("submit", null, (r) => `Sent for approval (${num(r.audience)} clients)`)}>Send for approval</button></>}
          {can("crm.campaigns_approve") && c.status === "pending" && (c.created_by_user_id === me
            ? <span className="text-sm text-gray-500 self-center">Someone else must approve this</span>
            : <>
              <button className="btn-ghost text-red-600" onClick={() => setRejecting("")}>Reject</button>
              <button className="btn-primary" disabled={busy} onClick={() => act("decide", { approve: true }, (r) => r.status === "sent" ? `Sent to ${num(r.sent)} clients` : "Approved; it will go out at the time set")}>
                {c.send_at && new Date(c.send_at.endsWith("Z") ? c.send_at : `${c.send_at}Z`) > new Date() ? "Approve" : "Approve and send"}</button></>)}
          {can("crm.campaigns") && ["draft", "pending", "approved", "rejected"].includes(c.status) &&
            <button className="btn-ghost" disabled={busy} onClick={() => act("cancel", null, () => "Cancelled")}>Cancel campaign</button>}
        </>} />
      <CrmTabs />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        <KpiCard label="Status" value={<Badge value={ST[c.status]}>{ST_LABEL[c.status]}</Badge>} sub={c.reject_reason ? `Reason: ${c.reject_reason}` : c.send_at ? `for ${when(c.send_at)}` : ""} />
        {c.status === "sent" ? <>
          <KpiCard label="Sent" value={num(c.sent)} sub={`finished ${when(c.finished_at)}`} tone="good" />
          <KpiCard label="Skipped" value={num(c.skipped)} sub="opted out, no consent or no phone" />
          <KpiCard label="Failed" value={num(c.failed)} tone={c.failed ? "warn" : "default"} /></> : <>
          <KpiCard label="In the segment now" value={num(c.audience)} sub={c.segment} />
          {c.purpose === "marketing" && <KpiCard label="Agreed to offers" value={num(c.audience_with_consent)} sub="only these get an offer" />}
          <KpiCard label="Drafted by" value={<span className="text-base">{c.created_by}</span>} sub={fmtDate(c.created_at)} /></>}
      </div>
      <div className="card p-5 mb-5">
        <div className="text-xs text-gray-500 mb-1">{c.purpose === "marketing" ? "Offer" : "Service notice"} · the message</div>
        <div className="font-mono text-sm whitespace-pre-line">{c.message}</div>
        {c.sample && <><div className="text-xs text-gray-500 mt-3 mb-1">As the first client will see it</div><div className="text-sm bg-canvas rounded-lg p-3">{c.sample}</div></>}
        {c.approved_by && <div className="text-xs text-gray-500 mt-3">Approved by {c.approved_by} on {when(c.approved_at)}</div>}
      </div>
      {c.recipient_list?.length > 0 && (
        <div className="card overflow-hidden">
          <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-border items-center">
            <span className="font-bold">Who it went to</span>
            <select className="input !w-auto text-sm" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter"><option value="">All</option><option value="sent">Sent</option><option value="skipped">Skipped</option><option value="failed">Failed</option></select>
            <div className="flex-1" />
            <button className="btn-ghost" onClick={() => exportCsv(`campaign-${c.id}.csv`, [["Client", "name"], ["Phone", "phone"], ["Status", "status"], ["Reason", "reason"]], list)}>Export CSV</button>
          </div>
          <table className="min-w-full text-sm"><thead><tr><th className="th">Client</th><th className="th">Phone</th><th className="th">Status</th><th className="th">Why</th></tr></thead>
            <tbody>{list.slice(0, 500).map((r) => (
              <tr key={r.borrower_id}><td className="td"><Link className="text-accent" to={`/clients/${r.borrower_id}`}>{r.name}</Link></td><td className="td font-mono text-xs">{r.phone}</td>
                <td className="td"><Badge value={r.status === "sent" ? "approved" : r.status === "failed" ? "overdue" : "pending"}>{r.status}</Badge></td><td className="td text-gray-500">{r.reason}</td></tr>))}</tbody></table>
        </div>
      )}
      {editing && <CampaignForm initial={{ ...c, later: !!c.send_at, send_at: c.send_at ? new Date(new Date(c.send_at.endsWith("Z") ? c.send_at : `${c.send_at}Z`).getTime() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "" }}
        segments={segs.data?.segments || []} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />}
      {rejecting != null && (
        <Modal title="Reject campaign" onClose={() => setRejecting(null)}>
          <Field label="Why (the person who drafted it will see this)"><input className="input" value={rejecting} onChange={(e) => setRejecting(e.target.value)} /></Field>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setRejecting(null)}>Back</button>
            <button className="btn-primary" disabled={busy || rejecting.trim().length < 5} onClick={() => act("decide", { approve: false, note: rejecting }, () => "Rejected")}>Reject</button></div>
        </Modal>
      )}
    </div>
  );
}
