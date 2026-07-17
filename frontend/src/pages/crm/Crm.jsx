// CRM & Field Sales: 5-stage Kanban pipeline + geo-tagged site-visit logging.
import { useEffect, useState } from "react";
import { api, fmtDate, fmtKES } from "../../lib/api";
import { Modal, PageHeader, Spinner } from "../../components/ui";

const STAGE_LABELS = { lead: "Lead", contacted: "Contacted", field_visit: "Field Visit", app_setup: "App Setup", disbursed: "Disbursed" };
const STAGE_COLORS = { lead: "border-gray-300", contacted: "border-blue-400", field_visit: "border-amber-400", app_setup: "border-teal", disbursed: "border-accent" };

function GeoChip({ lat, lng }) {
  if (lat == null || lng == null) return <span className="text-xs text-gray-400">No GPS fix</span>;
  return (
    <a className="inline-flex items-center gap-1 text-[11px] font-semibold text-teal bg-teal-50 px-2 py-0.5 rounded-full hover:underline"
      href={`https://www.google.com/maps?q=${lat},${lng}`} target="_blank" rel="noreferrer">
      📍 {lat.toFixed(4)}, {lng.toFixed(4)}
    </a>
  );
}

export default function Crm() {
  const [board, setBoard] = useState(null);
  const [staff, setStaff] = useState([]);
  const [regions, setRegions] = useState([]);
  const [creating, setCreating] = useState(false);
  const [visitFor, setVisitFor] = useState(null);  // lead when logging a visit
  const [visits, setVisits] = useState(null);      // {lead, list} when viewing visits
  const [err, setErr] = useState("");

  const load = () => api("/api/v1/crm/board").then(setBoard).catch((e) => setErr(e.detail));
  useEffect(() => {
    load();
    api("/api/v1/lending/org").then((o) => { setStaff(o.staff); setRegions(o.regions); }).catch(() => {});
  }, []);

  const move = async (lead, dir) => {
    const idx = board.stages.indexOf(lead.stage);
    const target = board.stages[idx + dir];
    if (!target) return;
    await api(`/api/v1/crm/leads/${lead.id}/stage`, { method: "PATCH", body: { stage: target } }).catch(() => {});
    load();
  };

  // ---- Lead creation ----
  const [form, setForm] = useState({ name: "", phone: "", sector: "", region_id: "", assigned_staff_id: "", estimated_loan_amount: "", notes: "" });
  const createLead = async (e) => {
    e.preventDefault(); setErr("");
    try {
      await api("/api/v1/crm/leads", { method: "POST", body: {
        name: form.name, phone: form.phone || null, sector: form.sector || null,
        region_id: form.region_id ? Number(form.region_id) : null,
        assigned_staff_id: form.assigned_staff_id ? Number(form.assigned_staff_id) : null,
        estimated_loan_amount: Number(form.estimated_loan_amount || 0), notes: form.notes || null,
      }});
      setCreating(false); setForm({ name: "", phone: "", sector: "", region_id: "", assigned_staff_id: "", estimated_loan_amount: "", notes: "" });
      load();
    } catch (e2) { setErr(e2.detail); }
  };

  // ---- Site visit logging (geo-tagged) ----
  const [vf, setVf] = useState({ staff_id: "", latitude: "", longitude: "", outcome: "positive", notes: "" });
  const captureGps = () => {
    if (!navigator.geolocation) { setErr("Geolocation not supported by this browser"); return; }
    navigator.geolocation.getCurrentPosition(
      (p) => setVf((s) => ({ ...s, latitude: p.coords.latitude.toFixed(6), longitude: p.coords.longitude.toFixed(6) })),
      () => setErr("Could not obtain GPS fix — enter coordinates manually"),
    );
  };
  const logVisit = async (e) => {
    e.preventDefault(); setErr("");
    try {
      await api("/api/v1/crm/visits", { method: "POST", body: {
        lead_id: visitFor.id, staff_id: vf.staff_id ? Number(vf.staff_id) : null,
        visit_date: new Date().toISOString().slice(0, 10),
        latitude: vf.latitude ? Number(vf.latitude) : null,
        longitude: vf.longitude ? Number(vf.longitude) : null,
        outcome: vf.outcome, notes: vf.notes || null,
      }});
      setVisitFor(null); setVf({ staff_id: "", latitude: "", longitude: "", outcome: "positive", notes: "" });
      load();
    } catch (e2) { setErr(e2.detail); }
  };

  const showVisits = async (lead) => {
    const list = await api(`/api/v1/crm/leads/${lead.id}/visits`).catch(() => []);
    setVisits({ lead, list });
  };

  if (!board) return <Spinner />;

  return (
    <div>
      <PageHeader title="CRM Pipeline" crumbs={["Engagement", "CRM"]}
        actions={<button className="btn-primary" onClick={() => setCreating(true)}>+ New Lead</button>} />
      {err && <div className="mb-3 text-sm text-red-600 bg-red-50 rounded-lg p-3">{err}</div>}

      {/* Kanban — horizontal scroll on mobile */}
      <div className="flex gap-3 overflow-x-auto pb-3 -mx-1 px-1">
        {board.stages.map((stage) => (
          <div key={stage} className="w-64 shrink-0">
            <div className={`bg-surface rounded-t-xl border-t-[3px] ${STAGE_COLORS[stage]} border-x border-border px-3 py-2.5 flex justify-between items-center`}>
              <span className="text-sm font-bold">{STAGE_LABELS[stage] || stage}</span>
              <span className="text-xs font-bold text-gray-400 bg-canvas px-2 py-0.5 rounded-full">{board.columns[stage]?.length || 0}</span>
            </div>
            <div className="bg-canvas/70 border border-t-0 border-border rounded-b-xl p-2 space-y-2 min-h-[120px] max-h-[65vh] overflow-y-auto">
              {(board.columns[stage] || []).map((lead) => {
                const idx = board.stages.indexOf(stage);
                return (
                  <div key={lead.id} className="card p-3">
                    <div className="font-semibold text-sm">{lead.name}</div>
                    <div className="text-xs text-gray-400">{lead.phone || "no phone"} · {lead.sector || "—"}</div>
                    {lead.estimated_loan_amount > 0 && (
                      <div className="text-xs font-semibold text-teal mt-1">Est. {fmtKES(lead.estimated_loan_amount)}</div>
                    )}
                    <div className="text-[11px] text-gray-400 mt-1">
                      {lead.assigned_staff_name || "Unassigned"} · {lead.visit_count} visit{lead.visit_count === 1 ? "" : "s"}
                    </div>
                    <div className="flex items-center gap-1 mt-2">
                      <button className="btn-ghost !py-0.5 !px-2 text-xs" disabled={idx === 0} onClick={() => move(lead, -1)} title="Move back">‹</button>
                      <button className="btn-ghost !py-0.5 !px-2 text-xs" disabled={idx === board.stages.length - 1} onClick={() => move(lead, 1)} title="Move forward">›</button>
                      <div className="flex-1" />
                      <button className="btn-ghost !py-0.5 !px-2 text-[11px]" onClick={() => showVisits(lead)}>Visits</button>
                      <button className="btn-ghost !py-0.5 !px-2 text-[11px]" onClick={() => setVisitFor(lead)}>+ Visit</button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {creating && (
        <Modal title="New Lead" onClose={() => setCreating(false)}>
          <form onSubmit={createLead} className="space-y-3">
            <div><label className="label">Name *</label><input className="input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="label">Phone</label><input className="input" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></div>
              <div><label className="label">Sector</label>
                <select className="input" value={form.sector} onChange={(e) => setForm({ ...form, sector: e.target.value })}>
                  <option value="">—</option>
                  {["retail", "agriculture", "food", "transport", "services", "manufacturing"].map((s) => <option key={s} value={s}>{s}</option>)}
                </select></div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="label">Region</label>
                <select className="input" value={form.region_id} onChange={(e) => setForm({ ...form, region_id: e.target.value })}>
                  <option value="">—</option>{regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                </select></div>
              <div><label className="label">Assigned officer</label>
                <select className="input" value={form.assigned_staff_id} onChange={(e) => setForm({ ...form, assigned_staff_id: e.target.value })}>
                  <option value="">Unassigned</option>
                  {staff.filter((s) => s.role === "loan_officer").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select></div>
            </div>
            <div><label className="label">Estimated loan amount (KES)</label>
              <input type="number" className="input" value={form.estimated_loan_amount} onChange={(e) => setForm({ ...form, estimated_loan_amount: e.target.value })} /></div>
            <div><label className="label">Notes</label>
              <textarea className="input" rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></div>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn-primary">Create lead</button>
            </div>
          </form>
        </Modal>
      )}

      {visitFor && (
        <Modal title={`Log Site Visit — ${visitFor.name}`} onClose={() => setVisitFor(null)}>
          <form onSubmit={logVisit} className="space-y-3">
            <div><label className="label">Field officer</label>
              <select className="input" value={vf.staff_id} onChange={(e) => setVf({ ...vf, staff_id: e.target.value })}>
                <option value="">—</option>
                {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select></div>
            <div>
              <div className="flex items-center justify-between">
                <label className="label !mb-0">Geo-tag (GPS)</label>
                <button type="button" className="btn-ghost !py-1 !px-2.5 text-xs" onClick={captureGps}>📍 Capture my location</button>
              </div>
              <div className="grid grid-cols-2 gap-3 mt-1.5">
                <input className="input" placeholder="Latitude e.g. -1.2921" value={vf.latitude} onChange={(e) => setVf({ ...vf, latitude: e.target.value })} />
                <input className="input" placeholder="Longitude e.g. 36.8219" value={vf.longitude} onChange={(e) => setVf({ ...vf, longitude: e.target.value })} />
              </div>
            </div>
            <div><label className="label">Outcome</label>
              <select className="input" value={vf.outcome} onChange={(e) => setVf({ ...vf, outcome: e.target.value })}>
                {["positive", "neutral", "negative", "not_found"].map((o) => <option key={o} value={o}>{o.replace("_", " ")}</option>)}
              </select></div>
            <div><label className="label">Notes</label>
              <textarea className="input" rows={2} value={vf.notes} onChange={(e) => setVf({ ...vf, notes: e.target.value })} /></div>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setVisitFor(null)}>Cancel</button>
              <button className="btn-primary">Log visit</button>
            </div>
          </form>
        </Modal>
      )}

      {visits && (
        <Modal title={`Site Visits — ${visits.lead.name}`} onClose={() => setVisits(null)} wide>
          {visits.list.length === 0 ? <p className="text-sm text-gray-400">No visits logged yet.</p> : (
            <div className="space-y-3">
              {visits.list.map((v) => (
                <div key={v.id} className="border border-border rounded-xl p-3.5">
                  <div className="flex flex-wrap items-center gap-2 justify-between">
                    <div className="text-sm font-semibold">{fmtDate(v.visit_date)} · {v.staff_name || "Unassigned"}</div>
                    <GeoChip lat={v.latitude} lng={v.longitude} />
                  </div>
                  <div className="text-xs mt-1"><span className="capitalize font-semibold text-teal">{(v.outcome || "—").replace("_", " ")}</span>
                    {v.notes && <span className="text-gray-500"> — {v.notes}</span>}</div>
                </div>
              ))}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
