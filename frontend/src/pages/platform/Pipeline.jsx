// WP13 — onboarded DCPs and potential ones (Finyl's sales pipeline).
import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, KpiCard, Modal, PageHeader, Spinner } from "../../components/ui";
import { api, fmtDate, notify } from "../../lib/api";
import { ErrorNote, Field, PlatformTabs, exportCsv, kes, useLoad, usePlatform } from "./common";

const COLS = ["lead", "contacted", "demo", "proposal", "signed"];

export default function Pipeline() {
  const { can } = usePlatform();
  const manage = can("platform.pipeline_manage");
  const { data, error, loading, reload } = useLoad("/api/v1/platform/leads");
  const [open, setOpen] = useState(null);       // lead object or "new"
  const [showClosed, setShowClosed] = useState(false);
  const items = data?.items || [];
  const stage = Object.fromEntries((data?.stages || []).map((s) => [s.key, s]));
  const openLeads = items.filter((l) => !["live", "lost"].includes(l.stage));

  return (
    <div>
      <PageHeader title="Pipeline" crumbs={["Finyl", "Pipeline"]}
        actions={<div className="flex gap-2">
          <button className="btn-ghost" disabled={!items.length} onClick={() => exportCsv("finyl-pipeline.csv", [
            ["Company", "company_name"], ["Stage", "stage_label"], ["Contact", "contact_name"], ["Phone", "contact_phone"],
            ["Email", "contact_email"], ["Expected loan book", "expected_loan_book"], ["Branches", "expected_branches"],
            ["Source", "source"], ["Next step", "next_step"], ["Next step date", "next_step_date"], ["Owner", "owner"],
            ["Lost reason", "lost_reason"], ["DCP on Finyl", "tenant"]], items)}>Export CSV</button>
          {manage && <button className="btn-primary" onClick={() => setOpen("new")}>Add potential DCP</button>}
        </div>} />
      <PlatformTabs />
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            <KpiCard variant="hero" label="Onboarded DCPs" value={data.onboarded.length} sub={`${data.onboarded.filter((t) => t.active).length} live`} />
            <KpiCard label="Potential DCPs" value={openLeads.length} sub={`${openLeads.filter((l) => l.overdue).length} with an overdue next step`} />
            <KpiCard label="Potential monthly subscription" value={kes(data.potential_monthly_subscription)} sub="If every open lead signs" tone="good" />
            <KpiCard label="Won / lost" value={`${stage.live?.count || 0} / ${stage.lost?.count || 0}`} />
          </div>
          <div className="grid md:grid-cols-5 gap-3 mb-5">
            {COLS.map((k) => (
              <div key={k} className="bg-canvas rounded-xl border border-border p-2 min-h-[120px]">
                <div className="flex items-baseline justify-between px-1 mb-2">
                  <span className="text-xs font-bold uppercase tracking-wide text-gray-500">{stage[k]?.label}</span>
                  <span className="text-xs text-gray-400">{stage[k]?.count || 0}</span>
                </div>
                {items.filter((l) => l.stage === k).map((l) => (
                  <button key={l.id} onClick={() => setOpen(l)} className="w-full text-left card p-2.5 mb-2 hover:shadow-card-hover">
                    <div className="font-semibold text-sm">{l.company_name}</div>
                    {l.expected_loan_book != null && <div className="text-xs text-gray-500 tabnums">Book {kes(l.expected_loan_book)}</div>}
                    {l.next_step && <div className={`text-xs mt-1 ${l.overdue ? "text-red-600 font-semibold" : "text-gray-500"}`}>{l.next_step}{l.next_step_date ? ` · ${fmtDate(l.next_step_date)}` : ""}</div>}
                    {l.owner && <div className="text-[11px] text-gray-400 mt-0.5">{l.owner}</div>}
                  </button>
                ))}
              </div>
            ))}
          </div>
          <div className="grid lg:grid-cols-2 gap-5">
            <div className="card overflow-hidden">
              <h3 className="font-bold px-4 pt-4 pb-2">Onboarded DCPs</h3>
              {!data.onboarded.length ? <Empty text="None yet" /> : (
                <table className="min-w-full text-sm"><tbody>
                  {data.onboarded.map((t) => (
                    <tr key={t.id}><td className="td font-semibold">{can("platform.dashboard") ? <Link className="hover:underline" to={`/platform/dcp/${t.id}`}>{t.name}</Link> : t.name}</td>
                      <td className="td">{t.active ? "Live" : "Switched off"}</td><td className="td text-gray-500">since {fmtDate(t.since)}</td></tr>
                  ))}
                </tbody></table>
              )}
            </div>
            <div className="card overflow-hidden">
              <div className="flex items-center px-4 pt-4 pb-2"><h3 className="font-bold flex-1">Won and lost</h3>
                <button className="text-xs underline" onClick={() => setShowClosed(!showClosed)}>{showClosed ? "Hide" : "Show"}</button></div>
              {showClosed && (
                <table className="min-w-full text-sm"><tbody>
                  {items.filter((l) => ["live", "lost"].includes(l.stage)).map((l) => (
                    <tr key={l.id} className="hover:bg-canvas cursor-pointer" onClick={() => setOpen(l)}>
                      <td className="td font-semibold">{l.company_name}</td><td className="td">{l.stage_label}</td>
                      <td className="td text-gray-500 whitespace-normal">{l.stage === "lost" ? l.lost_reason : l.tenant}</td></tr>
                  ))}
                </tbody></table>
              )}
            </div>
          </div>
        </>
      )}
      {open && <LeadModal lead={open === "new" ? null : open} stages={data?.stages || []} tenants={data?.onboarded || []}
        manage={manage} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); reload(); }} />}
    </div>
  );
}

function LeadModal({ lead, stages, tenants, manage, onClose, onSaved }) {
  const [f, setF] = useState(() => ({
    company_name: lead?.company_name || "", contact_name: lead?.contact_name || "", contact_phone: lead?.contact_phone || "",
    contact_email: lead?.contact_email || "", expected_loan_book: lead?.expected_loan_book ?? "", expected_branches: lead?.expected_branches ?? "",
    source: lead?.source || "", next_step: lead?.next_step || "", next_step_date: lead?.next_step_date || "",
    tenant_id: lead?.tenant_id ?? "", notes: lead?.notes || "" }));
  const [move, setMove] = useState({ stage: "", note: "", lost_reason: "" });
  const [err, setErr] = useState("");
  const history = useLoad(lead ? `/api/v1/platform/leads/${lead.id}/history` : null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const body = () => ({ ...f, expected_loan_book: f.expected_loan_book === "" ? null : Number(f.expected_loan_book),
    expected_branches: f.expected_branches === "" ? null : Number(f.expected_branches),
    next_step_date: f.next_step_date || null, tenant_id: f.tenant_id === "" ? null : Number(f.tenant_id) });
  const save = async () => {
    setErr("");
    try {
      if (lead) await api(`/api/v1/platform/leads/${lead.id}`, { method: "PUT", body: body() });
      else await api("/api/v1/platform/leads", { method: "POST", body: body() });
      notify("Saved", { tone: "success" });
      onSaved();
    } catch (e) { setErr(e.message); }
  };
  const doMove = async () => {
    setErr("");
    try {
      await api(`/api/v1/platform/leads/${lead.id}`, { method: "PUT", body: body() });
      await api(`/api/v1/platform/leads/${lead.id}/stage`, { method: "POST", body: move });
      notify("Moved", { tone: "success" });
      onSaved();
    } catch (e) { setErr(e.message); }
  };
  const ro = !manage;
  return (
    <Modal title={lead ? lead.company_name : "Add a potential DCP"} onClose={onClose} wide>
      <div className="space-y-3">
        <ErrorNote text={err} />
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Company"><input className="input" disabled={ro} value={f.company_name} onChange={set("company_name")} /></Field>
          <Field label="Source" hint="Referral, event, cold call…"><input className="input" disabled={ro} value={f.source} onChange={set("source")} /></Field>
          <Field label="Contact person"><input className="input" disabled={ro} value={f.contact_name} onChange={set("contact_name")} /></Field>
          <Field label="Phone"><input className="input" disabled={ro} value={f.contact_phone} onChange={set("contact_phone")} /></Field>
          <Field label="Email"><input className="input" disabled={ro} type="email" value={f.contact_email} onChange={set("contact_email")} /></Field>
          <Field label="Expected loan book (KES)"><input className="input" disabled={ro} type="number" min="0" value={f.expected_loan_book} onChange={set("expected_loan_book")} /></Field>
          <Field label="Expected branches"><input className="input" disabled={ro} type="number" min="0" value={f.expected_branches} onChange={set("expected_branches")} /></Field>
          <Field label="DCP on Finyl" hint="Link it once their account is set up; needed to mark it Live.">
            <select className="input" disabled={ro} value={f.tenant_id} onChange={set("tenant_id")}>
              <option value="">Not yet on Finyl</option>{tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
          <Field label="Next step"><input className="input" disabled={ro} value={f.next_step} onChange={set("next_step")} placeholder="e.g. send proposal" /></Field>
          <Field label="Next step by"><input className="input" disabled={ro} type="date" value={f.next_step_date} onChange={set("next_step_date")} /></Field>
        </div>
        <Field label="Notes"><textarea className="input" disabled={ro} rows={2} value={f.notes} onChange={set("notes")} /></Field>
        {manage && <div className="flex justify-end"><button className="btn-primary" disabled={!f.company_name.trim()} onClick={save}>{lead ? "Save" : "Add"}</button></div>}
        {lead && manage && (
          <div className="border-t border-border pt-3 grid sm:grid-cols-4 gap-2 items-end">
            <Field label={`Move from "${lead.stage_label}" to`}>
              <select className="input" value={move.stage} onChange={(e) => setMove({ ...move, stage: e.target.value })}>
                <option value="">Choose…</option>{stages.filter((s) => s.key !== lead.stage).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
              </select>
            </Field>
            {move.stage === "lost"
              ? <Field label="Why lost"><input className="input" value={move.lost_reason} onChange={(e) => setMove({ ...move, lost_reason: e.target.value })} /></Field>
              : <Field label="Note"><input className="input" value={move.note} onChange={(e) => setMove({ ...move, note: e.target.value })} /></Field>}
            <button className="btn-primary" disabled={!move.stage} onClick={doMove}>Move</button>
          </div>
        )}
        {lead && history.data && (
          <div className="border-t border-border pt-3">
            <div className="label">History</div>
            <ul className="text-sm space-y-1">{history.data.items.map((h, i) => (
              <li key={i}><span className="text-gray-400">{fmtDate(h.at)}</span> · {h.from ? `${h.from} → ` : ""}<b>{h.to}</b>{h.note ? ` · ${h.note}` : ""}{h.by ? ` · ${h.by}` : ""}</li>
            ))}</ul>
          </div>
        )}
      </div>
    </Modal>
  );
}
