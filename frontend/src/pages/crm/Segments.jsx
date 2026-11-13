// WP17 — segments: saved groups of clients (e.g. "repaid before, no loan for 60
// days") used for SMS campaigns. The count updates as clients change.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, notify } from "../../lib/api";
import { CrmTabs, ErrorNote, Field, LOAN_STATE_LABEL, num, useCrmOptions, useLoad } from "./common";

export function describe(rules, branches = []) {
  const r = rules || {};
  const out = [];
  if (r.loan_state && r.loan_state !== "any") out.push(LOAN_STATE_LABEL[r.loan_state]);
  if (r.gender) out.push(r.gender === "female" ? "Women" : "Men");
  if (r.sector) out.push(`Business: ${r.sector}`);
  if (r.branch_ids?.length) out.push(`Branch: ${r.branch_ids.map((id) => branches.find((b) => b.id === id)?.name || id).join(", ")}`);
  if (r.min_repaid_loans) out.push(`Repaid at least ${r.min_repaid_loans} loan${r.min_repaid_loans > 1 ? "s" : ""}`);
  if (r.min_cycle) out.push(`On loan ${r.min_cycle} or later`);
  if (r.dormant_days != null) out.push(`Last paid over ${r.dormant_days} days ago`);
  if (r.joined_within_days != null) out.push(`Joined in the last ${r.joined_within_days} days`);
  if (r.marketing_consent) out.push("Agreed to offers by SMS");
  return out.length ? out.join(" · ") : "All active clients";
}

const BLANK = { name: "", description: "", rules: { loan_state: "any", gender: "", sector: "", branch_ids: [], min_repaid_loans: "", min_cycle: "",
  dormant_days: "", joined_within_days: "", marketing_consent: false } };

function toBody(rules) {
  const r = { ...rules };
  if (r.loan_state === "any") delete r.loan_state;
  ["min_repaid_loans", "min_cycle", "dormant_days", "joined_within_days"].forEach((k) => { if (r[k] === "" || r[k] == null) delete r[k]; else r[k] = Number(r[k]); });
  ["gender", "sector"].forEach((k) => { if (!r[k]) delete r[k]; });
  if (!r.branch_ids?.length) delete r.branch_ids;
  if (!r.marketing_consent) delete r.marketing_consent;
  return r;
}

export default function Segments() {
  const { can } = useAuth();
  const opts = useCrmOptions();
  const { data, error, loading, reload } = useLoad("/api/v1/crm-plus/segments");
  const [form, setForm] = useState(null);
  const [preview, setPreview] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!form) return undefined;
    const t = setTimeout(() => api("/api/v1/crm-plus/segments/preview", { method: "POST", body: { rules: toBody(form.rules) } })
      .then(setPreview).catch((e) => setPreview({ error: e.message })), 300);
    return () => clearTimeout(t);
  }, [form]);
  const setR = (k, v) => setForm({ ...form, rules: { ...form.rules, [k]: v } });
  const save = async () => {
    setErr("");
    try {
      const body = { name: form.name, description: form.description || null, rules: toBody(form.rules) };
      await api(form.id ? `/api/v1/crm-plus/segments/${form.id}` : "/api/v1/crm-plus/segments", { method: form.id ? "PUT" : "POST", body });
      notify("Segment saved", { tone: "success" }); setForm(null); reload();
    } catch (e) { setErr(e.message); }
  };
  const remove = async (s) => {
    try { await api(`/api/v1/crm-plus/segments/${s.id}`, { method: "DELETE" }); reload(); } catch (e) { notify(e.message); }
  };
  const rows = data?.segments || [];
  return (
    <div>
      <PageHeader title="Segments" crumbs={["Engagement", "CRM", "Segments"]}
        actions={can("crm.campaigns") && <button className="btn-primary" onClick={() => { setPreview(null); setForm(JSON.parse(JSON.stringify(BLANK))); }}>New segment</button>} />
      <CrmTabs />
      <div className="card overflow-hidden">
        <ErrorNote text={error} />
        {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No segments yet. A segment is a saved group of clients, for example everyone who repaid but has not borrowed for two months." /> : (
          <table className="min-w-full text-sm"><thead><tr><th className="th">Segment</th><th className="th">Who is in it</th><th className="th text-right">Clients now</th><th className="th" /></tr></thead>
            <tbody>{rows.map((s) => (
              <tr key={s.id}><td className="td font-semibold">{s.name}{s.description && <div className="text-xs font-normal text-gray-500">{s.description}</div>}</td>
                <td className="td whitespace-normal text-gray-600">{describe(s.rules, opts?.branches)}</td>
                <td className="td text-right tabnums font-semibold">{num(s.count)}</td>
                <td className="td whitespace-nowrap">
                  {can("crm.campaigns") && <Link className="text-xs font-semibold text-accent mr-2" to={`/crm/campaigns?segment=${s.id}`}>Send SMS</Link>}
                  {can("crm.campaigns") && <button className="text-xs text-gray-500 mr-2" onClick={() => { setPreview(null); setForm({ ...JSON.parse(JSON.stringify(BLANK)), id: s.id, name: s.name, description: s.description || "", rules: { ...BLANK.rules, ...s.rules } }); }}>Edit</button>}
                  {can("crm.campaigns") && <button className="text-xs text-gray-400" onClick={() => remove(s)}>Delete</button>}</td></tr>
            ))}</tbody></table>
        )}
      </div>
      {form && (
        <Modal title={form.id ? "Edit segment" : "New segment"} onClose={() => setForm(null)} wide>
          <ErrorNote text={err} />
          <div className="grid sm:grid-cols-3 gap-3">
            <Field label="Name"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Repaid, idle 60 days" /></Field>
            <div className="sm:col-span-2"><Field label="Description (optional)"><input className="input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field></div>
            <Field label="Loans"><select className="input" value={form.rules.loan_state} onChange={(e) => setR("loan_state", e.target.value)}>
              {Object.entries(LOAN_STATE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label="Gender"><select className="input" value={form.rules.gender} onChange={(e) => setR("gender", e.target.value)}>
              <option value="">Any</option><option value="female">Women</option><option value="male">Men</option></select></Field>
            <Field label="Business (contains)"><input className="input" value={form.rules.sector} onChange={(e) => setR("sector", e.target.value)} placeholder="e.g. retail" /></Field>
            <Field label="Repaid at least (loans)"><input type="number" min="0" className="input" value={form.rules.min_repaid_loans} onChange={(e) => setR("min_repaid_loans", e.target.value)} /></Field>
            <Field label="On loan number (at least)"><input type="number" min="0" className="input" value={form.rules.min_cycle} onChange={(e) => setR("min_cycle", e.target.value)} /></Field>
            <Field label="Last paid more than (days ago)" hint="No loan running, and their last payment was this long ago"><input type="number" min="0" className="input" value={form.rules.dormant_days} onChange={(e) => setR("dormant_days", e.target.value)} /></Field>
            <Field label="Joined in the last (days)"><input type="number" min="0" className="input" value={form.rules.joined_within_days} onChange={(e) => setR("joined_within_days", e.target.value)} /></Field>
            <div className="sm:col-span-2"><Field label="Branches (none ticked = all)"><div className="flex flex-wrap gap-3 text-sm">{(opts?.branches || []).map((b) => (
              <label key={b.id} className="flex items-center gap-1"><input type="checkbox" checked={form.rules.branch_ids.includes(b.id)}
                onChange={(e) => setR("branch_ids", e.target.checked ? [...form.rules.branch_ids, b.id] : form.rules.branch_ids.filter((x) => x !== b.id))} />{b.name}</label>))}</div></Field></div>
            <label className="flex items-center gap-2 text-sm sm:col-span-3"><input type="checkbox" checked={form.rules.marketing_consent} onChange={(e) => setR("marketing_consent", e.target.checked)} /> Only clients who agreed to receive offers by SMS</label>
          </div>
          <div className="mt-4 rounded-lg bg-canvas p-3 text-sm" data-testid="segment-preview">
            {!preview ? "Counting…" : preview.error ? <span className="text-red-600">{preview.error}</span> : <>
              <b>{num(preview.count)}</b> client{preview.count === 1 ? "" : "s"} match now.
              {preview.sample.length > 0 && <span className="text-gray-500"> For example: {preview.sample.slice(0, 5).map((c) => c.name).join(", ")}{preview.count > 5 ? "…" : ""}</span>}</>}
          </div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn-primary" disabled={form.name.length < 3} onClick={save}>Save</button></div>
        </Modal>
      )}
    </div>
  );
}
