// Security configuration — tenant-wide security policy: OTP enforcement, device
// binding, geo-fence, time-fence, stipend rate, screenshot capture and activity
// logging. Loads the current config and PUTs the edited fields back.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { PageHeader, Spinner } from "../../components/ui";

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

function Section({ title, children }) {
  return (
    <div className="card p-5">
      <h3 className="font-bold text-base mb-1">{title}</h3>
      <div>{children}</div>
    </div>
  );
}

export default function SecurityConfig() {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");

  useEffect(() => {
    api("/api/v1/settings/security").then(setCfg).catch((e) => setErr(e.detail));
  }, []);

  const set = (k, v) => { setCfg((p) => ({ ...p, [k]: v })); setOk(""); };
  const num = (v) => (v === "" || v == null ? "" : v);

  const save = async () => {
    setSaving(true); setErr(""); setOk("");
    try {
      const body = {
        require_otp: cfg.require_otp,
        otp_channels: cfg.otp_channels,
        device_binding_enabled: cfg.device_binding_enabled,
        geofence_enabled: cfg.geofence_enabled,
        geofence_radius_km: cfg.geofence_radius_km === "" ? null : Number(cfg.geofence_radius_km),
        geofence_center_lat: cfg.geofence_center_lat === "" ? null : Number(cfg.geofence_center_lat),
        geofence_center_lng: cfg.geofence_center_lng === "" ? null : Number(cfg.geofence_center_lng),
        time_fence_enabled: cfg.time_fence_enabled,
        time_fence_start: cfg.time_fence_start || null,
        time_fence_end: cfg.time_fence_end || null,
        time_fence_timezone: cfg.time_fence_timezone || null,
        stipend_rate_kes_per_km: cfg.stipend_rate_kes_per_km === "" ? null : Number(cfg.stipend_rate_kes_per_km),
        screenshot_on_action: cfg.screenshot_on_action,
        activity_log_enabled: cfg.activity_log_enabled,
      };
      const res = await api("/api/v1/settings/security", { method: "PUT", body });
      setCfg(res); setOk("Security settings saved.");
    } catch (ex) { setErr(ex.detail || "Could not save settings"); }
    finally { setSaving(false); }
  };

  if (!cfg && !err) return (<div><PageHeader title="Security Configuration" crumbs={["Security", "Configuration"]} /><Spinner /></div>);

  return (
    <div>
      <PageHeader title="Security Configuration" crumbs={["Security", "Configuration"]}
        actions={cfg && <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save changes"}</button>} />
      {err && <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{err}</div>}
      {ok && <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg p-3">{ok}</div>}

      {cfg && (
        <div className="grid gap-5 lg:grid-cols-2">
          <Section title="Authentication">
            <Toggle label="Require OTP at login" hint="Second-factor code enforced for every login."
              checked={!!cfg.require_otp} onChange={(v) => set("require_otp", v)} />
            <Toggle label="Device binding" hint="Sessions bound to registered devices only."
              checked={!!cfg.device_binding_enabled} onChange={(v) => set("device_binding_enabled", v)} />
            <div className="pt-3">
              <label className="label">OTP channels</label>
              <input className="input" value={cfg.otp_channels || ""} onChange={(e) => set("otp_channels", e.target.value)}
                placeholder="e.g. sms,email" />
              <p className="text-[11px] text-gray-400 mt-1">Comma-separated delivery channels.</p>
            </div>
          </Section>

          <Section title="Monitoring">
            <Toggle label="Screenshot on action" hint="Capture a screenshot on sensitive actions."
              checked={!!cfg.screenshot_on_action} onChange={(v) => set("screenshot_on_action", v)} />
            <Toggle label="Activity logging" hint="Record staff activity events for audit."
              checked={!!cfg.activity_log_enabled} onChange={(v) => set("activity_log_enabled", v)} />
            <div className="pt-3">
              <label className="label">Field stipend rate (KES / km)</label>
              <input className="input" type="number" step="0.01" value={num(cfg.stipend_rate_kes_per_km)}
                onChange={(e) => set("stipend_rate_kes_per_km", e.target.value)} />
            </div>
          </Section>

          <Section title="Geo-fence">
            <Toggle label="Enable geo-fence" hint="Restrict field actions to a location radius."
              checked={!!cfg.geofence_enabled} onChange={(v) => set("geofence_enabled", v)} />
            <div className="grid grid-cols-3 gap-3 pt-3">
              <div><label className="label">Center lat</label>
                <input className="input" type="number" step="0.000001" value={num(cfg.geofence_center_lat)} onChange={(e) => set("geofence_center_lat", e.target.value)} /></div>
              <div><label className="label">Center lng</label>
                <input className="input" type="number" step="0.000001" value={num(cfg.geofence_center_lng)} onChange={(e) => set("geofence_center_lng", e.target.value)} /></div>
              <div><label className="label">Radius (km)</label>
                <input className="input" type="number" step="0.1" value={num(cfg.geofence_radius_km)} onChange={(e) => set("geofence_radius_km", e.target.value)} /></div>
            </div>
          </Section>

          <Section title="Time-fence">
            <Toggle label="Enable time-fence" hint="Restrict logins to working hours."
              checked={!!cfg.time_fence_enabled} onChange={(v) => set("time_fence_enabled", v)} />
            <div className="grid grid-cols-3 gap-3 pt-3">
              <div><label className="label">Start (HH:MM)</label>
                <input className="input" type="time" value={cfg.time_fence_start || ""} onChange={(e) => set("time_fence_start", e.target.value)} /></div>
              <div><label className="label">End (HH:MM)</label>
                <input className="input" type="time" value={cfg.time_fence_end || ""} onChange={(e) => set("time_fence_end", e.target.value)} /></div>
              <div><label className="label">Timezone</label>
                <input className="input" value={cfg.time_fence_timezone || ""} onChange={(e) => set("time_fence_timezone", e.target.value)} placeholder="Africa/Nairobi" /></div>
            </div>
          </Section>
        </div>
      )}
    </div>
  );
}
