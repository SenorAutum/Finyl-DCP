// WP15 — payroll: prepare a month, check it, a second person approves (it goes
// into the books), mark salaries paid, download the bank / M-Pesa file and the
// KRA, NSSF, SHIF and Housing Levy returns, and record paying them.
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Empty, KpiCard, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, download, fmtDate, notify } from "../../lib/api";
import { MoneyAccountSelect } from "../books/common";
import { ErrorNote, Field, HrTabs, Note, amt, exportCsv, kes, kes2, lastMonthStr, today, useLoad } from "./common";

const RUN_BADGE = { draft: ["draft", "draft"], approved: ["approved", "approved"], paid: ["paid", "paid"], cancelled: ["closed", "cancelled"] };

export default function Payroll() {
  const { can } = useAuth();
  const nav = useNavigate();
  const { data, error, loading } = useLoad("/api/v1/hr/payroll/runs");
  const stat = useLoad("/api/v1/hr/payroll/statutory");
  const [period, setPeriod] = useState(lastMonthStr());
  const [err, setErr] = useState("");
  const [remit, setRemit] = useState(null);
  const create = async () => {
    setErr("");
    try { const r = await api("/api/v1/hr/payroll/runs", { method: "POST", body: { period } }); nav(`/hr/payroll/${r.id}`); }
    catch (e) { setErr(e.message); }
  };
  const rows = data?.runs || [];
  return (
    <div>
      <PageHeader title="Payroll" crumbs={["HR", "Payroll"]} />
      <HrTabs />
      <ErrorNote text={error || err} />
      <div className="grid lg:grid-cols-3 gap-4">
        <div className="card overflow-hidden lg:col-span-2">
          {can("payroll.run") && (
            <div className="flex gap-3 items-end px-4 py-3 border-b border-border">
              <Field label="Month"><input type="month" className="input !w-auto" value={period} onChange={(e) => setPeriod(e.target.value)} /></Field>
              <button className="btn-primary" onClick={create}>Prepare payroll</button>
            </div>
          )}
          {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No payroll yet. Add employees, then prepare last month's payroll." /> : (
            <table className="min-w-full text-sm"><thead><tr><th className="th">Month</th><th className="th text-right">People</th><th className="th text-right">Gross</th>
              <th className="th text-right">Net pay</th><th className="th text-right">Cost to the company</th><th className="th">Status</th></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.id} className={`hover:bg-canvas cursor-pointer ${r.status === "cancelled" ? "opacity-40" : ""}`} onClick={() => nav(`/hr/payroll/${r.id}`)}>
                  <td className="td font-semibold">{r.label}</td><td className="td text-right">{r.employees}</td><td className="td text-right tabnums">{amt(r.totals.gross)}</td>
                  <td className="td text-right tabnums">{amt(r.totals.net)}</td><td className="td text-right tabnums">{amt(r.totals.employer_cost)}</td>
                  <td className="td"><Badge value={RUN_BADGE[r.status][0]}>{RUN_BADGE[r.status][1]}</Badge></td></tr>
              ))}</tbody></table>
          )}
        </div>
        <div className="card p-4">
          <div className="font-bold mb-1">Owed to KRA, NSSF, SHA and others</div>
          <div className="text-xs text-gray-500 mb-3">From approved payrolls, less what has been paid. PAYE, Housing Levy, NSSF and SHIF are due by the 9th of the next month.</div>
          <table className="w-full text-sm"><tbody>{(stat.data?.balances || []).map((b) => (
            <tr key={b.key}><td className="py-1">{b.label}</td><td className="text-right tabnums font-semibold">{amt(b.owed)}</td>
              <td className="text-right">{can("payroll.approve") && b.owed > 0 && <button className="text-xs font-semibold text-accent" onClick={() => setRemit({ ...b, amount: b.owed, paid_on: today(), account_id: null, reference: "" })}>Pay</button>}</td></tr>
          ))}</tbody></table>
        </div>
      </div>
      {remit && (
        <Modal title={`Record payment: ${remit.label}`} onClose={() => setRemit(null)}>
          <ErrorNote text={err} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Amount"><input type="number" className="input" value={remit.amount} onChange={(e) => setRemit({ ...remit, amount: e.target.value })} /></Field>
            <Field label="Paid on"><input type="date" className="input" value={remit.paid_on} onChange={(e) => setRemit({ ...remit, paid_on: e.target.value })} /></Field>
            <div className="col-span-2"><Field label="Paid from"><MoneyAccountSelect value={remit.account_id} onChange={(v) => setRemit({ ...remit, account_id: v })} /></Field></div>
            <div className="col-span-2"><Field label="Reference (e.g. KRA payment slip number)"><input className="input" value={remit.reference} onChange={(e) => setRemit({ ...remit, reference: e.target.value })} /></Field></div>
          </div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setRemit(null)}>Cancel</button>
            <button className="btn-primary" disabled={!remit.account_id} onClick={async () => {
              setErr("");
              try { await api("/api/v1/hr/payroll/remit", { method: "POST", body: { body: remit.key, amount: Number(remit.amount), paid_on: remit.paid_on, account_id: remit.account_id, reference: remit.reference || null } });
                setRemit(null); stat.reload(); notify("Payment recorded in the books", { tone: "success" }); }
              catch (e) { setErr(e.message); }
            }}>Record payment</button></div>
        </Modal>
      )}
    </div>
  );
}

export function PayrollRun() {
  const { id } = useParams();
  const { can, user } = useAuth();
  const { data: r, error, reload } = useLoad(`/api/v1/hr/payroll/runs/${id}`);
  const [err, setErr] = useState("");
  const [paying, setPaying] = useState(null);
  const [busy, setBusy] = useState(false);
  const act = async (path, body) => {
    setErr(""); setBusy(true);
    try { await api(`/api/v1/hr/payroll/runs/${id}/${path}`, { method: "POST", body }); reload(); notify("Done", { tone: "success" }); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  if (!r) return <div><HrTabs /><ErrorNote text={error} />{!error && <Spinner />}</div>;
  const t = r.totals || {};
  const final = ["approved", "paid"].includes(r.status);
  const cols = [["employee_no", "No."], ["employee", "Name"], ["basic", "Basic"], ["gross", "Gross"], ["nssf_employee", "NSSF"], ["shif", "SHIF"], ["housing_employee", "Housing"],
    ["taxable_pay", "Taxable"], ["paye", "PAYE"], ["other_deductions", "Other"], ["net", "Net pay"]];
  return (
    <div>
      <PageHeader title={`Payroll ${r.label}`} crumbs={["HR", "Payroll", r.label]}
        actions={<>
          {r.status === "draft" && can("payroll.run") && <button className="btn-ghost" disabled={busy} onClick={() => act("recalculate")}>Recalculate</button>}
          {r.status === "draft" && can("payroll.run") && <button className="btn-ghost" onClick={() => { const reason = window.prompt("Why cancel this payroll?"); if (reason) act("cancel", { reason }); }}>Cancel</button>}
          {r.status === "draft" && can("payroll.approve") && <button className="btn-primary" disabled={busy || (r.requires_second_person && r.created_by_user_id === user?.id && user?.role !== "super_admin")}
            title={r.requires_second_person && r.created_by_user_id === user?.id ? "Someone else must approve the payroll you prepared" : ""} onClick={() => act("approve")}>Approve and post to the books</button>}
          {r.status === "approved" && can("payroll.approve") && <button className="btn-primary" onClick={() => setPaying({ paid_on: today(), account_id: null })}>Mark salaries paid</button>}
        </>} />
      <HrTabs />
      <ErrorNote text={err} />
      <div className="text-sm text-gray-500 mb-3">
        <Badge value={RUN_BADGE[r.status][0]}>{RUN_BADGE[r.status][1]}</Badge> Prepared by {r.created_by || "—"}{r.approved_by && ` · approved by ${r.approved_by}`}{r.paid_on && ` · paid ${fmtDate(r.paid_on)}`}
        {r.status === "draft" && " · Changes to employees, pay items or leave show after 'Recalculate'."}
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
        <KpiCard label="Gross pay" value={kes(t.gross)} sub={`${r.employees} people`} />
        <KpiCard label="PAYE" value={kes(t.paye)} />
        <KpiCard label="NSSF (both)" value={kes((t.nssf_employee || 0) + (t.nssf_employer || 0))} sub={`SHIF ${kes(t.shif)}`} />
        <KpiCard label="Housing Levy (both)" value={kes((t.housing_employee || 0) + (t.housing_employer || 0))} sub={`NITA ${kes(t.nita)}`} />
        <KpiCard label="Net pay" value={kes(t.net)} sub={`Cost to company ${kes(t.employer_cost)}`} tone="success" />
      </div>
      {final && (
        <div className="card p-3 mb-4 flex flex-wrap gap-2 items-center text-sm">
          <span className="font-semibold mr-2">Downloads:</span>
          <button className="btn-ghost" onClick={() => download(`/api/v1/hr/payroll/runs/${id}/salary-file.csv?method=bank`, `salaries_bank_${r.period.slice(0, 7)}.csv`)}>Bank salary file</button>
          <button className="btn-ghost" onClick={() => download(`/api/v1/hr/payroll/runs/${id}/salary-file.csv?method=mpesa`, `salaries_mpesa_${r.period.slice(0, 7)}.csv`)}>M-Pesa bulk file</button>
          {["paye", "nssf", "shif", "housing"].map((k) => (
            <button key={k} className="btn-ghost" onClick={() => download(`/api/v1/hr/payroll/runs/${id}/returns/${k}.csv`, `${k}_${r.period.slice(0, 7)}.csv`)}>
              {{ paye: "KRA PAYE (P10)", nssf: "NSSF return", shif: "SHIF return", housing: "Housing Levy" }[k]}</button>
          ))}
          {r.journal_id && <Link className="text-accent font-semibold ml-auto" to={`/books/journals?id=${r.journal_id}`}>See it in the books →</Link>}
        </div>
      )}
      <div className="card overflow-x-auto">
        <div className="flex justify-end px-4 py-2"><button className="btn-ghost" onClick={() => exportCsv(`payroll_${r.period.slice(0, 7)}.csv`, cols.map(([k, l]) => [l, k]), r.payslips)}>Export CSV</button></div>
        {!r.payslips.length ? <Empty text="Nobody to pay this month." /> : (
          <table className="min-w-full text-sm"><thead><tr>{cols.map(([k, l]) => <th key={k} className={`th ${["employee_no", "employee"].includes(k) ? "" : "text-right"}`}>{l}</th>)}<th className="th" /></tr></thead>
            <tbody>{r.payslips.map((p) => (
              <tr key={p.id}>{cols.map(([k]) => <td key={k} className={`td ${["employee_no", "employee"].includes(k) ? (k === "employee" ? "font-semibold" : "font-mono text-xs") : "text-right tabnums"}`}>
                {["employee_no", "employee"].includes(k) ? p[k] : amt(p[k])}{k === "employee" && p.unpaid_days > 0 && <div className="text-xs text-amber-600 font-normal">{p.unpaid_days} unpaid days</div>}
                {k === "employee" && p.days_paid < p.days_in_month && <div className="text-xs text-gray-400 font-normal">{p.days_paid} of {p.days_in_month} days employed</div>}</td>)}
                <td className="td"><Link className="text-xs font-semibold text-accent" to={`/hr/payslips/${p.id}`}>Payslip</Link></td></tr>
            ))}
              <tr className="font-bold border-t-2 border-charcoal"><td className="td" colSpan={2}>Totals</td>{cols.slice(2).map(([k]) => <td key={k} className="td text-right tabnums">{amt(t[k])}</td>)}<td /></tr>
            </tbody></table>
        )}
      </div>
      {paying && (
        <Modal title="Mark salaries paid" onClose={() => setPaying(null)}>
          <Note>Record this after the bank or M-Pesa transfer of {kes2(t.net)} has gone out.</Note>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Paid on"><input type="date" className="input" value={paying.paid_on} onChange={(e) => setPaying({ ...paying, paid_on: e.target.value })} /></Field>
            <Field label="Paid from"><MoneyAccountSelect value={paying.account_id} onChange={(v) => setPaying({ ...paying, account_id: v })} /></Field>
          </div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setPaying(null)}>Cancel</button>
            <button className="btn-primary" disabled={!paying.account_id} onClick={async () => { await act("pay", paying); setPaying(null); }}>Mark paid</button></div>
        </Modal>
      )}
    </div>
  );
}

export function Payslip() {
  const { id } = useParams();
  const { data: p, error } = useLoad(`/api/v1/hr/payroll/payslips/${id}`);
  if (!p) return <div><ErrorNote text={error} />{!error && <Spinner />}</div>;
  const allowances = p.items.filter((i) => i.kind === "allowance");
  const deductions = p.items.filter((i) => i.kind === "deduction");
  const R = ({ l, v, b, neg }) => <tr className={b ? "font-bold border-t border-border" : ""}><td className="py-1">{l}</td><td className="py-1 text-right tabnums">{neg && v ? `(${amt(v)})` : amt(v)}</td></tr>;
  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex justify-end gap-2 mb-3 print:hidden"><button className="btn-ghost" onClick={() => window.history.back()}>Back</button><button className="btn-primary" onClick={() => window.print()}>Print / save as PDF</button></div>
      <div className="card p-6">
        <div className="flex justify-between mb-4">
          <div><div className="text-xl font-extrabold">{p.company}</div><div className="text-sm text-gray-500">Payslip for {p.label}{p.run_status !== "draft" ? "" : " (DRAFT)"}</div></div>
          <div className="text-right text-sm"><div className="font-bold">{p.employee}</div><div>{p.employee_no} · {p.job_title}</div><div className="text-gray-500">{p.branch || "Head office"}</div></div>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs text-gray-600 mb-4">
          <div>KRA PIN: {p.kra_pin || "—"}</div><div>NSSF: {p.nssf_no || "—"}</div><div>SHIF: {p.shif_no || "—"}</div>
          <div>Days paid: {p.days_paid} of {p.days_in_month}{p.unpaid_days ? ` (${p.unpaid_days} unpaid working days)` : ""}</div>
          <div className="col-span-2">Paid to: {p.pay_method === "mpesa" ? `M-Pesa ${p.mpesa_phone || ""}` : p.pay_method === "bank" ? `${p.bank_name || ""} ${p.bank_account_masked || ""}` : "Cash"}</div>
        </div>
        <div className="grid sm:grid-cols-2 gap-6">
          <table className="w-full text-sm"><tbody>
            <tr><td className="pb-1 font-bold" colSpan={2}>Earnings</td></tr>
            <R l="Basic pay" v={p.basic} />
            {allowances.map((a, i) => <R key={i} l={`${a.name}${a.taxable ? "" : " (not taxable)"}`} v={a.amount} />)}
            <R l="Gross pay" v={p.gross} b />
          </tbody></table>
          <table className="w-full text-sm"><tbody>
            <tr><td className="pb-1 font-bold" colSpan={2}>Deductions</td></tr>
            <R l="NSSF" v={p.nssf_employee} /><R l="SHIF" v={p.shif} /><R l="Housing Levy" v={p.housing_employee} />
            <R l="PAYE" v={p.paye} />
            {deductions.map((d, i) => <R key={i} l={d.name} v={d.amount} />)}
            <R l="Total deductions" v={p.gross - p.net} b />
          </tbody></table>
        </div>
        <div className="mt-4 rounded-xl bg-canvas p-4 flex justify-between items-center"><div className="font-bold">Net pay</div><div className="text-2xl font-extrabold tabnums">{kes2(p.net)}</div></div>
        <table className="w-full text-xs text-gray-600 mt-4"><tbody>
          <tr><td>Taxable pay</td><td className="text-right">{amt(p.taxable_pay)}</td><td className="pl-6">Tax charged</td><td className="text-right">{amt(p.income_tax)}</td></tr>
          <tr><td>Personal relief</td><td className="text-right">{amt(p.personal_relief)}</td><td className="pl-6">Insurance relief</td><td className="text-right">{amt(p.insurance_relief)}</td></tr>
          <tr><td>Employer NSSF</td><td className="text-right">{amt(p.nssf_employer)}</td><td className="pl-6">Employer Housing Levy</td><td className="text-right">{amt(p.housing_employer)}</td></tr>
        </tbody></table>
      </div>
    </div>
  );
}

export function P9() {
  const { id } = useParams();
  const [year, setYear] = useState(new Date().getFullYear());
  const { data: d, error } = useLoad(`/api/v1/hr/payroll/p9/${id}?year=${year}`);
  const cols = [["month", "Month"], ["basic", "Basic"], ["benefits", "Allowances"], ["gross", "Gross"], ["nssf_employee", "NSSF"], ["pension", "Pension"], ["shif", "SHIF"],
    ["housing_employee", "Housing"], ["taxable_pay", "Taxable pay"], ["income_tax", "Tax charged"], ["personal_relief", "Relief"], ["insurance_relief", "Ins. relief"], ["paye", "PAYE"]];
  return (
    <div>
      <div className="flex gap-2 mb-3 print:hidden items-end"><Field label="Year"><input type="number" className="input !w-28" value={year} onChange={(e) => setYear(e.target.value)} /></Field>
        <div className="flex-1" /><button className="btn-ghost" onClick={() => window.history.back()}>Back</button><button className="btn-primary" onClick={() => window.print()}>Print / save as PDF</button></div>
      <ErrorNote text={error} />
      {!d ? <Spinner /> : (
        <div className="card p-5 overflow-x-auto">
          <div className="text-lg font-extrabold">P9 tax deduction card, {d.year}</div>
          <div className="text-sm text-gray-600 mb-3">Employer: {d.employer} · Employee: {d.employee} ({d.employee_no}) · KRA PIN {d.kra_pin || "—"}</div>
          {!d.rows.length ? <Empty text="No approved payroll for this year." /> : (
            <table className="min-w-full text-xs"><thead><tr>{cols.map(([k, l]) => <th key={k} className={`th ${k === "month" ? "" : "text-right"}`}>{l}</th>)}</tr></thead>
              <tbody>{d.rows.map((r) => <tr key={r.month}>{cols.map(([k]) => <td key={k} className={`td ${k === "month" ? "" : "text-right tabnums"}`}>{k === "month" ? r[k] : amt(r[k])}</td>)}</tr>)}
                <tr className="font-bold border-t-2 border-charcoal"><td className="td">Total</td>{cols.slice(1).map(([k]) => <td key={k} className="td text-right tabnums">{amt(d.totals[k])}</td>)}</tr></tbody></table>
          )}
        </div>
      )}
    </div>
  );
}
