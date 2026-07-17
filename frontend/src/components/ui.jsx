// Shared UI primitives: status badges, KPI cards, modal, pagination, empty states.
import { useEffect } from "react";

const STATUS_STYLES = {
  // loans
  active: "bg-emerald-100 text-emerald-700", paid: "bg-emerald-100 text-emerald-700",
  approved: "bg-teal-100 text-teal-700", pending: "bg-gray-200 text-gray-600",
  underwriting: "bg-blue-100 text-blue-700", overdue: "bg-amber-100 text-amber-700",
  defaulted: "bg-red-100 text-red-700", rejected: "bg-gray-300 text-gray-700",
  // complaints
  open: "bg-blue-100 text-blue-700", in_progress: "bg-amber-100 text-amber-700",
  resolved: "bg-emerald-100 text-emerald-700", closed: "bg-gray-200 text-gray-600",
  // misc
  success: "bg-emerald-100 text-emerald-700", failed: "bg-red-100 text-red-700",
  sent: "bg-emerald-100 text-emerald-700", validated: "bg-emerald-100 text-emerald-700",
  draft: "bg-gray-200 text-gray-600", high: "bg-red-100 text-red-700",
  medium: "bg-amber-100 text-amber-700", low: "bg-gray-200 text-gray-600",
};

export function Badge({ value, children }) {
  const cls = STATUS_STYLES[value] || "bg-gray-200 text-gray-600";
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold ${cls}`}>
      {children || String(value || "—").replace(/_/g, " ")}
    </span>
  );
}

export function KpiCard({ label, value, sub, tone = "default" }) {
  const tones = { default: "text-charcoal", good: "text-accent", warn: "text-amber-600", bad: "text-red-600" };
  return (
    <div className="card p-4">
      <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`mt-1 text-2xl font-extrabold ${tones[tone]}`}>{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}

export function Modal({ title, onClose, children, wide }) {
  useEffect(() => {
    const h = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-charcoal/50 p-4 overflow-y-auto" onClick={onClose}>
      <div className={`card w-full ${wide ? "max-w-3xl" : "max-w-lg"} mt-8 mb-8`} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h3 className="font-bold text-lg">{title}</h3>
          <button className="text-gray-400 hover:text-charcoal text-xl leading-none" onClick={onClose}>×</button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Pagination({ page, total, pageSize = 20, onPage }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-between px-3 py-2 text-sm text-gray-500">
      <span>Page {page} of {pages} · {total} records</span>
      <div className="flex gap-1">
        <button className="btn-ghost !py-1 !px-2.5" disabled={page <= 1} onClick={() => onPage(page - 1)}>‹</button>
        <button className="btn-ghost !py-1 !px-2.5" disabled={page >= pages} onClick={() => onPage(page + 1)}>›</button>
      </div>
    </div>
  );
}

export function Empty({ text = "No records found" }) {
  return <div className="p-8 text-center text-sm text-gray-400">{text}</div>;
}

export function Spinner() {
  return <div className="p-10 flex justify-center"><div className="w-7 h-7 border-[3px] border-border border-t-accent rounded-full animate-spin" /></div>;
}

export function PageHeader({ title, crumbs = [], actions }) {
  return (
    <div className="mb-5">
      <div className="text-xs text-gray-400 mb-1 flex gap-1 flex-wrap">
        <span>Home</span>{crumbs.map((c, i) => <span key={i}>/ {c}</span>)}
      </div>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h1 className="text-2xl font-extrabold">{title}</h1>
        {actions && <div className="flex gap-2 flex-wrap">{actions}</div>}
      </div>
    </div>
  );
}
