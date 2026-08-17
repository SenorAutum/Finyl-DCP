// Per-DCP SMS Messaging — customise the SMS wording sent at each loan-lifecycle
// event. super_admin picks a DCP (?tenant_id=); tenant admins edit their own DCP.
// Each event has an editable body (with {{placeholder}} tokens), an active toggle,
// clickable variable chips, a live preview and a real "send test" action.
import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

function Switch({ on, onChange, disabled }) {
  return (
    <button type="button" onClick={onChange} aria-pressed={on} disabled={disabled}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
        on ? "bg-accent" : "bg-gray-300"} ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}>
      <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
        on ? "translate-x-[18px]" : "translate-x-[3px]"}`} />
    </button>
  );
}

// Client-side render of a body against sample values (mirrors the server engine:
// unknown/blank tokens collapse to an empty string).
function renderBody(body, ctx) {
  return (body || "").replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_, k) =>
    ctx && ctx[k] != null ? String(ctx[k]) : "");
}

function EventCard({ ev, variables, sampleContext, onSave, onTest }) {
  const [body, setBody] = useState(ev.body);
  const [active, setActive] = useState(ev.active);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const taRef = useRef(null);

  // Re-sync when the selected DCP changes underneath us.
  useEffect(() => { setBody(ev.body); setActive(ev.active); setNote(""); setErr(""); },
    [ev.event_key, ev.body, ev.active]);

  const dirty = body !== ev.body || active !== ev.active;

  const insertToken = (token) => {
    const ta = taRef.current;
    const snippet = `{{${token}}}`;
    if (!ta) { setBody((b) => b + snippet); return; }
    const start = ta.selectionStart ?? body.length;
    const end = ta.selectionEnd ?? body.length;
    const next = body.slice(0, start) + snippet + body.slice(end);
    setBody(next);
    requestAnimationFrame(() => {
      ta.focus();
      const pos = start + snippet.length;
      ta.setSelectionRange(pos, pos);
    });
  };

  const save = async () => {
    setBusy(true); setErr(""); setNote("");
    try {
      await onSave(ev.event_key, { body, active });
      setNote("Saved.");
    } catch (e) { setErr(e.detail || "Save failed"); }
    finally { setBusy(false); }
  };

  const reset = () => { setBody(ev.default_body); setNote("Reverted to default wording (unsaved)."); setErr(""); };

  const test = async () => {
    const phone = window.prompt(
      "Send a REAL test SMS of this template to which phone number?\n(e.g. 2547XXXXXXXX). This dispatches a live SMS.");
    if (!phone) return;
    setBusy(true); setErr(""); setNote("");
    try {
      const r = await onTest(ev.event_key, { phone, body });
      setNote(`Test ${r.status} → ${phone}${r.error ? ` (${r.error})` : ""}`);
    } catch (e) { setErr(e.detail || "Send failed"); }
    finally { setBusy(false); }
  };

  const preview = renderBody(body, sampleContext);
  const len = preview.length;
  const segments = Math.max(1, Math.ceil(len / 160));

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className="font-semibold text-gray-800">{ev.label}</div>
          <div className="text-[11px] text-gray-400">
            {ev.source === "custom" ? "Custom" : "Default"}
            {ev.updated_at ? ` · updated ${new Date(ev.updated_at).toLocaleString()}` : ""}
          </div>
        </div>
        <label className="flex items-center gap-2 text-xs text-gray-600">
          {active ? "Active" : "Disabled"}
          <Switch on={active} disabled={busy} onChange={() => setActive((a) => !a)} />
        </label>
      </div>

      <textarea ref={taRef} className="input w-full h-24 font-mono text-sm" value={body}
        onChange={(e) => setBody(e.target.value)} disabled={busy}
        placeholder="SMS body — use {{placeholders}} below" />

      <div className="mt-2 flex flex-wrap gap-1">
        {variables.map((v) => (
          <button key={v.token} type="button" title={v.description} disabled={busy}
            onClick={() => insertToken(v.token)}
            className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 hover:bg-accent hover:text-white transition-colors">
            {`{{${v.token}}}`}
          </button>
        ))}
      </div>

      <div className="mt-3 rounded-lg bg-gray-50 p-2">
        <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Preview</div>
        <div className="text-sm text-gray-800 whitespace-pre-wrap">{preview || <span className="text-gray-400">—</span>}</div>
        <div className="text-[11px] text-gray-400 mt-1">{len} chars · {segments} SMS segment{segments > 1 ? "s" : ""}</div>
      </div>

      {err && <div className="mt-2 text-sm text-red-600 bg-red-50 rounded-lg p-2">{err}</div>}
      {note && <div className="mt-2 text-sm text-emerald-700 bg-emerald-50 rounded-lg p-2">{note}</div>}

      <div className="mt-3 flex items-center gap-2">
        <button className="btn-primary" onClick={save} disabled={busy || !dirty}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button className="btn-secondary" onClick={reset} disabled={busy}>Reset to default</button>
        <button className="btn-secondary" onClick={test} disabled={busy}>Send test</button>
      </div>
    </div>
  );
}

export default function Messaging() {
  const { user } = useAuth();
  const isSuper = user?.role === "super_admin";
  const [data, setData] = useState(null);
  const [tenantId, setTenantId] = useState("");
  const [err, setErr] = useState("");

  const qs = (tid) => (isSuper && tid ? `?tenant_id=${tid}` : "");

  const load = (tid) => {
    setErr(""); setData(null);
    api(`/api/v1/messaging/templates${qs(tid)}`).then(setData).catch((e) => setErr(e.detail));
  };
  // Load on mount. For super_admin this bootstraps the DCP picker (the server
  // defaults to the first tenant) and pre-selects it; tenant admins load their own.
  useEffect(() => { load(""); }, [isSuper]); // eslint-disable-line react-hooks/exhaustive-deps

  // Once the bootstrap response arrives, adopt its resolved tenant as the selection.
  useEffect(() => {
    if (isSuper && data && !tenantId && data.tenant_id) setTenantId(String(data.tenant_id));
  }, [isSuper, data, tenantId]);

  const pickTenant = (e) => { const tid = e.target.value; setTenantId(tid); if (tid) load(tid); else setData(null); };

  const onSave = async (event_key, payload) => {
    await api(`/api/v1/messaging/templates/${event_key}${qs(tenantId)}`, { method: "PUT", body: payload });
    load(tenantId);
  };
  const onTest = (event_key, payload) =>
    api(`/api/v1/messaging/templates/${event_key}/send-test${qs(tenantId)}`, { method: "POST", body: payload });

  return (
    <div>
      <PageHeader title="SMS Messaging" crumbs={["Administration", "SMS Messaging"]} />
      <p className="mb-4 text-sm text-gray-500 max-w-3xl">
        Customise the SMS wording sent at each loan-lifecycle event. Use the
        <span className="font-mono"> {"{{placeholder}}"} </span> chips to insert borrower/loan
        values; the preview shows sample output. Disabling an event stops that SMS from sending.
      </p>

      {isSuper && (
        <div className="mb-4 flex items-center gap-3">
          <label className="text-sm font-medium text-gray-700">DCP (Tenant)</label>
          <select className="input max-w-xs" value={tenantId} onChange={pickTenant}>
            <option value="">— Select a DCP —</option>
            {(data?.tenants || []).map((t) => (
              <option key={t.id} value={t.id}>{t.name} ({t.code}){t.active ? "" : " — inactive"}</option>
            ))}
          </select>
        </div>
      )}

      {err && <div className="mb-3 text-sm text-red-600 bg-red-50 rounded-lg p-3">{err}</div>}

      {isSuper && !tenantId && !err &&
        <div className="text-sm text-gray-400">Select a DCP above to customise its SMS templates.</div>}

      {!data && ((isSuper && tenantId) || !isSuper) && !err && <Spinner />}

      {data && (
        <div className="grid gap-4 md:grid-cols-2">
          {data.templates.map((ev) => (
            <EventCard key={ev.event_key} ev={ev} variables={data.variables}
              sampleContext={data.sample_context} onSave={onSave} onTest={onTest} />
          ))}
        </div>
      )}
    </div>
  );
}
