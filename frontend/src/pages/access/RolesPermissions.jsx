// Roles & Permissions — EDITABLE per-tenant matrix (super_admin only).
// Toggling a checkbox grants/revokes a single permission on a role for the
// current tenant via POST /access/roles/{role}/permissions; super_admin is
// protected (always all-on, never editable).
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { PageHeader, Spinner, Badge, Modal } from "../../components/ui";

export default function RolesPermissions() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(null);   // `${role}:${perm}` currently saving
  const [showCreate, setShowCreate] = useState(false);
  const [rename, setRename] = useState(null);   // role object being renamed

  const load = () =>
    api("/api/v1/access/roles")
      .then(setData)
      .catch((e) => setErr(e.detail || "Failed to load roles"));

  useEffect(() => { load(); }, []);
  if (err) return <div className="card p-6 text-red-600 text-sm">{err}</div>;
  if (!data) return <Spinner />;

  const perms = data.permissions || [];
  const roles = data.roles || [];
  const held = (role) => new Set(role.wildcard ? perms.map((p) => p.key) : role.permissions);

  async function toggle(role, permKey, next) {
    if (role.protected) return;
    const id = `${role.role}:${permKey}`;
    setSaving(id);
    setErr("");
    // Optimistic update.
    setData((d) => ({
      ...d,
      roles: d.roles.map((r) =>
        r.role === role.role
          ? { ...r, permissions: next
              ? Array.from(new Set([...r.permissions, permKey]))
              : r.permissions.filter((k) => k !== permKey) }
          : r),
    }));
    try {
      await api(`/api/v1/access/roles/${role.role}/permissions`, {
        method: "POST", body: { permission_key: permKey, granted: next },
      });
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : "Save failed");
      await load(); // reconcile with server truth on failure
    } finally {
      setSaving(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Roles & Permissions"
        crumbs={["Administration", "Roles & Permissions"]}
        actions={<button className="btn-primary" onClick={() => setShowCreate(true)}>+ New role</button>}
      />
      <p className="text-sm text-gray-500 mb-4">
        Editable permission matrix for this DCP. Tick a box to grant a permission to a role,
        untick to revoke — changes save immediately and take effect on the user's next request.
        <span className="font-semibold"> Super Admin</span> holds every permission and cannot be edited.
      </p>

      <div className="card overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border bg-canvas">
              <th className="th sticky left-0 bg-canvas text-left min-w-[240px]">Permission</th>
              {roles.map((r) => (
                <th key={r.role} className="th text-center whitespace-nowrap px-2" title={r.role}>
                  <div className="[writing-mode:vertical-rl] rotate-180 mx-auto h-28 font-semibold flex items-center gap-1">
                    <span>{r.label}</span>
                    {!r.protected && (
                      <button className="text-gray-300 hover:text-accent" title="Rename role"
                              onClick={() => setRename(r)}>✎</button>
                    )}
                    {r.custom && <span className="text-[9px] text-teal">custom</span>}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {perms.map((p) => (
              <tr key={p.key} className="border-b border-border/50 hover:bg-canvas/40">
                <td className="td sticky left-0 bg-surface">
                  <div className="font-mono text-[11px] text-teal">{p.key}</div>
                  <div className="text-gray-400 text-[11px]">{p.description}</div>
                </td>
                {roles.map((r) => {
                  const on = held(r).has(p.key);
                  const id = `${r.role}:${p.key}`;
                  return (
                    <td key={r.role} className="td text-center">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-emerald-600 cursor-pointer disabled:cursor-not-allowed"
                        checked={on}
                        disabled={r.protected || saving === id}
                        onChange={(e) => toggle(r, p.key, e.target.checked)}
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {roles.map((r) => (
          <div key={r.role} className="card px-3 py-2 text-xs">
            <span className="font-semibold">{r.label}</span>{" "}
            {r.wildcard
              ? <Badge value="approved">all permissions</Badge>
              : <span className="text-gray-400">{held(r).size} permissions</span>}
          </div>
        ))}
      </div>

      {showCreate && <CreateRoleModal onClose={() => setShowCreate(false)} onSaved={() => { setShowCreate(false); load(); }} />}
      {rename && <RenameRoleModal role={rename} onClose={() => setRename(null)} onSaved={() => { setRename(null); load(); }} />}
    </div>
  );
}

function CreateRoleModal({ onClose, onSaved }) {
  const [roleKey, setRoleKey] = useState("");
  const [label, setLabel] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true); setErr("");
    try {
      await api("/api/v1/access/roles", { method: "POST", body: { role_key: roleKey.trim().toLowerCase(), label: label.trim() } });
      onSaved();
    } catch (e) { setErr(e.detail || "Could not create role"); setBusy(false); }
  }
  return (
    <Modal title="Create custom role" onClose={onClose}>
      <div className="space-y-3">
        <div>
          <label className="text-xs font-semibold text-gray-600">Role key</label>
          <input className="input" placeholder="e.g. field_auditor" value={roleKey}
                 onChange={(e) => setRoleKey(e.target.value)} />
          <p className="text-[11px] text-gray-400 mt-1">Lowercase letters, digits and underscores (3–40 chars). Cannot match a built-in role.</p>
        </div>
        <div>
          <label className="text-xs font-semibold text-gray-600">Display label</label>
          <input className="input" placeholder="Field Auditor" value={label} onChange={(e) => setLabel(e.target.value)} />
        </div>
        {err && <div className="text-red-600 text-sm">{err}</div>}
        <p className="text-[11px] text-gray-400">The new role starts with no permissions — grant them by ticking boxes in the matrix.</p>
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !roleKey.trim() || !label.trim()} onClick={save}>Create</button>
        </div>
      </div>
    </Modal>
  );
}

function RenameRoleModal({ role, onClose, onSaved }) {
  const [label, setLabel] = useState(role.label || "");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true); setErr("");
    try {
      await api(`/api/v1/access/roles/${role.role}`, { method: "PATCH", body: { label: label.trim() } });
      onSaved();
    } catch (e) { setErr(e.detail || "Could not rename role"); setBusy(false); }
  }
  return (
    <Modal title={`Rename “${role.label}”`} onClose={onClose}>
      <div className="space-y-3">
        <div>
          <label className="text-xs font-semibold text-gray-600">Display label</label>
          <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
        </div>
        {err && <div className="text-red-600 text-sm">{err}</div>}
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !label.trim()} onClick={save}>Save</button>
        </div>
      </div>
    </Modal>
  );
}
