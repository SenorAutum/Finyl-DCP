// WP14 — pieces shared by the accounting (books) screens.
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { api, blobUrl, upload } from "../../lib/api";

export { ErrorNote, Field, Tabs, exportCsv, kes, kes2, num, useLoad } from "../platform/common";

const LINKS = [
  { to: "/books", label: "Overview", perm: "accounting.view", end: true },
  { to: "/books/journals", label: "Journals", perm: "accounting.view" },
  { to: "/books/reports", label: "Financial statements", perm: "accounting.view" },
  { to: "/books/payables", label: "Bills & payments", perm: "accounting.view" },
  { to: "/books/claims", label: "Expense claims", perm: "expenses.submit" },
  { to: "/books/reconciliation", label: "Reconciliation", perm: "accounting.view" },
  { to: "/books/assets", label: "Fixed assets", perm: "accounting.view" },
  { to: "/books/budgets", label: "Budgets", perm: "accounting.view" },
  { to: "/books/close", label: "Month-end close", perm: "accounting.view" },
  { to: "/books/settings", label: "Accounts & settings", perm: "accounting.view" },
];

export function BooksTabs() {
  const { can } = useAuth();
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-border mb-5 -mt-1 print:hidden">
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

// Money with two decimals and no currency (tables of figures).
export const amt = (n) => (n == null || n === "" ? "" : Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
// Negative figures in brackets, the way accountants read them.
export const acc = (n) => (n == null ? "" : Number(n) < 0 ? `(${amt(-n)})` : amt(n));

export function today() {
  const d = new Date(Date.now() + 3 * 3600 * 1000);
  return d.toISOString().slice(0, 10);
}
export function yearStart() { return `${today().slice(0, 4)}-01-01`; }
export function monthStart() { return `${today().slice(0, 7)}-01`; }

// The DCP's accounts (cached for the page).
let _cache = null;
export function useAccounts() {
  const [data, setData] = useState(_cache);
  useEffect(() => {
    let live = true;
    api("/api/v1/books/accounts").then((d) => { _cache = d; if (live) setData(d); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return data;
}
export function forgetAccounts() { _cache = null; }

export function useBranches() {
  const [branches, setBranches] = useState([]);
  useEffect(() => {
    api("/api/v1/books/branches").then((d) => setBranches(d?.branches || [])).catch(() => {});
  }, []);
  return branches;
}

export function AccountSelect({ value, onChange, filter, placeholder = "Choose an account", className = "" }) {
  const data = useAccounts();
  const list = (data?.accounts || []).filter((a) => a.active && (!filter || filter(a)));
  const groups = {};
  list.forEach((a) => { (groups[a.type] = groups[a.type] || []).push(a); });
  const LABEL = { asset: "Assets", liability: "Liabilities", equity: "Equity", income: "Income", expense: "Expenses" };
  return (
    <select className={`input ${className}`} value={value || ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">{placeholder}</option>
      {Object.entries(groups).map(([t, rows]) => (
        <optgroup key={t} label={LABEL[t] || t}>
          {rows.map((a) => <option key={a.id} value={a.id}>{a.code} — {a.name}</option>)}
        </optgroup>
      ))}
    </select>
  );
}

export function MoneyAccountSelect(props) {
  return <AccountSelect {...props} filter={(a) => a.is_cash} placeholder="Bank, M-Pesa or cash account" />;
}

export function DateRange({ from, to, onChange, showFrom = true }) {
  return (
    <div className="flex flex-wrap items-end gap-2">
      {showFrom && (
        <label className="block"><span className="label">From</span>
          <input type="date" className="input !w-auto" value={from || ""} onChange={(e) => onChange({ from: e.target.value, to })} /></label>
      )}
      <label className="block"><span className="label">{showFrom ? "To" : "As at"}</span>
        <input type="date" className="input !w-auto" value={to || ""} onChange={(e) => onChange({ from, to: e.target.value })} /></label>
    </div>
  );
}

export function Attachments({ entity, id, items, onDone, canAdd = true }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const add = async (file) => {
    if (!file) return;
    setBusy(true); setErr("");
    try {
      const fd = new FormData(); fd.append("file", file);
      await upload(`/api/v1/books/attachments/${entity}/${id}`, fd);
      onDone && onDone();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const open = async (a) => {
    const url = await blobUrl(`/api/v1/books/attachments/${a.id}`);
    window.open(url, "_blank", "noopener");
  };
  return (
    <div className="mt-3">
      <div className="label">Attachments (receipts, invoices)</div>
      <div className="flex flex-wrap gap-2 items-center">
        {(items || []).map((a) => (
          <button key={a.id} type="button" className="text-xs px-2 py-1 rounded-lg bg-canvas border border-border hover:border-accent" onClick={() => open(a)}>
            📎 {a.filename}
          </button>
        ))}
        {!items?.length && <span className="text-xs text-gray-400">None yet</span>}
        {canAdd && (
          <label className="text-xs px-2 py-1 rounded-lg border border-dashed border-border cursor-pointer hover:border-accent">
            {busy ? "Uploading…" : "+ Attach a file"}
            <input type="file" className="hidden" accept=".pdf,.jpg,.jpeg,.png,.webp,.xlsx,.csv,.docx"
              onChange={(e) => add(e.target.files?.[0])} disabled={busy} />
          </label>
        )}
      </div>
      {err && <div className="text-xs text-red-600 mt-1">{err}</div>}
    </div>
  );
}

export function Note({ children }) {
  return <div className="rounded-lg bg-canvas border border-border text-xs text-gray-500 px-3 py-2 mb-3">{children}</div>;
}
