// WP13 — Finyl's loans to DCPs: facilities (limit, monthly rate, term),
// drawdowns, the repayment schedule, repayments (through Finyl's paybill with
// the facility number as the account, or recorded by hand) and arrears.
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Empty, KpiCard, Modal, PageHeader, Spinner } from "../../components/ui";
import { api, fmtDate, notify, todayLocal } from "../../lib/api";
import { ErrorNote, Field, PlatformTabs, exportCsv, kes, kes2, useLoad, usePlatform } from "./common";

export default function Lending() {
  const { can } = usePlatform();
  const manage = can("platform.lending_manage");
  const { data, error, loading, reload } = useLoad("/api/v1/platform/facilities");
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const open = params.get("facility");
  const items = data?.items || [];
  const live = items.filter((f) => f.status !== "closed");
  const sum = (k) => live.reduce((a, f) => a + (f[k] || 0), 0);

  return (
    <div>
      <PageHeader title="Finyl's loans to DCPs" crumbs={["Finyl", "Loans to DCPs"]}
        actions={manage && <button className="btn-primary" onClick={() => setCreating(true)}>New facility</button>} />
      <PlatformTabs />
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            <KpiCard variant="hero" label="Principal owed to Finyl" value={kes(sum("principal_outstanding"))} sub={`${live.length} open facilities`} />
            <KpiCard label="Total limits" value={kes(sum("limit_amount"))} sub={`${kes(sum("available"))} still available`} />
            <KpiCard label="Interest received" value={kes(items.reduce((a, f) => a + f.interest_paid, 0))} tone="good" />
            <KpiCard label="Arrears" value={kes(sum("arrears"))} tone={sum("arrears") ? "bad" : "good"}
              sub={`${live.filter((f) => f.arrears > 0).length} facility(ies) late`} />
          </div>
          <div className="card overflow-hidden">
            <div className="flex items-center px-4 py-3 border-b border-border">
              <h3 className="font-bold flex-1">Facilities</h3>
              <button className="btn-ghost" disabled={!items.length} onClick={() => exportCsv("finyl-facilities.csv", [
                ["Facility", "number"], ["DCP", "tenant"], ["Limit", "limit_amount"], ["Rate % / month", "interest_rate_pct"],
                ["Method", "interest_method"], ["Term (months)", "term_months"], ["Drawn", "drawn"], ["Principal owed", "principal_outstanding"],
                ["Total owed", "total_outstanding"], ["Paid", "paid"], ["Arrears", "arrears"], ["Days late", "days_late"],
                ["Class", "class"], ["Status", "status"]], items)}>Export CSV</button>
            </div>
            {!items.length ? <Empty text="No loans to DCPs yet" /> : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead><tr><th className="th">Facility</th><th className="th">DCP</th><th className="th text-right">Limit</th>
                    <th className="th text-right">Rate</th><th className="th text-right">Drawn</th><th className="th text-right">Principal owed</th>
                    <th className="th">Next instalment</th><th className="th text-right">Arrears</th><th className="th">Class</th><th className="th">Status</th></tr></thead>
                  <tbody>
                    {items.map((f) => (
                      <tr key={f.id} className="hover:bg-canvas cursor-pointer" onClick={() => setParams({ facility: f.id })}>
                        <td className="td font-mono text-xs">{f.number}</td><td className="td font-semibold">{f.tenant}</td>
                        <td className="td text-right tabnums">{kes(f.limit_amount)}</td>
                        <td className="td text-right tabnums">{f.interest_rate_pct}% {f.interest_method === "flat" ? "flat" : "reducing"}</td>
                        <td className="td text-right tabnums">{kes(f.drawn)}</td><td className="td text-right tabnums">{kes(f.principal_outstanding)}</td>
                        <td className="td">{f.next_due ? `${fmtDate(f.next_due.due)} · ${kes(f.next_due.unpaid)}` : "—"}</td>
                        <td className={`td text-right tabnums ${f.arrears ? "text-red-600 font-semibold" : ""}`}>{kes(f.arrears)}{f.days_late ? ` (${f.days_late}d)` : ""}</td>
                        <td className="td capitalize">{f.class}</td>
                        <td className="td"><Badge value={f.status === "closed" ? "closed" : f.status === "suspended" ? "pending" : f.arrears ? "overdue" : "active"}>{f.status}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
      {creating && <NewFacility onClose={() => setCreating(false)} onDone={(f) => { setCreating(false); reload(); setParams({ facility: f.id }); }} />}
      {open && <FacilityModal id={open} manage={manage} onClose={() => { setParams({}); reload(); }} />}
    </div>
  );
}

function NewFacility({ onClose, onDone }) {
  const { data } = useLoad("/api/v1/platform/overview?days=30");
  const [f, setF] = useState({ tenant_id: "", limit_amount: "", interest_rate_pct: "3", interest_method: "reducing",
    term_months: "12", start_date: todayLocal(), purpose: "" });
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      const r = await api("/api/v1/platform/facilities", { method: "POST", body: { ...f, tenant_id: Number(f.tenant_id),
        limit_amount: Number(f.limit_amount), interest_rate_pct: Number(f.interest_rate_pct), term_months: Number(f.term_months) } });
      notify(`Facility ${r.number} created`, { tone: "success" });
      onDone(r);
    } catch (e) { setErr(e.message); }
  };
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal title="New loan facility for a DCP" onClose={onClose}>
      <div className="space-y-3">
        <ErrorNote text={err} />
        <Field label="DCP">
          <select className="input" value={f.tenant_id} onChange={set("tenant_id")}>
            <option value="">Choose…</option>
            {(data?.tenants || []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Limit (KES)"><input className="input" type="number" min="1" value={f.limit_amount} onChange={set("limit_amount")} /></Field>
          <Field label="Interest per month (%)"><input className="input" type="number" min="0" max="20" step="0.01" value={f.interest_rate_pct} onChange={set("interest_rate_pct")} /></Field>
          <Field label="Method" hint="Reducing: interest on what is still owed. Flat: on the amount drawn.">
            <select className="input" value={f.interest_method} onChange={set("interest_method")}><option value="reducing">Reducing balance</option><option value="flat">Flat</option></select>
          </Field>
          <Field label="Term of each drawdown (months)"><input className="input" type="number" min="1" max="120" value={f.term_months} onChange={set("term_months")} /></Field>
          <Field label="Start date"><input className="input" type="date" value={f.start_date} onChange={set("start_date")} /></Field>
        </div>
        <Field label="Purpose"><input className="input" value={f.purpose} onChange={set("purpose")} placeholder="e.g. on-lending working capital" /></Field>
        <p className="text-xs text-gray-500">The DCP repays through Finyl's paybill with the facility number (e.g. FAC-0001) as the account number; payments are applied automatically, interest first.</p>
        <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!f.tenant_id || !f.limit_amount} onClick={save}>Create facility</button></div>
      </div>
    </Modal>
  );
}

function FacilityModal({ id, manage, onClose }) {
  const { data: f, error, reload } = useLoad(`/api/v1/platform/facilities/${id}`);
  const [dd, setDd] = useState({ amount: "", disbursed_on: todayLocal(), reference: "" });
  const [pay, setPay] = useState({ amount: "", reference: "", paid_at: todayLocal() });
  const [err, setErr] = useState("");
  const act = async (fn, msg) => { setErr(""); try { await fn(); notify(msg, { tone: "success" }); await reload(); } catch (e) { setErr(e.message); } };
  const drawdown = () => act(async () => {
    await api(`/api/v1/platform/facilities/${id}/drawdowns`, { method: "POST", body: { amount: Number(dd.amount), disbursed_on: dd.disbursed_on, reference: dd.reference || null } });
    setDd({ amount: "", disbursed_on: todayLocal(), reference: "" });
  }, "Drawdown recorded");
  const repay = () => act(async () => {
    await api(`/api/v1/platform/facilities/${id}/payments`, { method: "POST", body: { amount: Number(pay.amount), reference: pay.reference, paid_at: `${pay.paid_at}T09:00:00`, channel: "manual" } });
    setPay({ amount: "", reference: "", paid_at: todayLocal() });
  }, "Repayment recorded");
  const setStatus = (status) => act(() => api(`/api/v1/platform/facilities/${id}`, { method: "PUT", body: { status } }), `Facility ${status}`);
  return (
    <Modal title={f ? `${f.number} — ${f.tenant}` : "Facility"} onClose={onClose} wide>
      <ErrorNote text={error || err} />
      {!f ? <Spinner /> : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <div><div className="label">Limit</div><b className="tabnums">{kes(f.limit_amount)}</b></div>
            <div><div className="label">Available</div><b className="tabnums">{kes(f.available)}</b></div>
            <div><div className="label">Principal owed</div><b className="tabnums">{kes2(f.principal_outstanding)}</b></div>
            <div><div className="label">Total still due</div><b className="tabnums">{kes2(f.total_outstanding)}</b></div>
            <div><div className="label">Rate</div>{f.interest_rate_pct}% a month, {f.interest_method}</div>
            <div><div className="label">Term</div>{f.term_months} months per drawdown</div>
            <div><div className="label">Arrears</div><span className={f.arrears ? "text-red-600 font-bold" : ""}>{kes2(f.arrears)}{f.days_late ? ` · ${f.days_late} days` : ""}</span></div>
            <div><div className="label">Status</div><span className="capitalize">{f.status} · {f.class}</span></div>
          </div>
          {f.purpose && <div className="text-sm text-gray-500">Purpose: {f.purpose}</div>}

          {manage && f.status === "active" && (
            <div className="grid sm:grid-cols-4 gap-2 items-end border-t border-border pt-3">
              <Field label="Drawdown (KES)"><input className="input" type="number" min="1" value={dd.amount} onChange={(e) => setDd({ ...dd, amount: e.target.value })} /></Field>
              <Field label="Paid out on"><input className="input" type="date" value={dd.disbursed_on} onChange={(e) => setDd({ ...dd, disbursed_on: e.target.value })} /></Field>
              <Field label="Bank / M-Pesa ref"><input className="input" value={dd.reference} onChange={(e) => setDd({ ...dd, reference: e.target.value })} /></Field>
              <button className="btn-primary" disabled={!dd.amount} onClick={drawdown}>Record drawdown</button>
            </div>
          )}
          {manage && f.status !== "closed" && f.drawn > 0 && (
            <div className="grid sm:grid-cols-4 gap-2 items-end border-t border-border pt-3">
              <Field label="Repayment (KES)"><input className="input" type="number" min="1" value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} /></Field>
              <Field label="Reference"><input className="input" value={pay.reference} onChange={(e) => setPay({ ...pay, reference: e.target.value })} /></Field>
              <Field label="Paid on"><input className="input" type="date" value={pay.paid_at} onChange={(e) => setPay({ ...pay, paid_at: e.target.value })} /></Field>
              <button className="btn-primary" disabled={!pay.amount || !pay.reference} onClick={repay}>Record repayment</button>
            </div>
          )}

          <div>
            <div className="label">Repayment schedule</div>
            {!f.schedule.length ? <div className="text-sm text-gray-400">Nothing drawn yet.</div> : (
              <div className="max-h-64 overflow-y-auto border border-border rounded-lg">
                <table className="min-w-full text-sm">
                  <thead><tr><th className="th">Due</th><th className="th text-right">Principal</th><th className="th text-right">Interest</th><th className="th text-right">Paid</th><th className="th text-right">Unpaid</th></tr></thead>
                  <tbody>
                    {f.schedule.map((r, i) => {
                      const late = r.unpaid > 0 && r.due < todayLocal();
                      return (
                        <tr key={i} className={late ? "bg-red-50" : ""}>
                          <td className="td">{fmtDate(r.due)}</td><td className="td text-right tabnums">{kes2(r.principal)}</td>
                          <td className="td text-right tabnums">{kes2(r.interest)}</td><td className="td text-right tabnums">{kes2(r.paid)}</td>
                          <td className={`td text-right tabnums ${late ? "text-red-600 font-semibold" : ""}`}>{kes2(r.unpaid)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <div className="grid sm:grid-cols-2 gap-4">
            <div><div className="label">Drawdowns</div>
              {!f.drawdowns.length ? <div className="text-sm text-gray-400">None</div> :
                <ul className="text-sm space-y-1">{f.drawdowns.map((d) => <li key={d.id} className="tabnums">{fmtDate(d.disbursed_on)} · {kes2(d.amount)}{d.reference ? ` · ${d.reference}` : ""}</li>)}</ul>}
            </div>
            <div><div className="label">Repayments</div>
              {!f.payments.length ? <div className="text-sm text-gray-400">None</div> :
                <ul className="text-sm space-y-1">{f.payments.map((p) => <li key={p.reference} className="tabnums">{fmtDate(p.paid_at)} · {kes2(p.amount)} <span className="text-gray-400">(principal {kes(p.principal)}, interest {kes(p.interest)})</span> · <span className="font-mono text-xs">{p.reference}</span></li>)}</ul>}
            </div>
          </div>
          {manage && f.status !== "closed" && (
            <div className="flex gap-2 border-t border-border pt-3">
              {f.status === "active" && <button className="btn-ghost" onClick={() => setStatus("suspended")}>Suspend new drawdowns</button>}
              {f.status === "suspended" && <button className="btn-ghost" onClick={() => setStatus("active")}>Allow drawdowns again</button>}
              {f.principal_outstanding <= 0 && <button className="btn-ghost" onClick={() => setStatus("closed")}>Close facility</button>}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
