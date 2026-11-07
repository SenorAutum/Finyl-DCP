// KYC Settings (Validation Preferences) — each DCP's own KYC policy: which checks
// are required, how strict the process is, and its limits. The central KYC
// decision, enforced at profile approval, loan application and loan approval,
// reads these (WP6). Open to each DCP's admin (security.manage).
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { PageHeader, Spinner } from "../../components/ui";

const TOGGLES = [
  ["age_check_mandatory", "Age check", "Borrower must meet the minimum age requirement."],
  ["mpesa_validation_mandatory", "M-Pesa name confirmation", "A staff member confirms the name M-Pesa holds for the client's number (who may confirm is set on the right)."],
  ["ekyc_mandatory", "eKYC (ID registry)", "The external ID-registry check must verify the client. A failed eKYC always blocks, even when this is off."],
  ["face_validation_mandatory", "Face validation", "Biometric selfie-to-ID match required."],
  ["alt_phone_validation_mandatory", "Alternate phone", "Secondary phone must be verified by a name-lookup provider (none is connected yet, so leave this off)."],
  ["crb_check_mandatory", "CRB check", "A successful CRB report is required, no older than the validity period set under Limits."],
  ["guarantor_validation_mandatory", "Guarantor validation", "At least one validated guarantor required."],
  ["ocr_mandatory", "ID document read (OCR)", "An uploaded ID must be read by OCR and its number must match the profile. Needs an OCR engine on the server."],
];

function Toggle({ label, hint, checked, onChange }) {
  return (
    <label className="flex items-start justify-between gap-3 py-3 border-b border-border last:border-0 cursor-pointer">
      <div>
        <div className="text-sm font-semibold">{label}</div>
        {hint && <div className="text-xs text-gray-400 mt-0.5">{hint}</div>}
      </div>
      <button type="button" onClick={() => onChange(!checked)}
        className={`shrink-0 w-11 h-6 rounded-full transition-colors relative ${checked ? "bg-accent" : "bg-gray-300"}`}>
        <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${checked ? "translate-x-5" : ""}`} />
      </button>
    </label>
  );
}

const DOC_TYPES = ["national_id_front", "national_id_back", "passport", "kra_pin",
  "business_permit", "payslip", "bank_statement", "other"];

function Choice({ label, hint, value, options, onChange }) {
  return (
    <div className="py-3 border-b border-border">
      <div className="text-sm font-semibold">{label}</div>
      {hint && <div className="text-xs text-gray-400 mt-0.5 mb-2">{hint}</div>}
      <div className="space-y-1.5">
        {options.map(([v, text]) => (
          <label key={String(v)} className="flex items-start gap-2 text-sm cursor-pointer">
            <input type="radio" className="mt-1" checked={value === v} onChange={() => onChange(v)} />
            <span>{text}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

function NumberField({ label, hint, value, onChange, placeholder }) {
  return (
    <div className="py-3 border-b border-border last:border-0">
      <label className="text-sm font-semibold block">{label}</label>
      {hint && <div className="text-xs text-gray-400 mt-0.5 mb-2">{hint}</div>}
      <input type="number" min="0" className="input max-w-[160px]" value={value ?? ""} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
    </div>
  );
}

export default function ValidationPrefs() {
  const [prefs, setPrefs] = useState(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");

  useEffect(() => {
    api("/api/v1/settings/validation-prefs").then(setPrefs).catch((e) => setErr(e.detail));
  }, []);

  const set = (k, v) => { setPrefs((p) => ({ ...p, [k]: v })); setOk(""); };
  const rules = prefs?.custom_rules || {};
  const setRule = (k, v) => set("custom_rules", (() => {
    const next = { ...rules };
    if (v === null || v === "" || (Array.isArray(v) && v.length === 0)) delete next[k]; else next[k] = v;
    return next;
  })());
  const docs = rules.require_documents || [];

  const save = async () => {
    setSaving(true); setErr(""); setOk("");
    try {
      const body = {};
      TOGGLES.forEach(([k]) => { body[k] = !!prefs[k]; });
      body.custom_rules = rules;
      body.mpesa_confirmation_mode = prefs.mpesa_confirmation_mode || "second_person";
      body.identity_edit_approvals = prefs.identity_edit_approvals || 2;
      body.allow_review_override = prefs.allow_review_override !== false;
      body.crb_max_age_days = prefs.crb_max_age_days || 90;
      body.min_credit_score = prefs.min_credit_score == null ? -1 : prefs.min_credit_score;
      const res = await api("/api/v1/settings/validation-prefs", { method: "PUT", body });
      setPrefs(res); setOk("KYC settings saved. They apply from the next approval or application.");
    } catch (ex) { setErr(ex.detail || "Could not save settings"); }
    finally { setSaving(false); }
  };

  const header = <PageHeader title="KYC Settings" crumbs={["Settings", "KYC Settings"]}
    actions={prefs && <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save changes"}</button>} />;
  if (!prefs && !err) return (<div>{header}<Spinner /></div>);

  return (
    <div>
      {header}
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      {ok && <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg p-3">{ok}</div>}

      {prefs && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-start">
          <div className="card p-5">
            <h3 className="font-bold text-base mb-1">Which checks are required</h3>
            <p className="text-xs text-gray-400 mb-2">
              Switched-on checks must pass before a profile is approved, a loan is applied for, or a loan is approved.
              Under-18 applicants and a missing National ID are always refused.
            </p>
            {TOGGLES.map(([key, label, hint]) => (
              <Toggle key={key} label={label} hint={hint} checked={!!prefs[key]} onChange={(v) => set(key, v)} />
            ))}
          </div>

          <div className="space-y-5">
            <div className="card p-5">
              <h3 className="font-bold text-base mb-1">How strict the process is</h3>
              <Choice label="Who confirms a client's M-Pesa name"
                value={prefs.mpesa_confirmation_mode || "second_person"} onChange={(v) => set("mpesa_confirmation_mode", v)}
                options={[["second_person", "A second person: someone with client-approval rights who did not capture the client (recommended)"],
                          ["single_person", "Any staff member who can edit clients, including the onboarding officer"]]} />
              <Choice label="Approvals needed to change phone, ID number, names or date of birth on a client with an open loan"
                value={prefs.identity_edit_approvals || 2} onChange={(v) => set("identity_edit_approvals", v)}
                options={[[2, "Two different approvers (recommended)"], [1, "One approver"]]}
                hint="The person who asked for the change can never approve it." />
              <Choice label="When the KYC check says “needs manual review”"
                value={prefs.allow_review_override !== false} onChange={(v) => set("allow_review_override", v)}
                hint="For example an open KYC escalation, or an ID whose number could not be read."
                options={[[true, "A user with KYC-resolve rights may approve, writing down what they checked"],
                          [false, "It must be fixed first (escalation closed, ID read again)"]]} />
            </div>

            <div className="card p-5">
              <h3 className="font-bold text-base mb-1">Limits</h3>
              <NumberField label="Minimum age" placeholder="18" value={rules.min_age ?? null}
                hint="Never below 18. Each loan product can also set its own age range."
                onChange={(v) => setRule("min_age", v)} />
              <NumberField label="Maximum age" placeholder="No limit" value={rules.max_age ?? null}
                onChange={(v) => setRule("max_age", v)} />
              <NumberField label="CRB report stays valid for (days)" placeholder="90" value={prefs.crb_max_age_days ?? 90}
                hint="Used when the CRB check is required, or a minimum score is set."
                onChange={(v) => set("crb_max_age_days", v)} />
              <NumberField label="Minimum CRB score" placeholder="No minimum" value={prefs.min_credit_score ?? null}
                hint="Leave empty for no minimum. When set, a successful CRB check is needed."
                onChange={(v) => set("min_credit_score", v)} />
              <div className="py-3">
                <div className="text-sm font-semibold">Documents every client must have on file</div>
                <div className="grid grid-cols-2 gap-1.5 mt-2">
                  {DOC_TYPES.map((t) => (
                    <label key={t} className="flex items-center gap-2 text-sm capitalize cursor-pointer">
                      <input type="checkbox" checked={docs.includes(t)}
                        onChange={(e) => setRule("require_documents", e.target.checked ? [...docs, t] : docs.filter((d) => d !== t))} />
                      {t.replace(/_/g, " ")}
                    </label>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
