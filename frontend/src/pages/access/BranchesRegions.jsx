// Branches & Regions — view org structure, create regions/branches. org.* gated.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useAuth } from "../../hooks/useAuth";
import { PageHeader, Spinner, Empty, Modal } from "../../components/ui";

export default function BranchesRegions() {
  const { can } = useAuth();
  const [org, setOrg] = useState(null);
  const [modal, setModal] = useState(null); // 'region' | 'branch'
  const [msg, setMsg] = useState("");
  const load = () => api("/api/v1/access/org").then(setOrg).catch((e) => { setMsg(e.detail); setOrg({ regions: [], branches: [], staff: [] }); });
  useEffect(load, []);
  if (!org) return <Spinner />;
  const canManage = can("org.manage");
  const regionName = (id) => org.regions.find((r) => r.id === id)?.name || "—";

  return (
    <div>
      <PageHeader title="Branches & Regions" crumbs={["Administration", "Branches & Regions"]}
        actions={canManage && <>
          <button className="btn-ghost" onClick={() => setModal("region")}>+ Region</button>
          <button className="btn-primary" onClick={() => setModal("branch")}>+ Branch</button>
        </>} />
      {msg && <div className="mb-3 text-sm px-3 py-2 rounded-lg bg-teal-50 text-teal-700 border border-teal-200">{msg}</div>}
      <div className="grid md:grid-cols-2 gap-4">
        <div className="card overflow-hidden">
          <div className="px-4 py-2 font-semibold border-b border-border bg-canvas">Regions ({org.regions.length})</div>
          {org.regions.length === 0 ? <Empty text="No regions" /> :
            org.regions.map((r) => (
              <div key={r.id} className="px-4 py-2 border-b border-border/60 text-sm flex justify-between">
                <span>{r.name}</span>
                <span className="text-gray-400 text-xs">{org.branches.filter((b) => b.region_id === r.id).length} branches</span>
              </div>
            ))}
        </div>
        <div className="card overflow-hidden">
          <div className="px-4 py-2 font-semibold border-b border-border bg-canvas">Branches ({org.branches.length})</div>
          {org.branches.length === 0 ? <Empty text="No branches" /> :
            org.branches.map((b) => (
              <div key={b.id} className="px-4 py-2 border-b border-border/60 text-sm flex justify-between">
                <span>{b.name}</span>
                <span className="text-gray-400 text-xs">{regionName(b.region_id)}</span>
              </div>
            ))}
        </div>
      </div>

      {modal === "region" && <RegionModal onClose={() => setModal(null)} onDone={() => { setModal(null); load(); }} />}
      {modal === "branch" && <BranchModal regions={org.regions} onClose={() => setModal(null)} onDone={() => { setModal(null); load(); }} />}
    </div>
  );
}

function RegionModal({ onClose, onDone }) {
  const [name, setName] = useState(""); const [err, setErr] = useState("");
  const save = async () => { try { await api("/api/v1/access/regions", { method: "POST", body: { name } }); onDone(); } catch (e) { setErr(e.detail); } };
  return (
    <Modal title="New region" onClose={onClose}>
      <label className="label">Region name</label>
      <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
      {err && <div className="text-sm text-red-600 mt-2">{err}</div>}
      <div className="flex justify-end gap-2 pt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={save} disabled={!name}>Save</button></div>
    </Modal>
  );
}

function BranchModal({ regions, onClose, onDone }) {
  const [name, setName] = useState(""); const [region, setRegion] = useState(regions[0]?.id || ""); const [err, setErr] = useState("");
  const save = async () => { try { await api("/api/v1/access/branches", { method: "POST", body: { name, region_id: parseInt(region, 10) } }); onDone(); } catch (e) { setErr(e.detail); } };
  return (
    <Modal title="New branch" onClose={onClose}>
      <label className="label">Branch name</label>
      <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
      <label className="label mt-3">Region</label>
      <select className="input" value={region} onChange={(e) => setRegion(e.target.value)}>
        {regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
      </select>
      {err && <div className="text-sm text-red-600 mt-2">{err}</div>}
      <div className="flex justify-end gap-2 pt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={save} disabled={!name || !region}>Save</button></div>
    </Modal>
  );
}
