// WP15 — HR settings: statutory rates (change them here when the law changes),
// payroll rules, and a calculator to check a salary under these rates.
import { useEffect, useState } from "react";
import { PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, notify } from "../../lib/api";
import { ErrorNote, Field, HrTabs, Note, amt, useLoad } from "./common";

export default function HrSettings() {
  const { can } = useAuth();
  const { data, error, setData } = useLoad("/api/v1/hr/settings");
  const [st, setSt] = useState(null);
  const [err, setErr] = useState("");
  const [basic, setBasic] = useState(50000);
  const [calc, setCalc] = useState(null);
  useEffect(() => { if (data) setSt(JSON.parse(JSON.stringify(data.statutory))); }, [data]);
  useEffect(() => {
    const t = setTimeout(() => api("/api/v1/hr/payroll/preview", { method: "POST", body: { basic: Number(basic || 0) } }).then(setCalc).catch(() => {}), 300);
    return () => clearTimeout(t);
  }, [basic, data]);
  if (!data || !st) return <div><HrTabs /><ErrorNote text={error} />{!error && <Spinner />}</div>;
  const off = !can("hr.manage");
  const save = async (extra = {}) => {
    setErr("");
    try {
      const changed = {};
      Object.keys(st).forEach((k) => { if (JSON.stringify(st[k]) !== JSON.stringify(data.statutory[k])) changed[k] = st[k]; });
      const r = await api("/api/v1/hr/settings", { method: "PUT", body: { statutory: Object.keys(changed).length ? changed : null,
        deduct_absence: data.deduct_absence, payroll_approval: data.payroll_approval, employee_prefix: data.employee_prefix, ...extra } });
      setData(r); notify("HR settings saved", { tone: "success" });
    } catch (e) { setErr(e.message); }
  };
  const num = (k) => (
    <Field key={k} label={data.labels[k]}>
      <input type="number" step="any" className={`input ${data.overridden.includes(k) ? "border-amber-400" : ""}`} disabled={off} value={st[k]}
        onChange={(e) => setSt({ ...st, [k]: e.target.value === "" ? "" : Number(e.target.value) })} />
    </Field>
  );
  return (
    <div>
      <PageHeader title="HR settings" crumbs={["HR", "Settings"]} />
      <HrTabs />
      <ErrorNote text={err} />
      <div className="grid lg:grid-cols-3 gap-4">
        <div className="card p-5 lg:col-span-2">
          <div className="font-bold mb-1">Statutory rates</div>
          <Note>Set to Kenya's rules in force from {data.defaults.effective_from}: NSSF year 4, SHIF 2.75%, Housing Levy 1.5%, PAYE bands unchanged by the Finance Act 2026.
            When the law changes, update the figures here; payrolls already approved keep the old figures. Changed values are outlined in amber.</Note>
          <div className="font-semibold text-sm mb-2">PAYE bands (monthly)</div>
          <table className="text-sm mb-4"><tbody>{st.paye_bands.map((b, i) => (
            <tr key={i}><td className="pr-2 py-1">{i === st.paye_bands.length - 1 ? "Everything above" : "Next"}</td>
              <td className="pr-2">{i < st.paye_bands.length - 1 && <input type="number" className="input !w-32" disabled={off} value={b[0] ?? ""}
                onChange={(e) => setSt({ ...st, paye_bands: st.paye_bands.map((x, j) => (j === i ? [Number(e.target.value), x[1]] : x)) })} />}</td>
              <td className="pr-2">at</td><td><input type="number" step="any" className="input !w-24" disabled={off} value={b[1]}
                onChange={(e) => setSt({ ...st, paye_bands: st.paye_bands.map((x, j) => (j === i ? [x[0], Number(e.target.value)] : x)) })} /></td><td className="pl-1">%</td></tr>
          ))}</tbody></table>
          <div className="grid sm:grid-cols-2 gap-3">
            {["personal_relief", "insurance_relief_rate", "insurance_relief_cap", "pension_deduction_cap", "disability_exemption", "nssf_rate",
              "nssf_lower_limit", "nssf_upper_limit", "shif_rate", "shif_minimum", "housing_levy_rate", "nita_per_employee"].map(num)}
            <Field label={data.labels.public_holidays}><input className="input" disabled={off} value={(st.public_holidays || []).join(", ")}
              onChange={(e) => setSt({ ...st, public_holidays: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></Field>
            <Field label={data.labels.effective_from}><input type="date" className="input" disabled={off} value={st.effective_from} onChange={(e) => setSt({ ...st, effective_from: e.target.value })} /></Field>
          </div>
          <div className="font-bold mt-6 mb-2">Payroll rules</div>
          <div className="space-y-2 text-sm">
            <label className="flex items-center gap-2"><input type="checkbox" disabled={off} checked={data.payroll_approval} onChange={(e) => setData({ ...data, payroll_approval: e.target.checked })} />
              A second person must approve payroll (recommended)</label>
            <label className="flex items-center gap-2"><input type="checkbox" disabled={off} checked={data.deduct_absence} onChange={(e) => setData({ ...data, deduct_absence: e.target.checked })} />
              Absences marked in the attendance register are unpaid</label>
            <div className="max-w-xs"><Field label="Employee number prefix"><input className="input" disabled={off} value={data.employee_prefix} onChange={(e) => setData({ ...data, employee_prefix: e.target.value })} /></Field></div>
          </div>
          {!off && <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => save({ reset_statutory: true, statutory: null })}>Back to Kenya's standard rates</button>
            <button className="btn-primary" onClick={() => save()}>Save</button></div>}
        </div>
        <div className="card p-5 h-fit">
          <div className="font-bold mb-2">Check a salary</div>
          <Field label="Basic salary (KES a month)"><input type="number" className="input" value={basic} onChange={(e) => setBasic(e.target.value)} /></Field>
          {calc && <table className="w-full text-sm mt-3"><tbody>
            {[["Gross pay", "gross"], ["NSSF", "nssf_employee"], ["SHIF", "shif"], ["Housing Levy", "housing_employee"], ["Taxable pay", "taxable_pay"], ["Tax charged", "income_tax"],
              ["Personal relief", "personal_relief"], ["PAYE", "paye"]].map(([l, k]) => <tr key={k}><td className="py-0.5">{l}</td><td className="text-right tabnums">{amt(calc[k])}</td></tr>)}
            <tr className="font-bold border-t border-border"><td className="py-1">Net pay</td><td className="text-right tabnums">{amt(calc.net)}</td></tr>
            <tr className="text-gray-500"><td className="py-0.5">Employer also pays</td><td className="text-right tabnums">{amt(calc.nssf_employer + calc.housing_employer + calc.nita)}</td></tr>
          </tbody></table>}
          <div className="text-xs text-gray-400 mt-2">Uses the saved rates.</div>
        </div>
      </div>
    </div>
  );
}
