// WP13 — Finyl's own billing: standard prices, each DCP's billing set-up,
// monthly invoices (printable), payments received, revenue by month, and
// payments to Finyl's paybill that named no invoice or facility.
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { api, fmtDate, notify, todayLocal } from "../../lib/api";
import { COLORS, ErrorNote, Field, PlatformTabs, Tabs, exportCsv, kes, kes2, lastMonth, useLoad, usePlatform } from "./common";

const KIND_LABEL = { onboarding: "Onboarding", subscription: "Subscription", branch: "New branches", sms: "SMS", other: "Other" };
const KIND_COLOR = { onboarding: "#0F766E", subscription: "#10B981", branch: "#F59E0B", sms: "#6366F1", other: "#9CA3AF" };

function statusBadge(i) {
  if (i.status === "void") return <Badge value="closed">cancelled</Badge>;
  if (i.status === "paid") return <Badge value="paid">paid</Badge>;
  if (i.overdue) return <Badge value="overdue">overdue</Badge>;
  return <Badge value="pending">{i.status === "part_paid" ? "part paid" : "unpaid"}</Badge>;
}

export default function Billing() {
  const { can } = usePlatform();
  const manage = can("platform.billing_manage");
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState("invoices");
  const open = params.get("invoice");
  const tabs = [["invoices", "Invoices"], ["revenue", "Revenue"], ["dcps", "DCP billing"], ["prices", "Standard prices"],
    ...(manage ? [["unmatched", "Unmatched payments"]] : [])];
  return (
    <div>
      <PageHeader title="Billing & revenue" crumbs={["Finyl", "Billing"]} />
      <PlatformTabs />
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === "invoices" && <Invoices manage={manage} onOpen={(id) => setParams({ invoice: id })} />}
      {tab === "revenue" && <Revenue />}
      {tab === "dcps" && <DcpBilling manage={manage} />}
      {tab === "prices" && <Prices manage={manage} />}
      {tab === "unmatched" && <Unmatched />}
      {open && <InvoiceModal id={open} manage={manage} onClose={() => setParams({})} />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
function Invoices({ manage, onOpen }) {
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(`/api/v1/platform/invoices${status ? `?status=${status}` : ""}`);
  const [gen, setGen] = useState(false);
  const items = data?.items || [];
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-border">
        <select className="input !w-auto text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All invoices</option><option value="unpaid">Unpaid</option><option value="paid">Paid</option><option value="void">Cancelled</option>
        </select>
        <div className="flex-1" />
        <button className="btn-ghost" disabled={!items.length} onClick={() => exportCsv("finyl-invoices.csv", [
          ["Number", "number"], ["DCP", "tenant"], ["Month", "period"], ["Issued", (i) => i.issued_at?.slice(0, 10)],
          ["Due", "due_date"], ["Total (KES)", "total"], ["Paid (KES)", "paid"], ["Balance (KES)", "balance"], ["Status", "status"],
          ["Loan book used (KES)", "outstanding_book"]], items)}>Export CSV</button>
        {manage && <button className="btn-primary" onClick={() => setGen(true)}>Produce invoices</button>}
      </div>
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !items.length ? <Empty text="No invoices yet. Switch billing on for a DCP under 'DCP billing', then produce invoices." /> : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead><tr><th className="th">Number</th><th className="th">DCP</th><th className="th">Month</th><th className="th">Due</th>
              <th className="th text-right">Total</th><th className="th text-right">Balance</th><th className="th">Status</th></tr></thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id} className="hover:bg-canvas cursor-pointer" onClick={() => onOpen(i.id)}>
                  <td className="td font-mono text-xs">{i.number}</td><td className="td font-semibold">{i.tenant}</td>
                  <td className="td">{i.period_label}</td><td className="td">{fmtDate(i.due_date)}</td>
                  <td className="td text-right tabnums">{kes(i.total)}</td><td className="td text-right tabnums">{kes(i.balance)}</td>
                  <td className="td">{statusBadge(i)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {gen && <GenerateModal onClose={() => setGen(false)} onDone={() => { setGen(false); reload(); }} />}
    </div>
  );
}

function GenerateModal({ onClose, onDone }) {
  const [period, setPeriod] = useState(lastMonth());
  const [tenantId, setTenantId] = useState("");
  const { data } = useLoad("/api/v1/platform/billing/dcps");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState(null);
  const active = (data?.items || []).filter((t) => t.billing_active);
  const go = async () => {
    setBusy(true); setErr("");
    try {
      const r = await api("/api/v1/platform/invoices/generate", { method: "POST",
        body: { period, tenant_id: tenantId ? Number(tenantId) : null } });
      setResult(r);
      if (r.made.length) notify(`${r.made.length} invoice(s) produced`, { tone: "success" });
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal title="Produce invoices" onClose={result ? onDone : onClose}>
      {result ? (
        <div className="space-y-3 text-sm">
          <p><b>{result.made.length}</b> invoice(s) produced.</p>
          {result.problems.length > 0 && (
            <div><div className="label">Not produced</div><ul className="list-disc pl-5 text-gray-600">{result.problems.map((p) => <li key={p}>{p}</li>)}</ul></div>
          )}
          <button className="btn-primary" onClick={onDone}>Done</button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-gray-500">Finyl produces these by itself on the invoice day each month. Use this for a month that was missed or after cancelling one.</p>
          <ErrorNote text={err} />
          <Field label="Month"><input type="month" className="input" value={period} onChange={(e) => setPeriod(e.target.value)} /></Field>
          <Field label="DCP" hint={`Leave on 'every DCP' to invoice all ${active.length} DCP(s) with billing on.`}>
            <select className="input" value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
              <option value="">Every DCP with billing on</option>
              {active.map((t) => <option key={t.tenant_id} value={t.tenant_id}>{t.name}</option>)}
            </select>
          </Field>
          <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn-primary" disabled={busy || !period} onClick={go}>{busy ? "Working…" : "Produce"}</button></div>
        </div>
      )}
    </Modal>
  );
}

function InvoiceModal({ id, manage, onClose }) {
  const { data: inv, error, reload } = useLoad(`/api/v1/platform/invoices/${id}`);
  const [pay, setPay] = useState({ amount: "", reference: "", paid_at: todayLocal(), note: "" });
  const [voidReason, setVoidReason] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const act = async (fn) => { setBusy(true); setErr(""); try { await fn(); await reload(); } catch (e) { setErr(e.message); } finally { setBusy(false); } };
  const record = () => act(async () => {
    await api(`/api/v1/platform/invoices/${id}/payments`, { method: "POST", body: {
      amount: Number(pay.amount), reference: pay.reference, paid_at: `${pay.paid_at}T09:00:00`, channel: "manual", note: pay.note || null } });
    setPay({ amount: "", reference: "", paid_at: todayLocal(), note: "" });
    notify("Payment recorded", { tone: "success" });
  });
  const cancel = () => act(async () => {
    await api(`/api/v1/platform/invoices/${id}/void`, { method: "POST", body: { reason: voidReason } });
    notify("Invoice cancelled", { tone: "success" });
  });
  const print = () => {
    const w = window.open("", "_blank");
    if (!w) return;
    w.document.write(printable(inv));
    w.document.close(); w.focus(); w.print();
  };
  return (
    <Modal title={inv ? `Invoice ${inv.number}` : "Invoice"} onClose={onClose} wide>
      <ErrorNote text={error || err} />
      {!inv ? <Spinner /> : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
            <div><span className="text-gray-500">DCP</span> <b>{inv.tenant}</b></div>
            <div><span className="text-gray-500">Month</span> {inv.period_label}</div>
            <div><span className="text-gray-500">Issued</span> {fmtDate(inv.issued_at)}</div>
            <div><span className="text-gray-500">Due</span> {fmtDate(inv.due_date)}</div>
            <div>{statusBadge(inv)}</div>
          </div>
          <table className="min-w-full text-sm">
            <thead><tr><th className="th">Item</th><th className="th text-right">Qty</th><th className="th text-right">Price</th><th className="th text-right">Amount</th></tr></thead>
            <tbody>
              {inv.lines.map((l, i) => (
                <tr key={i}><td className="td whitespace-normal">{l.description}</td><td className="td text-right tabnums">{l.quantity}</td>
                  <td className="td text-right tabnums">{kes2(l.unit_price)}</td><td className="td text-right tabnums">{kes2(l.amount)}</td></tr>
              ))}
              <tr><td className="td font-bold" colSpan={3}>Total</td><td className="td text-right font-bold tabnums">{kes2(inv.total)}</td></tr>
              <tr><td className="td" colSpan={3}>Paid</td><td className="td text-right tabnums">{kes2(inv.paid)}</td></tr>
              <tr><td className="td font-bold" colSpan={3}>Balance</td><td className="td text-right font-bold tabnums">{kes2(inv.balance)}</td></tr>
            </tbody>
          </table>
          <div className="text-sm rounded-lg bg-canvas border border-border p-3">
            {inv.pay_to.paybill
              ? <>Pay through M-Pesa: Paybill <b>{inv.pay_to.paybill}</b>, account <b className="font-mono">{inv.pay_to.account}</b>. Payments are matched automatically.</>
              : <>Finyl's paybill number is not set on this server yet (FINYL_PAYBILL). Record payments by hand below.</>}
            {inv.billing_email && <div className="text-gray-500 mt-1">Billing contact: {inv.billing_email}</div>}
          </div>
          {inv.payments.length > 0 && (
            <div><div className="label">Payments received</div>
              <ul className="text-sm space-y-1">{inv.payments.map((p) => <li key={p.reference} className="tabnums">{fmtDate(p.paid_at)} · {kes2(p.amount)} · <span className="font-mono">{p.reference}</span> · {p.channel.replace("_", " ")}</li>)}</ul>
            </div>
          )}
          <div className="flex gap-2"><button className="btn-ghost" onClick={print}>Print / save as PDF</button></div>
          {manage && inv.status !== "void" && inv.status !== "paid" && (
            <div className="border-t border-border pt-4 grid sm:grid-cols-4 gap-2 items-end">
              <Field label="Amount (KES)"><input className="input" type="number" min="1" value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} /></Field>
              <Field label="M-Pesa code / bank ref"><input className="input" value={pay.reference} onChange={(e) => setPay({ ...pay, reference: e.target.value })} /></Field>
              <Field label="Paid on"><input className="input" type="date" value={pay.paid_at} onChange={(e) => setPay({ ...pay, paid_at: e.target.value })} /></Field>
              <button className="btn-primary" disabled={busy || !pay.amount || !pay.reference} onClick={record}>Record payment</button>
            </div>
          )}
          {manage && inv.status !== "void" && inv.paid === 0 && (
            <div className="border-t border-border pt-4 flex flex-wrap gap-2 items-end">
              <Field label="Cancel this invoice — reason"><input className="input !w-72" value={voidReason} onChange={(e) => setVoidReason(e.target.value)} placeholder="e.g. wrong loan book figure" /></Field>
              <button className="btn-ghost text-red-600" disabled={busy || voidReason.trim().length < 5} onClick={cancel}>Cancel invoice</button>
              <span className="text-[11px] text-gray-400">The month can then be invoiced again.</span>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function printable(inv) {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const money = (n) => Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const rows = inv.lines.map((l) => `<tr><td>${esc(l.description)}</td><td class=r>${l.quantity}</td><td class=r>${money(l.unit_price)}</td><td class=r>${money(l.amount)}</td></tr>`).join("");
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(inv.number)}</title><style>
  body{font-family:system-ui,Segoe UI,Arial,sans-serif;color:#1f2937;margin:40px;font-size:13px}
  h1{font-size:22px;margin:0 0 4px}table{width:100%;border-collapse:collapse;margin-top:18px}
  th,td{border-bottom:1px solid #e5e7eb;padding:8px 6px;text-align:left}.r{text-align:right}
  .tot td{font-weight:700}.muted{color:#6b7280}.box{margin-top:18px;padding:10px;border:1px solid #e5e7eb;border-radius:8px}
  </style></head><body>
  <h1>Invoice ${esc(inv.number)}</h1>
  <div class=muted>Finyl digital credit platform</div>
  <p><b>Bill to:</b> ${esc(inv.tenant)}${inv.billing_email ? ` (${esc(inv.billing_email)})` : ""}<br>
  <b>Period:</b> ${esc(inv.period_label)} &nbsp; <b>Issued:</b> ${esc(inv.issued_at?.slice(0, 10))} &nbsp; <b>Due:</b> ${esc(inv.due_date)}</p>
  <table><thead><tr><th>Item</th><th class=r>Qty</th><th class=r>Price (KES)</th><th class=r>Amount (KES)</th></tr></thead>
  <tbody>${rows}<tr class=tot><td colspan=3>Total</td><td class=r>${money(inv.total)}</td></tr>
  <tr><td colspan=3>Paid</td><td class=r>${money(inv.paid)}</td></tr>
  <tr class=tot><td colspan=3>Balance due</td><td class=r>${money(inv.balance)}</td></tr></tbody></table>
  <div class=box>${inv.pay_to.paybill ? `Pay by M-Pesa: Paybill <b>${esc(inv.pay_to.paybill)}</b>, Account <b>${esc(inv.pay_to.account)}</b>.` : "Payment details: contact Finyl."}</div>
  </body></html>`;
}

// --------------------------------------------------------------------------- //
function Revenue() {
  const [months, setMonths] = useState(12);
  const { data, error, loading } = useLoad(`/api/v1/platform/revenue?months=${months}`);
  const rows = data?.months || [];
  const kinds = Object.keys(KIND_LABEL).filter((k) => rows.some((r) => r[k]));
  const total = rows.reduce((a, r) => a + r.total, 0);
  const received = rows.reduce((a, r) => a + (r.received || 0), 0);
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <h3 className="font-bold flex-1">Invoiced per month, by kind of charge</h3>
        <select className="input !w-auto text-sm" value={months} onChange={(e) => setMonths(Number(e.target.value))}>
          {[6, 12, 24].map((m) => <option key={m} value={m}>Last {m} months</option>)}
        </select>
        <button className="btn-ghost" disabled={!rows.length} onClick={() => exportCsv("finyl-revenue.csv",
          [["Month", "label"], ...Object.keys(KIND_LABEL).map((k) => [`${KIND_LABEL[k]} (KES)`, (r) => r[k] || 0]),
           ["Total invoiced (KES)", "total"], ["Received (KES)", (r) => r.received || 0]], rows)}>Export CSV</button>
      </div>
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No invoices in this period" /> : (
        <>
          <div className="text-sm text-gray-500 mb-2">Invoiced {kes(total)} · received {kes(received)}</div>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={rows} margin={{ left: 0, right: 8 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="label" tickLine={false} />
              <YAxis tickFormatter={(v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v)} tickLine={false} axisLine={false} width={48} />
              <Tooltip formatter={(v) => kes(v)} />
              <Legend />
              {kinds.map((k) => <Bar key={k} dataKey={k} name={KIND_LABEL[k]} stackId="a" fill={KIND_COLOR[k]} />)}
              <Bar dataKey="received" name="Received" fill={COLORS.received} fillOpacity={0.35} />
            </BarChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
function DcpBilling({ manage }) {
  const { data, error, loading, reload } = useLoad("/api/v1/platform/billing/dcps");
  const [edit, setEdit] = useState(null);
  const items = data?.items || [];
  return (
    <div className="card overflow-hidden">
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !items.length ? <Empty text="No DCPs" /> : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead><tr><th className="th">DCP</th><th className="th">Billing</th><th className="th text-right">Loan book now</th>
              <th className="th text-right">Subscription now</th><th className="th">Prices</th><th className="th text-right">Unpaid</th><th className="th" /></tr></thead>
            <tbody>
              {items.map((t) => {
                const e = t.effective;
                const sub = Math.max((t.book * e.subscription_rate_pct) / 100, e.subscription_minimum);
                const own = Object.values(t.own_prices).some((v) => v != null);
                return (
                  <tr key={t.tenant_id}>
                    <td className="td font-semibold">{t.name}</td>
                    <td className="td">{t.billing_active ? <Badge value="active">on since {fmtDate(t.billing_start)}</Badge> : <Badge value="pending">off</Badge>}</td>
                    <td className="td text-right tabnums">{kes(t.book)}</td>
                    <td className="td text-right tabnums">{kes(sub)}</td>
                    <td className="td">{own ? <span className="text-amber-700 font-semibold">own prices</span> : <span className="text-gray-400">standard</span>}</td>
                    <td className={`td text-right tabnums ${t.unpaid ? "font-semibold" : ""}`}>{kes(t.unpaid)}</td>
                    <td className="td text-right">{manage && <button className="btn-ghost !py-1 text-xs" onClick={() => setEdit(t)}>Edit</button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {edit && (
        <Modal title={`Billing — ${edit.name}`} onClose={() => setEdit(null)}>
          <TenantBillingForm tenantId={edit.tenant_id} billing={edit} onSaved={() => { setEdit(null); reload(); }} />
        </Modal>
      )}
    </div>
  );
}

const OWN = [["onboarding_fee", "Onboarding fee (KES)"], ["subscription_rate_pct", "Subscription (% of loan book)"],
  ["subscription_minimum", "Monthly minimum (KES)"], ["branch_fee", "New branch fee (KES)"], ["branches_included", "Branches included"]];

export function TenantBillingForm({ tenantId, billing, onSaved }) {
  const [f, setF] = useState(() => ({
    billing_active: billing.billing_active, billing_start: billing.billing_start || "",
    billing_email: billing.billing_email || "", notes: billing.notes || "",
    onboarding_invoiced: billing.onboarding_invoiced,
    ...Object.fromEntries(OWN.map(([k]) => [k, billing.own_prices?.[k] ?? ""])),
  }));
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const std = billing.effective || {};
  const save = async () => {
    setBusy(true); setErr("");
    const body = { billing_active: f.billing_active, billing_email: f.billing_email || null, notes: f.notes || null,
      onboarding_invoiced: f.onboarding_invoiced, clear: [] };
    if (f.billing_start) body.billing_start = f.billing_start;
    for (const [k] of OWN) { if (f[k] === "" || f[k] == null) body.clear.push(k); else body[k] = Number(f[k]); }
    try {
      await api(`/api/v1/platform/tenants/${tenantId}/billing`, { method: "PUT", body });
      notify("Billing saved", { tone: "success" });
      onSaved?.();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-3">
      <ErrorNote text={err} />
      <label className="flex items-center gap-2 text-sm font-semibold">
        <input type="checkbox" checked={f.billing_active} onChange={(e) => setF({ ...f, billing_active: e.target.checked })} />
        Bill this DCP every month
      </label>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Billing starts" hint="First month to invoice"><input className="input" type="date" value={f.billing_start} onChange={(e) => setF({ ...f, billing_start: e.target.value })} /></Field>
        <Field label="Invoices go to (email)"><input className="input" type="email" value={f.billing_email} onChange={(e) => setF({ ...f, billing_email: e.target.value })} /></Field>
      </div>
      <div className="text-xs text-gray-500">Leave a price empty to use the standard price (shown in grey).</div>
      <div className="grid grid-cols-2 gap-3">
        {OWN.map(([k, label]) => (
          <Field key={k} label={label}>
            <input className="input" type="number" min="0" step="any" value={f[k]} placeholder={std[k] != null ? String(std[k]) : ""}
              onChange={(e) => setF({ ...f, [k]: e.target.value })} />
          </Field>
        ))}
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={f.onboarding_invoiced} onChange={(e) => setF({ ...f, onboarding_invoiced: e.target.checked })} />
        Onboarding fee already invoiced (or waived)
      </label>
      <Field label="Notes"><textarea className="input" rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      <div className="flex justify-end"><button className="btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button></div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
function Prices({ manage }) {
  const { data, error, reload } = useLoad("/api/v1/platform/pricing");
  const [f, setF] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { if (data) setF(data); }, [data]);
  if (!f) return <><ErrorNote text={error} /><Spinner /></>;
  const save = async () => {
    setErr("");
    try {
      await api("/api/v1/platform/pricing", { method: "PUT", body: {
        onboarding_fee: Number(f.onboarding_fee), subscription_rate_pct: Number(f.subscription_rate_pct),
        subscription_minimum: Number(f.subscription_minimum), branch_fee: Number(f.branch_fee),
        branches_included: Number(f.branches_included), bill_sms: f.bill_sms,
        payment_terms_days: Number(f.payment_terms_days), invoice_day: Number(f.invoice_day) } });
      notify("Standard prices saved", { tone: "success" });
      reload();
    } catch (e) { setErr(e.message); }
  };
  const inp = (k, props = {}) => <input className="input" type="number" disabled={!manage} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} {...props} />;
  return (
    <div className="card p-4 max-w-2xl">
      <p className="text-sm text-gray-500 mb-3">These apply to every DCP unless it has its own prices (DCP billing). Invoices are produced on the invoice day for the month before, using the loan book on that month's last day.</p>
      <ErrorNote text={err} />
      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="Onboarding fee, one-off (KES)">{inp("onboarding_fee", { min: 0 })}</Field>
        <Field label="Monthly subscription (% of loan book)">{inp("subscription_rate_pct", { min: 0, step: "0.01" })}</Field>
        <Field label="Monthly minimum (KES)">{inp("subscription_minimum", { min: 0 })}</Field>
        <Field label="New branch set-up fee (KES)">{inp("branch_fee", { min: 0 })}</Field>
        <Field label="Branches included" hint="Branches beyond this are charged once each">{inp("branches_included", { min: 0 })}</Field>
        <Field label="Payment terms (days)">{inp("payment_terms_days", { min: 0, max: 90 })}</Field>
        <Field label="Invoice day of the month">{inp("invoice_day", { min: 1, max: 28 })}</Field>
        <label className="flex items-center gap-2 text-sm mt-6">
          <input type="checkbox" disabled={!manage} checked={!!f.bill_sms} onChange={(e) => setF({ ...f, bill_sms: e.target.checked })} />
          Add SMS sent to the monthly invoice (rate card prices)
        </label>
      </div>
      {manage && <div className="flex justify-end mt-4"><button className="btn-primary" onClick={save}>Save standard prices</button></div>}
    </div>
  );
}

// --------------------------------------------------------------------------- //
function Unmatched() {
  const { data, error, loading, reload } = useLoad("/api/v1/platform/unmatched");
  const invs = useLoad("/api/v1/platform/invoices?status=unpaid");
  const facs = useLoad("/api/v1/platform/facilities");
  const [pick, setPick] = useState({});
  const [err, setErr] = useState("");
  const allocate = async (u) => {
    const v = pick[u.id] || "";
    const [kind, id] = v.split(":");
    setErr("");
    try {
      await api(`/api/v1/platform/unmatched/${u.id}/allocate`, { method: "POST",
        body: kind === "inv" ? { invoice_id: Number(id) } : { facility_id: Number(id) } });
      notify("Payment allocated", { tone: "success" });
      reload(); invs.reload(); facs.reload();
    } catch (e) { setErr(e.message); }
  };
  const items = data?.items || [];
  return (
    <div className="card overflow-hidden">
      <p className="text-sm text-gray-500 px-4 pt-3">Payments into Finyl's paybill whose account number named no invoice, facility or DCP code. Choose where each belongs.</p>
      <ErrorNote text={error || err} />
      {loading && !data ? <Spinner /> : !items.length ? <Empty text="Nothing waiting" /> : (
        <table className="min-w-full text-sm">
          <thead><tr><th className="th">Received</th><th className="th">Reference</th><th className="th">Payer</th><th className="th">Account typed</th>
            <th className="th text-right">Amount</th><th className="th">Belongs to</th><th className="th" /></tr></thead>
          <tbody>
            {items.map((u) => (
              <tr key={u.id}>
                <td className="td">{fmtDate(u.paid_at)}</td><td className="td font-mono text-xs">{u.reference}</td><td className="td">{u.payer || "—"}</td>
                <td className="td">{u.account_entered || "—"}</td><td className="td text-right tabnums">{kes2(u.amount)}</td>
                <td className="td">
                  {u.status !== "open" ? <Badge value="resolved">{u.status}</Badge> : (
                    <select className="input !w-64 text-sm" value={pick[u.id] || ""} onChange={(e) => setPick({ ...pick, [u.id]: e.target.value })}>
                      <option value="">Choose…</option>
                      <optgroup label="Unpaid invoices">{(invs.data?.items || []).map((i) => <option key={i.id} value={`inv:${i.id}`}>{i.number} · {i.tenant} · {kes(i.balance)}</option>)}</optgroup>
                      <optgroup label="Loan facilities">{(facs.data?.items || []).filter((f) => f.status !== "closed").map((f) => <option key={f.id} value={`fac:${f.id}`}>{f.number} · {f.tenant}</option>)}</optgroup>
                    </select>
                  )}
                </td>
                <td className="td">{u.status === "open" && <button className="btn-primary !py-1" disabled={!pick[u.id]} onClick={() => allocate(u)}>Allocate</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
