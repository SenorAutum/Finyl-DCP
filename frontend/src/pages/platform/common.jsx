// WP13 — pieces shared by the Finyl command-centre pages.
import { useCallback, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { api } from "../../lib/api";

export const num = (n) => (n == null ? "—" : Number(n).toLocaleString("en-KE", { maximumFractionDigits: 0 }));
export const kes = (n) => (n == null ? "—" : `KES ${Number(n).toLocaleString("en-KE", { maximumFractionDigits: 0 })}`);
export const kes2 = (n) => (n == null ? "—" : `KES ${Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
export const pct = (n) => (n == null ? "—" : `${n}%`);
export const COLORS = { disbursed: "#334155", collected: "#10B981", invoiced: "#0D9488", received: "#10B981" };

// Load an endpoint; `reload()` fetches again (after a change).
export function useLoad(path, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try { setData(await api(path)); setError(""); }
    catch (e) { setError(e.message || "Could not load"); }
    finally { setLoading(false); }
  }, [path]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, [reload, ...deps]);
  return { data, error, loading, reload, setData };
}

// The person's command-centre switches (super admin has them all).
export function usePlatform() {
  const { user } = useAuth();
  const perms = user?.platform?.permissions || [];
  const can = (p) => user?.role === "super_admin" || perms.includes(p);
  return { can, platform: user?.platform, isSuper: user?.role === "super_admin" };
}

const LINKS = [
  { to: "/platform", label: "Command centre", perm: "platform.dashboard", end: true },
  { to: "/platform/billing", label: "Billing & revenue", perm: "platform.revenue" },
  { to: "/platform/lending", label: "Loans to DCPs", perm: "platform.lending" },
  { to: "/platform/pipeline", label: "Pipeline", perm: "platform.pipeline" },
  { to: "/platform/alerts", label: "Alerts", perm: "platform.alerts" },
  { to: "/platform/people", label: "Finyl people", perm: "platform.users_manage" },
];

export function PlatformTabs() {
  const { can } = usePlatform();
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-border mb-5 -mt-1">
      {LINKS.filter((l) => can(l.perm)).map((l) => (
        <NavLink key={l.to} to={l.to} end={l.end}
          className={({ isActive }) => `px-3 py-2 text-sm font-semibold whitespace-nowrap border-b-2 -mb-px ${
            isActive ? "border-accent text-charcoal" : "border-transparent text-gray-500 hover:text-charcoal"}`}>
          {l.label}
        </NavLink>
      ))}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="flex gap-1 overflow-x-auto mb-4">
      {tabs.map(([k, label]) => (
        <button key={k} onClick={() => onChange(k)}
          className={`px-3 py-1.5 rounded-lg text-sm font-semibold whitespace-nowrap ${
            value === k ? "bg-charcoal text-white" : "bg-surface border border-border text-gray-600 hover:text-charcoal"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

const BAND = { green: "bg-emerald-100 text-emerald-700", amber: "bg-amber-100 text-amber-700", red: "bg-red-100 text-red-700" };
export function Health({ health, showScore = true }) {
  if (!health) return null;
  return (
    <span title={(health.reasons || []).join("\n") || "No concerns"}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${BAND[health.band] || BAND.amber}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />{showScore ? health.score : health.band}
    </span>
  );
}

const SEV = { critical: "bg-red-100 text-red-700", warning: "bg-amber-100 text-amber-700", info: "bg-blue-100 text-blue-700" };
export function Severity({ value }) {
  return <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold uppercase ${SEV[value] || SEV.info}`}>{value}</span>;
}

// Save rows as a CSV file (opens in Excel).
export function exportCsv(filename, columns, rows) {
  const esc = (v) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [columns.map((c) => esc(c[0])).join(","),
    ...rows.map((r) => columns.map((c) => esc(typeof c[1] === "function" ? c[1](r) : r[c[1]])).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

export function ErrorNote({ text }) {
  return text ? <div className="rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 mb-3">{text}</div> : null;
}

export function Field({ label, children, hint }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-gray-400 mt-1">{hint}</span>}
    </label>
  );
}

export function lastMonth() {
  const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
