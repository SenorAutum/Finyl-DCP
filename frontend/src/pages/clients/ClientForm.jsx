// Create / Edit Client — full-screen KYC onboarding screen.
//
// Layout mirrors the reference design:
//   1. Documents card (multi-file queue) + Attachments side panel
//   2. Green M-Pesa validation band
//   3. "Client ID Details" 3-column grid
//   4. "Business & Profile" card
//   5. Mobile Wallet / Next of Kin tabbed sub-grids with "+ Add row"
//
// Files chosen before the client exists are held in a local queue and uploaded
// straight after the client record is saved. "Process ID" posts the queue (or
// the already-saved ID documents) to the OCR endpoint and merges the returned
// National-ID fields into the form.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, upload, blobUrl, fmtKES } from "../../lib/api";
import { Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

export const EMPTY_CLIENT = {
  serial_number: "", national_id: "", phone: "",
  first_name: "", middle_name: "", last_name: "",
  date_of_birth: "", gender: "", district_of_birth: "", place_of_issue: "",
  date_of_issue: "", district: "", division: "", location: "", sub_location: "",
  kyc_status: "draft", current_credit_rating: "", is_active: true,
  onboarded_by: "", approved_by_user_id: "",
  region_id: "", branch_id: "", business_sector: "",
  baseline_monthly_sales: 0, baseline_employees: 0, credit_score: 0,
  wallets: [], next_of_kin: [],
};

const EMPTY_WALLET = { id: null, mobile_number: "", wallet_number: "", operator: "M-Pesa", active: true };
const EMPTY_NOK = { id: null, full_name: "", relationship: "Spouse", mobile_number: "", national_id: "", address: "", active: true };

const OCR_EXT = /\.(jpe?g|png|webp|pdf)$/i;
const bytes = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

// Fields the OCR merge is allowed to write into the form.
const OCR_FIELDS = ["serial_number", "national_id", "first_name", "middle_name", "last_name",
  "date_of_birth", "gender", "district_of_birth", "place_of_issue", "date_of_issue",
  "district", "division", "location", "sub_location"];

function Field({ label, children, hint }) {
  return (
    <div>
      <label className="label">{label}</label>
      {children}
      {hint && <div className="text-[11px] text-gray-400 mt-0.5">{hint}</div>}
    </div>
  );
}

export default function ClientForm({ clientId, onClose, onSaved }) {
  const { user, can } = useAuth();
  const fileRef = useRef(null);
  // Primary-identity fields (phone / national_id / date_of_birth) are locked on
  // an existing client unless the officer holds clients.edit_locked. Creation is
  // always allowed so these are only frozen when editing a saved record.
  const locked = !!clientId && !can("clients.edit_locked");

  const [form, setForm] = useState({ ...EMPTY_CLIENT, onboarded_by: user?.full_name || "" });
  const [ref, setRef] = useState({ wallet_operators: [], relationships: [], doc_types: [], kyc_statuses: [], approvers: [] });
  const [org, setOrg] = useState({ regions: [], branches: [] });
  const [loading, setLoading] = useState(!!clientId);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState("wallets");

  // Documents: queued (not yet uploaded) + saved (already on the server)
  const [queue, setQueue] = useState([]);          // [{file, doc_type}]
  const [saved, setSaved] = useState([]);
  const [preview, setPreview] = useState(null);

  // Async service results
  const [ocr, setOcr] = useState(null);
  const [ocrBusy, setOcrBusy] = useState(false);
  const [ekyc, setEkyc] = useState(null);
  const [ekycBusy, setEkycBusy] = useState(false);
  const [mpesa, setMpesa] = useState(null);
  const [mpesaBusy, setMpesaBusy] = useState(false);

  const set = (k) => (e) => {
    const v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setForm((s) => ({ ...s, [k]: v }));
  };

  // ---- initial load -------------------------------------------------------
  useEffect(() => {
    api("/api/v1/clients/reference").then(setRef).catch(() => {});
    api("/api/v1/lending/org").then(setOrg).catch(() => {});
  }, []);

  useEffect(() => {
    if (!clientId) return;
    setLoading(true);
    api(`/api/v1/clients/${clientId}`)
      .then((c) => {
        const next = { ...EMPTY_CLIENT };
        Object.keys(EMPTY_CLIENT).forEach((k) => {
          if (c[k] !== undefined && c[k] !== null) next[k] = c[k];
        });
        next.wallets = (c.wallets || []).map((w) => ({ ...EMPTY_WALLET, ...w }));
        next.next_of_kin = (c.next_of_kin || []).map((n) => ({ ...EMPTY_NOK, ...n }));
        next.approved_by_user_id = c.approved_by_user_id || "";
        setForm(next);
        setSaved(c.documents || []);
        if (c.mpesa_validated) {
          setMpesa({ matched: true, msisdn: c.phone, registered_name: c.mpesa_validation_name,
                     result_desc: "Previously validated" });
        }
        if (c.ekyc_status) setEkyc({ status: c.ekyc_status, reference: c.ekyc_reference });
      })
      .catch((e) => setErr(e.detail))
      .finally(() => setLoading(false));
  }, [clientId]);

  // ---- document queue -----------------------------------------------------
  const addFiles = (e) => {
    const picked = Array.from(e.target.files || []);
    if (!picked.length) return;
    setQueue((q) => [...q, ...picked.map((f) => ({ file: f, doc_type: guessType(f.name) }))]);
    e.target.value = "";           // allow re-picking the same file
  };
  // Best-effort pre-selection of the document type from the file name — the
  // officer can always change it in the Attachments panel. Values must be
  // members of DOC_TYPES (served by /api/v1/clients/reference).
  const guessType = (name) => {
    const n = name.toLowerCase();
    if (n.includes("back")) return "national_id_back";
    if (n.includes("front") || /\bid\b|_id|id_/.test(n)) return "national_id_front";
    if (n.includes("passport")) return "passport";
    if (n.includes("kra") || n.includes("pin")) return "kra_pin";
    if (n.includes("permit")) return "business_permit";
    if (n.includes("payslip") || n.includes("pay_slip")) return "payslip";
    if (n.includes("statement") || n.includes("bank")) return "bank_statement";
    return "other";
  };
  const dropQueued = (i) => setQueue((q) => q.filter((_, idx) => idx !== i));
  const setQueuedType = (i, v) => setQueue((q) => q.map((it, idx) => (idx === i ? { ...it, doc_type: v } : it)));

  const deleteSaved = async (doc) => {
    if (!clientId) return;
    try {
      await api(`/api/v1/clients/${clientId}/documents/${doc.id}`, { method: "DELETE" });
      setSaved((s) => s.filter((d) => d.id !== doc.id));
    } catch (e) { setErr(e.detail); }
  };

  const openSaved = async (doc) => {
    try { setPreview({ url: await blobUrl(`/api/v1/clients/${clientId}/documents/${doc.id}/download`), doc }); }
    catch (e) { setErr(e.detail); }
  };

  const uploadQueue = async (id) => {
    if (!queue.length) return [];
    const fd = new FormData();
    queue.forEach((q) => fd.append("files", q.file, q.file.name));
    const types = queue.map((q) => q.doc_type).join(",");
    const res = await upload(`/api/v1/clients/${id}/documents?doc_types=${encodeURIComponent(types)}`, fd);
    setQueue([]);
    return res.documents || [];
  };

  // ---- Process ID (OCR) ---------------------------------------------------
  const ocrable = useMemo(() => queue.filter((q) => OCR_EXT.test(q.file.name)), [queue]);

  const processId = async () => {
    setErr(""); setOcr(null);
    if (!ocrable.length) {
      setErr("Queue at least one JPEG, PNG or PDF of the ID before running Process ID.");
      return;
    }
    setOcrBusy(true);
    try {
      const fd = new FormData();
      ocrable.forEach((q) => fd.append("files", q.file, q.file.name));
      const path = `/api/v1/clients/ocr/process-id${clientId ? `?client_id=${clientId}` : ""}`;
      const res = await upload(path, fd);
      setOcr(res);
      // Merge — only fill fields the officer has left empty, never clobber typing.
      setForm((s) => {
        const next = { ...s };
        OCR_FIELDS.forEach((k) => {
          const v = res.fields?.[k];
          if (v && !String(next[k] || "").trim()) next[k] = v;
        });
        return next;
      });
      // Preview the first OCR-able image inline.
      const firstImg = ocrable.find((q) => /\.(jpe?g|png|webp)$/i.test(q.file.name));
      if (firstImg) setPreview({ url: URL.createObjectURL(firstImg.file), doc: { original_name: firstImg.file.name } });
    } catch (e) {
      setErr(e.detail);
    } finally { setOcrBusy(false); }
  };

  // ---- eKYC ---------------------------------------------------------------
  const runEkyc = async () => {
    setErr(""); setEkycBusy(true);
    try {
      const res = await api("/api/v1/clients/ekyc/verify", {
        method: "POST",
        body: {
          client_id: clientId || null, national_id: form.national_id,
          first_name: form.first_name, middle_name: form.middle_name || null,
          last_name: form.last_name, date_of_birth: form.date_of_birth || null,
          phone: form.phone || null,
        },
      });
      setEkyc(res);
      if (res.status === "verified" && form.kyc_status === "draft") {
        setForm((s) => ({ ...s, kyc_status: "validated" }));
      }
    } catch (e) { setErr(e.detail); } finally { setEkycBusy(false); }
  };

  // ---- Validate M-Pesa ----------------------------------------------------
  const validateMpesa = async () => {
    setErr(""); setMpesaBusy(true);
    try {
      const res = await api("/api/v1/clients/validate-mpesa", {
        method: "POST",
        body: {
          client_id: clientId || null, phone: form.phone,
          national_id: form.national_id,
          expected_name: [form.first_name, form.middle_name, form.last_name].filter(Boolean).join(" "),
        },
      });
      setMpesa(res);
    } catch (e) { setErr(e.detail); setMpesa(null); } finally { setMpesaBusy(false); }
  };

  // ---- nested sub-grids ---------------------------------------------------
  const addRow = (key) => setForm((s) => ({
    ...s, [key]: [...s[key], key === "wallets" ? { ...EMPTY_WALLET } : { ...EMPTY_NOK }],
  }));
  const setRow = (key, i, field, value) => setForm((s) => ({
    ...s, [key]: s[key].map((r, idx) => (idx === i ? { ...r, [field]: value } : r)),
  }));
  const dropRow = (key, i) => setForm((s) => ({ ...s, [key]: s[key].filter((_, idx) => idx !== i) }));

  // ---- save ---------------------------------------------------------------
  const save = async (e) => {
    e.preventDefault();
    setErr(""); setSaving(true);
    const body = { ...form };
    ["region_id", "branch_id", "approved_by_user_id"].forEach((k) => { body[k] = body[k] ? Number(body[k]) : null; });
    ["date_of_birth", "date_of_issue"].forEach((k) => { body[k] = body[k] || null; });
    body.baseline_monthly_sales = Number(body.baseline_monthly_sales || 0);
    body.baseline_employees = Number(body.baseline_employees || 0);
    body.credit_score = Number(body.credit_score || 0);
    body.wallets = form.wallets.filter((w) => w.mobile_number || w.wallet_number);
    body.next_of_kin = form.next_of_kin.filter((n) => n.full_name || n.mobile_number);
    try {
      const res = clientId
        ? await api(`/api/v1/clients/${clientId}`, { method: "PUT", body })
        : await api("/api/v1/clients", { method: "POST", body });
      if (queue.length) await uploadQueue(res.id);
      // Validations run while the client was still a draft could not be stored
      // against a row that did not exist yet — replay them now so the badges,
      // audit trail and eKYC reference are persisted. Failures are non-fatal.
      if (!clientId) {
        if (mpesa?.matched) {
          await api("/api/v1/clients/validate-mpesa", {
            method: "POST", body: { client_id: res.id },
          }).catch(() => {});
        }
        if (ekyc?.status) {
          await api("/api/v1/clients/ekyc/verify", {
            method: "POST", body: { client_id: res.id },
          }).catch(() => {});
        }
      }
      onSaved?.(res);
    } catch (e2) {
      setErr(e2.detail); setSaving(false);
    }
  };

  const title = clientId ? `Edit Client — ${form.first_name} ${form.last_name}` : "Create Client";

  return (
    <div className="fixed inset-0 z-50 bg-canvas overflow-y-auto">
      {/* sticky header */}
      <div className="sticky top-0 z-10 bg-surface border-b border-border px-4 sm:px-6 py-3 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="text-xs text-gray-400">Home / Lending / Clients / {clientId ? "Edit" : "Create"}</div>
          <h1 className="text-xl font-extrabold">{title}</h1>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button type="submit" form="client-form" className="btn-primary" disabled={saving}>
            {saving ? "Saving…" : clientId ? "Save changes" : "Save client"}
          </button>
        </div>
      </div>

      {loading ? <Spinner /> : (
        <form id="client-form" onSubmit={save} className="p-4 sm:p-6 space-y-5 max-w-[1400px] mx-auto">
          {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}

          {/* ---------------- Documents + Attachments ---------------- */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            <div className="card p-5 lg:col-span-2">
              <h2 className="font-bold text-base mb-1">Documents</h2>
              <p className="text-xs text-gray-500 leading-relaxed mb-3">
                You can select several files at once (Ctrl/Cmd+click or Shift+click in the dialog).
                They queue until Save. Process ID runs OCR on every JPEG/PDF in the queue
                (e.g. front + back) and merges fields; preview uses the first JPEG.
              </p>

              <input ref={fileRef} type="file" multiple onChange={addFiles} className="hidden" />
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" className="btn-ghost" onClick={() => fileRef.current?.click()}>
                  📎 Choose files
                </button>
                <button type="button" className="btn-ghost" onClick={processId} disabled={ocrBusy}>
                  {ocrBusy ? "Processing…" : "🪪 Process ID"}
                </button>
                <button type="button" className="btn-ghost" onClick={runEkyc} disabled={ekycBusy}>
                  {ekycBusy ? "Verifying…" : "🛡️ eKYC"}
                </button>
                <span className="text-xs text-gray-400">
                  Any file type · max 10 MB each · {queue.length} queued
                </span>
              </div>

              {ocr && (
                <div className="mt-4 rounded-lg border border-teal/40 bg-teal/5 p-3">
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div className="text-sm font-semibold text-teal">
                      OCR complete — {Object.keys(ocr.fields || {}).length} field(s) read from {ocr.files_processed || 1} file(s)
                    </div>
                    <span className="text-[11px] text-gray-500">Engine: {ocr.engine || "tesseract"}</span>
                  </div>
                  <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-4 gap-y-1">
                    {Object.entries(ocr.fields || {}).map(([k, v]) => (
                      <div key={k} className="text-[11px]">
                        <span className="text-gray-500">{k.replace(/_/g, " ")}: </span>
                        <span className="font-semibold">{String(v)}</span>
                        {ocr.confidence?.[k] != null && (
                          <span className="text-gray-400"> ({Math.round(ocr.confidence[k] * 100)}%)</span>
                        )}
                      </div>
                    ))}
                  </div>
                  <details className="mt-2">
                    <summary className="text-[11px] text-gray-500 cursor-pointer">Raw OCR text</summary>
                    <pre className="mt-1 text-[10px] whitespace-pre-wrap text-gray-600 max-h-40 overflow-y-auto">{ocr.raw_text}</pre>
                  </details>
                </div>
              )}

              {ekyc && (
                <div className={`mt-3 rounded-lg border p-3 text-sm ${
                  ekyc.status === "verified" ? "border-accent/40 bg-accent/5 text-accent" : "border-amber-300 bg-amber-50 text-amber-700"}`}>
                  <span className="font-semibold">eKYC {ekyc.status?.replace(/_/g, " ")}</span>
                  {ekyc.verified_name && <> — {ekyc.verified_name}</>}
                  {ekyc.match_score != null && <> · match {ekyc.match_score}%</>}
                  {ekyc.reference && <span className="text-gray-500 text-xs"> · ref {ekyc.reference}</span>}
                  {ekyc.provider && <span className="text-gray-400 text-xs"> · {ekyc.provider}</span>}
                </div>
              )}
            </div>

            {/* Attachments side panel */}
            <div className="card p-5">
              <h2 className="font-bold text-base mb-3">Attachments</h2>
              {!queue.length && !saved.length && (
                <p className="text-xs text-gray-400">Nothing attached yet.</p>
              )}
              <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                {queue.map((q, i) => (
                  <div key={`q${i}`} className="rounded-lg border border-amber-300 bg-amber-50 p-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-xs font-semibold truncate">{q.file.name}</div>
                        <div className="text-[10px] text-amber-700">Queued · {bytes(q.file.size)}</div>
                      </div>
                      <button type="button" className="text-amber-700 hover:text-red-600 text-sm" onClick={() => dropQueued(i)}>🗑</button>
                    </div>
                    <select className="input !py-1 !text-[11px] mt-1.5" value={q.doc_type} onChange={(e) => setQueuedType(i, e.target.value)}>
                      {(ref.doc_types || []).map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
                    </select>
                  </div>
                ))}
                {saved.map((d) => (
                  <div key={`s${d.id}`} className="rounded-lg border border-border bg-white p-2 flex items-start justify-between gap-2">
                    <button type="button" className="min-w-0 text-left" onClick={() => openSaved(d)}>
                      <div className="text-xs font-semibold truncate hover:text-accent">{d.original_name}</div>
                      <div className="text-[10px] text-gray-400">
                        {d.doc_type?.replace(/_/g, " ")} · {bytes(d.size_bytes || 0)}{d.ocr_applied ? " · OCR" : ""}
                      </div>
                    </button>
                    <button type="button" className="text-gray-400 hover:text-red-600 text-sm" onClick={() => deleteSaved(d)}>🗑</button>
                  </div>
                ))}
              </div>
              {preview && (
                <div className="mt-3">
                  <div className="text-[11px] text-gray-500 mb-1 truncate">Preview · {preview.doc?.original_name}</div>
                  <img src={preview.url} alt="document preview" className="w-full rounded-lg border border-border" />
                </div>
              )}
            </div>
          </div>

          {/* ---------------- M-Pesa validation band ---------------- */}
          <div className="rounded-xl border border-accent/40 bg-accent/10 px-4 py-3 flex flex-wrap items-center gap-3">
            <div className="flex-1 min-w-[240px]">
              <div className="text-sm font-bold text-teal">M-Pesa number validation</div>
              <div className="text-xs text-gray-600">
                Confirms the mobile number is registered to the same National ID before disbursement.
              </div>
            </div>
            <input className="input max-w-[200px] bg-white" placeholder="2547XXXXXXXX" disabled={locked}
              value={form.phone} onChange={set("phone")} />
            <button type="button" className="btn-primary" onClick={validateMpesa} disabled={mpesaBusy}>
              {mpesaBusy ? "Checking…" : "Validate M-Pesa"}
            </button>
            {mpesa && (
              <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold ${
                mpesa.matched ? "bg-accent text-white" : "bg-red-100 text-red-700"}`}>
                {mpesa.matched ? "✓" : "✕"} {mpesa.registered_name || mpesa.result_desc}
                {mpesa.msisdn && <span className="opacity-80">· {mpesa.msisdn}</span>}
              </span>
            )}
          </div>

          {/* ---------------- Client ID Details ---------------- */}
          <div className="card p-5">
            <h2 className="font-bold text-base mb-4">Client ID Details</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <Field label="Serial Number"><input className="input" value={form.serial_number || ""} onChange={set("serial_number")} /></Field>
              <Field label="National ID *" hint={locked ? "🔒 Locked — requires elevated approval to edit." : undefined}>
                <input className="input" required disabled={locked} value={form.national_id || ""} onChange={set("national_id")} />
              </Field>
              <Field label="Mobile Number" hint={locked ? "🔒 Locked — requires elevated approval to edit." : undefined}>
                <input className="input" placeholder="2547XXXXXXXX" disabled={locked} value={form.phone || ""} onChange={set("phone")} />
              </Field>

              <Field label="First Name *"><input className="input" required value={form.first_name || ""} onChange={set("first_name")} /></Field>
              <Field label="Middle Name"><input className="input" value={form.middle_name || ""} onChange={set("middle_name")} /></Field>
              <Field label="Last Name *"><input className="input" required value={form.last_name || ""} onChange={set("last_name")} /></Field>

              <Field label="Date of Birth" hint={locked ? "🔒 Locked — requires elevated approval to edit." : undefined}>
                <input type="date" className="input" disabled={locked} value={form.date_of_birth || ""} onChange={set("date_of_birth")} />
              </Field>
              <Field label="Gender">
                <select className="input" value={form.gender || ""} onChange={set("gender")}>
                  <option value="">—</option><option value="female">Female</option><option value="male">Male</option>
                </select>
              </Field>
              <Field label="District of Birth"><input className="input" value={form.district_of_birth || ""} onChange={set("district_of_birth")} /></Field>

              <Field label="Place of Issue"><input className="input" value={form.place_of_issue || ""} onChange={set("place_of_issue")} /></Field>
              <Field label="Date of Issue"><input type="date" className="input" value={form.date_of_issue || ""} onChange={set("date_of_issue")} /></Field>
              <Field label="District"><input className="input" value={form.district || ""} onChange={set("district")} /></Field>

              <Field label="Division"><input className="input" value={form.division || ""} onChange={set("division")} /></Field>
              <Field label="Location"><input className="input" value={form.location || ""} onChange={set("location")} /></Field>
              <Field label="Sub Location"><input className="input" value={form.sub_location || ""} onChange={set("sub_location")} /></Field>

              <Field label="KYC Status">
                <select className="input" value={form.kyc_status} onChange={set("kyc_status")}>
                  {(ref.kyc_statuses?.length ? ref.kyc_statuses : ["draft", "pending", "validated", "failed", "rejected"])
                    .map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </Field>
              <Field label="Current Credit Rating">
                <select className="input" value={form.current_credit_rating || ""} onChange={set("current_credit_rating")}>
                  <option value="">—</option>
                  {["A", "B", "C", "D", "E"].map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </Field>
              <Field label="Onboarded By" hint="Set automatically from the signed-in officer.">
                <input className="input bg-gray-50" readOnly value={form.onboarded_by || user?.full_name || ""} />
              </Field>

              <Field label="Approved By">
                <select className="input" value={form.approved_by_user_id || ""} onChange={set("approved_by_user_id")}>
                  <option value="">— not approved yet —</option>
                  {(ref.approvers || []).map((a) => <option key={a.id} value={a.id}>{a.name} ({a.role.replace(/_/g, " ")})</option>)}
                </select>
              </Field>
              <Field label="Active">
                <label className="flex items-center gap-2 h-[38px] text-sm">
                  <input type="checkbox" className="w-4 h-4 accent-emerald-500" checked={!!form.is_active} onChange={set("is_active")} />
                  Client is active
                </label>
              </Field>
            </div>
          </div>

          {/* ---------------- Business & Profile ---------------- */}
          <div className="card p-5">
            <h2 className="font-bold text-base mb-4">Business &amp; Profile</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <Field label="Region">
                <select className="input" value={form.region_id || ""} onChange={set("region_id")}>
                  <option value="">—</option>
                  {org.regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                </select>
              </Field>
              <Field label="Branch">
                <select className="input" value={form.branch_id || ""} onChange={set("branch_id")}>
                  <option value="">—</option>
                  {org.branches
                    .filter((b) => !form.region_id || b.region_id === Number(form.region_id))
                    .map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                </select>
              </Field>
              <Field label="Business Sector">
                <select className="input" value={form.business_sector || ""} onChange={set("business_sector")}>
                  <option value="">—</option>
                  {["retail", "agriculture", "food", "transport", "services", "manufacturing"]
                    .map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </Field>
              <Field label="Baseline Monthly Sales (KES)" hint={fmtKES(form.baseline_monthly_sales)}>
                <input type="number" className="input" value={form.baseline_monthly_sales} onChange={set("baseline_monthly_sales")} />
              </Field>
              <Field label="Baseline Employees"><input type="number" className="input" value={form.baseline_employees} onChange={set("baseline_employees")} /></Field>
              <Field label="Credit Score"><input type="number" className="input" value={form.credit_score || 0} onChange={set("credit_score")} /></Field>
            </div>
          </div>

          {/* ---------------- Mobile Wallet / Next of Kin ---------------- */}
          <div className="card overflow-hidden">
            <div className="flex border-b border-border">
              {[["wallets", `Mobile Wallet (${form.wallets.length})`], ["nok", `Next of Kin (${form.next_of_kin.length})`]]
                .map(([k, lbl]) => (
                  <button key={k} type="button" onClick={() => setTab(k)}
                    className={`px-5 py-3 text-sm font-semibold border-b-2 -mb-px transition-colors ${
                      tab === k ? "border-accent text-accent" : "border-transparent text-gray-500 hover:text-charcoal"}`}>
                    {lbl}
                  </button>
                ))}
            </div>

            {tab === "wallets" ? (
              <div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead><tr>
                      <th className="th">Mobile Number</th><th className="th">Wallet Number</th>
                      <th className="th">Operator</th><th className="th">Active</th><th className="th"></th>
                    </tr></thead>
                    <tbody>
                      {form.wallets.length === 0 && (
                        <tr><td className="td text-gray-400" colSpan={5}>No mobile wallets yet — use “+ Add row”.</td></tr>
                      )}
                      {form.wallets.map((w, i) => (
                        <tr key={i} className="border-t border-border">
                          <td className="td"><input className="input !py-1.5" placeholder="2547XXXXXXXX" value={w.mobile_number || ""} onChange={(e) => setRow("wallets", i, "mobile_number", e.target.value)} /></td>
                          <td className="td"><input className="input !py-1.5" value={w.wallet_number || ""} onChange={(e) => setRow("wallets", i, "wallet_number", e.target.value)} /></td>
                          <td className="td">
                            <select className="input !py-1.5" value={w.operator || ""} onChange={(e) => setRow("wallets", i, "operator", e.target.value)}>
                              {(ref.wallet_operators || ["M-Pesa"]).map((o) => <option key={o} value={o}>{o}</option>)}
                            </select>
                          </td>
                          <td className="td"><input type="checkbox" className="w-4 h-4 accent-emerald-500" checked={!!w.active} onChange={(e) => setRow("wallets", i, "active", e.target.checked)} /></td>
                          <td className="td text-right"><button type="button" className="text-gray-400 hover:text-red-600" onClick={() => dropRow("wallets", i)}>🗑</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="p-3 border-t border-border">
                  <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={() => addRow("wallets")}>+ Add row</button>
                </div>
              </div>
            ) : (
              <div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead><tr>
                      <th className="th">Full Name</th><th className="th">Relationship</th><th className="th">Mobile Number</th>
                      <th className="th">National ID</th><th className="th">Address</th><th className="th">Active</th><th className="th"></th>
                    </tr></thead>
                    <tbody>
                      {form.next_of_kin.length === 0 && (
                        <tr><td className="td text-gray-400" colSpan={7}>No next of kin yet — use “+ Add row”.</td></tr>
                      )}
                      {form.next_of_kin.map((n, i) => (
                        <tr key={i} className="border-t border-border">
                          <td className="td"><input className="input !py-1.5" value={n.full_name || ""} onChange={(e) => setRow("next_of_kin", i, "full_name", e.target.value)} /></td>
                          <td className="td">
                            <select className="input !py-1.5" value={n.relationship || ""} onChange={(e) => setRow("next_of_kin", i, "relationship", e.target.value)}>
                              {(ref.relationships || ["Spouse"]).map((r) => <option key={r} value={r}>{r}</option>)}
                            </select>
                          </td>
                          <td className="td"><input className="input !py-1.5" placeholder="2547XXXXXXXX" value={n.mobile_number || ""} onChange={(e) => setRow("next_of_kin", i, "mobile_number", e.target.value)} /></td>
                          <td className="td"><input className="input !py-1.5" value={n.national_id || ""} onChange={(e) => setRow("next_of_kin", i, "national_id", e.target.value)} /></td>
                          <td className="td"><input className="input !py-1.5" value={n.address || ""} onChange={(e) => setRow("next_of_kin", i, "address", e.target.value)} /></td>
                          <td className="td"><input type="checkbox" className="w-4 h-4 accent-emerald-500" checked={!!n.active} onChange={(e) => setRow("next_of_kin", i, "active", e.target.checked)} /></td>
                          <td className="td text-right"><button type="button" className="text-gray-400 hover:text-red-600" onClick={() => dropRow("next_of_kin", i)}>🗑</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="p-3 border-t border-border">
                  <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={() => addRow("next_of_kin")}>+ Add row</button>
                </div>
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2 pb-8">
            <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn-primary" disabled={saving}>
              {saving ? "Saving…" : clientId ? "Save changes" : "Save client"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
