// Validation preferences — tenant KYC/onboarding validation policy. Each toggle
// makes a check mandatory during client onboarding (age, M-Pesa, face, alt phone,
// CRB, guarantor, OCR). Loads current prefs and PUTs edited fields back.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { PageHeader, Spinner } from "../../components/ui";

const TOGGLES = [
  ["age_check_mandatory", "Age check", "Borrower must meet the minimum age requirement."],
  ["mpesa_validation_mandatory", "M-Pesa validation", "Verify the borrower's M-Pesa number is active."],
  ["face_validation_mandatory", "Face validation", "Biometric selfie-to-ID match required."],
  ["alt_phone_validation_mandatory", "Alternate phone", "Secondary contact phone must be validated."],
  ["crb_check_mandatory", "CRB check", "Credit Reference Bureau check required."],
  ["guarantor_validation_mandatory", "Guarantor validation", "At least one validated guarantor required."],
  ["ocr_mandatory", "Document OCR", "ID/document OCR extraction must succeed."],
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

export default function ValidationPrefs() {
  const [prefs, setPrefs] = useState(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");

  useEffect(() => {
    api("/api/v1/settings/validation-prefs").then(setPrefs).catch((e) => setErr(e.detail));
  }, []);

  const set = (k, v) => { setPrefs((p) => ({ ...p, [k]: v })); setOk(""); };

  const save = async () => {
    setSaving(true); setErr(""); setOk("");
    try {
      const body = {};
      TOGGLES.forEach(([k]) => { body[k] = !!prefs[k]; });
      const res = await api("/api/v1/settings/validation-prefs", { method: "PUT", body });
      setPrefs(res); setOk("Validation preferences saved.");
    } catch (ex) { setErr(ex.detail || "Could not save preferences"); }
    finally { setSaving(false); }
  };

  if (!prefs && !err) return (<div><PageHeader title="Validation Preferences" crumbs={["Administration", "Validation Preferences"]} /><Spinner /></div>);

  return (
    <div>
      <PageHeader title="Validation Preferences" crumbs={["Administration", "Validation Preferences"]}
        actions={prefs && <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save changes"}</button>} />
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      {ok && <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg p-3">{ok}</div>}

      {prefs && (
        <div className="card p-5 max-w-2xl">
          <h3 className="font-bold text-base mb-1">Mandatory onboarding checks</h3>
          <p className="text-xs text-gray-400 mb-2">Enabled checks block client onboarding until they pass.</p>
          {TOGGLES.map(([key, label, hint]) => (
            <Toggle key={key} label={label} hint={hint} checked={!!prefs[key]} onChange={(v) => set(key, v)} />
          ))}
        </div>
      )}
    </div>
  );
}
