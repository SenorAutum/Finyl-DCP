// User & Access Management — create/edit users, assign roles, lock/unlock,
// reset passwords, scope to branch/region/staff. Gated by users.* permissions.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useAuth } from "../../hooks/useAuth";
import { PageHeader, Badge, Empty, Spinner, Modal } from "../../components/ui";

const EMPTY = { email: "", full_name: "", role: "relationship_officer", password: "",
                branch_id: "", region_id: "", staff_id: "", active: true };

export default function Users() {
  const { can } = useAuth();
  const [rows, setRows] = useState(null);
  const [meta, setMeta] = useState({ assignable_roles: [] });
  const [org, setOrg] = useState({ regions: [], branches: [], staff: [] });
  const [editing, setEditing] = useState(null);
  const [msg, setMsg] = useState("");

  const load = () => {
    api("/api/v1/access/users").then(setRows).catch((e) => { setMsg(e.detail); setRows([]); });
  };
  useEffect(() => {
    load();
    api("/api/v1/access/permissions").then(setMeta).catch(() => {});
    if (can("org.view")) api("/api/v1/access/org").then(setOrg).catch(() => {});
  }, []); // eslint-disable-line

  const toInt = (v) => (v === "" || v == null ? null : parseInt(v, 10));

  const save = async (form) => {
    setMsg("");
    const payload = {
      email: form.email, full_name: form.full_name, role: form.role,
      branch_id: toInt(form.branch_id), region_id: toInt(form.region_id),
      staff_id: toInt(form.staff_id), active: form.active,
    };
    try {
      if (form.id) {
        await api(`/api/v1/access/users/${form.id}`, { method: "PUT", body: payload });
      } else {
        await api("/api/v1/access/users", { method: "POST",
          body: { ...payload, password: form.password || null } });
      }
      setEditing(null); load(); setMsg("Saved");
    } catch (e) { setMsg(e.detail); }
  };

  const setState = async (u, patch) => {
    const q = new URLSearchParams(patch).toString();
    try { await api(`/api/v1/access/users/${u.id}/state?${q}`, { method: "POST" }); load(); }
    catch (e) { setMsg(e.detail); }
  };

  const resetPw = async (u) => {
    const pw = prompt(`New password for ${u.email} (leave blank to force reset on next login):`);
    if (pw === null) return;
    try { await api(`/api/v1/access/users/${u.id}/reset-password`, { method: "POST", body: { password: pw || null } });
      setMsg(pw ? "Password updated" : "Password reset flagged"); }
    catch (e) { setMsg(e.detail); }
  };

  if (!rows) return <Spinner />;
  const canManage = can("users.manage");

  return (
    <div>
      <PageHeader title="User & Access Management" crumbs={["Administration", "Users"]}
        actions={canManage && <button className="btn-primary" onClick={() => setEditing({ ...EMPTY })}>+ New user</button>} />
      {msg && <div className="mb-3 text-sm px-3 py-2 rounded-lg bg-teal-50 text-teal-700 border border-teal-200">{msg}</div>}
      <div className="card overflow-hidden">
        {rows.length === 0 ? <Empty text="No users" /> : (
          <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left border-b border-border bg-canvas">
              <th className="th">User</th><th className="th">Role</th><th className="th">Scope</th>
              <th className="th">Status</th><th className="th text-right">Actions</th>
            </tr></thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id} className="border-b border-border/60">
                  <td className="td">
                    <div className="font-medium">{u.full_name}</div>
                    <div className="text-xs text-gray-400 font-mono">{u.email}</div>
                  </td>
                  <td className="td">{u.role_label}</td>
                  <td className="td text-xs text-gray-500">
                    {u.region_name && <div>Region: {u.region_name}</div>}
                    {u.branch_name && <div>Branch: {u.branch_name}</div>}
                    {u.staff_name && <div>Officer: {u.staff_name}</div>}
                    {!u.region_name && !u.branch_name && !u.staff_name && "Company-wide"}
                  </td>
                  <td className="td space-x-1">
                    <Badge value={u.active ? "active" : "closed"}>{u.active ? "active" : "inactive"}</Badge>
                    {u.is_locked && <Badge value="failed">locked</Badge>}
                    {u.force_password_reset && <Badge value="pending">reset</Badge>}
                  </td>
                  <td className="td text-right space-x-1 whitespace-nowrap">
                    {canManage && <button className="btn-ghost !py-1 !px-2 text-xs" onClick={() => setEditing({
                      id: u.id, email: u.email, full_name: u.full_name, role: u.role,
                      branch_id: u.branch_id || "", region_id: u.region_id || "",
                      staff_id: u.staff_id || "", active: u.active, password: "" })}>Edit</button>}
                    {can("users.lock") && <>
                      <button className="btn-ghost !py-1 !px-2 text-xs" onClick={() => setState(u, { lock: !u.is_locked })}>
                        {u.is_locked ? "Unlock" : "Lock"}</button>
                      <button className="btn-ghost !py-1 !px-2 text-xs" onClick={() => setState(u, { activate: !u.active })}>
                        {u.active ? "Deactivate" : "Activate"}</button>
                      <button className="btn-ghost !py-1 !px-2 text-xs" onClick={() => resetPw(u)}>Reset PW</button>
                    </>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {editing && (
        <UserForm form={editing} meta={meta} org={org} onCancel={() => setEditing(null)} onSave={save} />
      )}
    </div>
  );
}

function UserForm({ form: initial, meta, org, onCancel, onSave }) {
  const [f, setF] = useState(initial);
  const up = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <Modal title={f.id ? "Edit user" : "New user"} onClose={onCancel}>
      <div className="space-y-3">
        <div><label className="label">Full name</label><input className="input" value={f.full_name} onChange={up("full_name")} /></div>
        <div><label className="label">Email</label>
          <input className="input" value={f.email} onChange={up("email")} disabled={!!f.id} /></div>
        <div><label className="label">Role</label>
          <select className="input" value={f.role} onChange={up("role")}>
            {(meta.assignable_roles || []).map((r) => <option key={r.role} value={r.role}>{r.label}</option>)}
          </select></div>
        <div className="grid grid-cols-3 gap-2">
          <div><label className="label">Region</label>
            <select className="input" value={f.region_id} onChange={up("region_id")}>
              <option value="">—</option>
              {(org.regions || []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select></div>
          <div><label className="label">Branch</label>
            <select className="input" value={f.branch_id} onChange={up("branch_id")}>
              <option value="">—</option>
              {(org.branches || []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select></div>
          <div><label className="label">Officer</label>
            <select className="input" value={f.staff_id} onChange={up("staff_id")}>
              <option value="">—</option>
              {(org.staff || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select></div>
        </div>
        {!f.id && <div><label className="label">Password (blank = default {`"Finyl@2026"`} + force reset)</label>
          <input className="input" value={f.password} onChange={up("password")} placeholder="Finyl@2026" /></div>}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={up("active")} /> Active</label>
        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button className="btn-primary" onClick={() => onSave(f)}>Save</button>
        </div>
      </div>
    </Modal>
  );
}
