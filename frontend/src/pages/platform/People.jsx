// WP13 — Finyl's own staff and partners (directors, finance, operations,
// support): each has switches for what they may see or do, and the DCPs they
// may see (all, or the ones the super admin picks — changeable at any time).
import { useState } from "react";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { api, notify } from "../../lib/api";
import { ErrorNote, Field, PlatformTabs, useLoad, usePlatform } from "./common";

export default function People() {
  const { data, error, loading, reload } = useLoad("/api/v1/platform/users");
  const [edit, setEdit] = useState(null);
  const items = data?.items || [];
  return (
    <div>
      <PageHeader title="Finyl people" crumbs={["Finyl", "People"]}
        actions={<button className="btn-primary" onClick={() => setEdit({})}>Add person</button>} />
      <PlatformTabs />
      <p className="text-sm text-gray-500 mb-3">People who work for Finyl or partner with it. They sign in like anyone else and see only the Finyl screens their switches allow, for the DCPs given to them. They never see a DCP's own screens.</p>
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : (
        <div className="card overflow-hidden">
          {!items.length ? <Empty text="No Finyl people yet" /> : (
            <table className="min-w-full text-sm">
              <thead><tr><th className="th">Name</th><th className="th">Role</th><th className="th">Switches</th><th className="th">DCPs</th><th className="th">Status</th><th className="th" /></tr></thead>
              <tbody>
                {items.map((u) => (
                  <tr key={u.id}>
                    <td className="td"><div className="font-semibold">{u.full_name}</div><div className="text-xs text-gray-500">{u.email}</div></td>
                    <td className="td">{u.title || data.presets[u.preset]?.label || u.preset}</td>
                    <td className="td text-xs whitespace-normal max-w-xs">{u.permissions.map((p) => p.replace("platform.", "").replace("_", " ")).join(", ") || "none"}</td>
                    <td className="td text-xs whitespace-normal max-w-xs">{u.all_tenants ? "All DCPs" : u.tenants.join(", ") || "None yet"}</td>
                    <td className="td">{u.active ? <Badge value="active">active</Badge> : <Badge value="closed">off</Badge>}</td>
                    <td className="td text-right"><button className="btn-ghost !py-1 text-xs" onClick={() => setEdit(u)}>Edit</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {edit && data && <PersonModal u={edit} meta={data} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
    </div>
  );
}

function PersonModal({ u, meta, onClose, onSaved }) {
  const { isSuper, platform } = usePlatform();
  const isNew = !u.id;
  const [f, setF] = useState({
    email: u.email || "", full_name: u.full_name || "", phone: u.phone || "", title: u.title || "",
    preset: u.preset && u.preset !== "super_admin" ? u.preset : "director",
    permissions: u.permissions || meta.presets.director.permissions,
    all_tenants: !!u.all_tenants, tenant_ids: u.tenant_ids || [], active: u.active ?? true, password: "" });
  const [err, setErr] = useState("");
  const mine = new Set(platform?.permissions || []);
  const allowed = (p) => isSuper || (mine.has(p) && p !== "platform.users_manage");
  const choosePreset = (preset) => setF({ ...f, preset, permissions: preset === "custom" ? f.permissions : meta.presets[preset].permissions });
  const toggle = (p) => setF({ ...f, preset: "custom",
    permissions: f.permissions.includes(p) ? f.permissions.filter((x) => x !== p) : [...f.permissions, p] });
  const toggleT = (id) => setF({ ...f, tenant_ids: f.tenant_ids.includes(id) ? f.tenant_ids.filter((x) => x !== id) : [...f.tenant_ids, id] });
  const save = async () => {
    setErr("");
    const body = { ...f, password: f.password || null };
    try {
      if (isNew) await api("/api/v1/platform/users", { method: "POST", body });
      else await api(`/api/v1/platform/users/${u.id}`, { method: "PUT", body });
      notify(isNew ? "Person added. Give them the temporary password; they change it when they first sign in." : "Saved", { tone: "success" });
      onSaved();
    } catch (e) { setErr(e.message); }
  };
  return (
    <Modal title={isNew ? "Add a Finyl person" : `Edit ${u.full_name}`} onClose={onClose} wide>
      <div className="space-y-4">
        <ErrorNote text={err} />
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Full name"><input className="input" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
          <Field label="Email (sign-in)"><input className="input" type="email" disabled={!isNew} value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Phone"><input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
          <Field label="Title shown" hint="e.g. Director, Partner — Acme Capital"><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
          <Field label={isNew ? "Temporary password" : "New temporary password (optional)"} hint="At least 10 characters. They must change it at first sign-in.">
            <input className="input" type="text" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
          </Field>
          <Field label="Starting point">
            <select className="input" value={f.preset} onChange={(e) => choosePreset(e.target.value)}>
              {Object.entries(meta.presets).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
            </select>
          </Field>
        </div>
        <div>
          <div className="label">Switches</div>
          <div className="grid sm:grid-cols-2 gap-1.5">
            {Object.entries(meta.catalog).map(([p, label]) => (
              <label key={p} className={`flex items-start gap-2 text-sm ${allowed(p) ? "" : "opacity-40"}`}>
                <input type="checkbox" className="mt-0.5" disabled={!allowed(p)} checked={f.permissions.includes(p)} onChange={() => toggle(p)} />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </div>
        <div>
          <div className="label">DCPs they see</div>
          <label className="flex items-center gap-2 text-sm mb-2">
            <input type="checkbox" checked={f.all_tenants} disabled={!isSuper && !platform?.all_tenants}
              onChange={(e) => setF({ ...f, all_tenants: e.target.checked })} />
            Every DCP (now and future)
          </label>
          {!f.all_tenants && (
            <div className="grid sm:grid-cols-3 gap-1">
              {meta.tenants.map((t) => (
                <label key={t.id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.tenant_ids.includes(t.id)} onChange={() => toggleT(t.id)} />{t.name}</label>
              ))}
            </div>
          )}
        </div>
        {!isNew && (
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} />
            Can sign in (untick to switch them off at once)</label>
        )}
        <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!f.full_name || !f.email || (isNew && f.password.length < 10)} onClick={save}>{isNew ? "Add person" : "Save"}</button></div>
      </div>
    </Modal>
  );
}
