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

// A matching dot colour per status (softer accent for the optional indicator).
const DOT_STYLES = {
  active: "bg-emerald-500", paid: "bg-emerald-500", approved: "bg-teal-500",
  pending: "bg-gray-400", underwriting: "bg-blue-500", overdue: "bg-amber-500",
  defaulted: "bg-red-500", rejected: "bg-gray-500",
  open: "bg-blue-500", in_progress: "bg-amber-500", resolved: "bg-emerald-500",
  closed: "bg-gray-400", success: "bg-emerald-500", failed: "bg-red-500",
  sent: "bg-emerald-500", validated: "bg-emerald-500", draft: "bg-gray-400",
  high: "bg-red-500", medium: "bg-amber-500", low: "bg-gray-400",
};

// Badge — status pill. `dot` (optional) adds a small leading status dot.
// Backward compatible: existing <Badge value= children= /> callers are unchanged.
export function Badge({ value, children, dot = false }) {
  const cls = STATUS_STYLES[value] || "bg-gray-200 text-gray-600";
  const dotCls = DOT_STYLES[value] || "bg-gray-400";
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ring-1 ring-inset ring-black/5 ${cls}`}>
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${dotCls}`} />}
      {children || String(value || "—").replace(/_/g, " ")}
    </span>
  );
}

// KpiCard — headline metric.
// Original props (label, value, sub, tone) behave exactly as before. New OPTIONAL
// props: `icon` (node), `delta` (string/number chip), `trend` ("up"|"down"|"flat"
// -> colours the delta chip), `variant` ("hero" -> emerald→teal gradient card).
export function KpiCard({ label, value, sub, tone = "default", icon, delta, trend, variant }) {
  const hero = variant === "hero";
  const tones = { default: "text-charcoal", good: "text-accent", warn: "text-amber-600", bad: "text-red-600" };
  const valueColor = hero ? "text-white" : tones[tone];
  const labelColor = hero ? "text-white/80" : "text-gray-500";
  const subColor = hero ? "text-white/70" : "text-gray-400";

  const trendMap = {
    up: hero ? "bg-white/20 text-white" : "bg-emerald-100 text-emerald-700",
    down: hero ? "bg-white/20 text-white" : "bg-red-100 text-red-700",
    flat: hero ? "bg-white/20 text-white" : "bg-gray-200 text-gray-600",
  };
  const trendArrow = trend === "up" ? "↑" : trend === "down" ? "↓" : trend === "flat" ? "→" : "";

  return (
    <div className={`${hero ? "card-hero" : "card"} p-4`}>
      <div className="flex items-start justify-between gap-2">
        <div className={`text-[11px] font-bold uppercase tracking-wider ${labelColor}`}>{label}</div>
        {icon && (
          <span className={`shrink-0 text-lg leading-none ${hero ? "text-white/90" : "text-gray-400"}`}>{icon}</span>
        )}
      </div>
      <div className="mt-1 flex items-baseline gap-2 flex-wrap">
        <div className={`text-2xl font-extrabold tabnums ${valueColor}`}>{value}</div>
        {delta != null && delta !== "" && (
          <span className={`px-1.5 py-0.5 rounded-md text-[11px] font-bold ${trendMap[trend] || trendMap.flat}`}>
            {trendArrow && <span className="mr-0.5">{trendArrow}</span>}{delta}
          </span>
        )}
      </div>
      {sub && <div className={`text-xs mt-0.5 ${subColor}`}>{sub}</div>}
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
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-charcoal/40 backdrop-blur-sm p-4 overflow-y-auto animate-fade-in" onClick={onClose}>
      <div
        className={`w-full ${wide ? "max-w-3xl" : "max-w-lg"} mt-8 mb-8 bg-surface border border-border rounded-2xl shadow-card-hover animate-slide-up`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h3 className="font-bold text-lg">{title}</h3>
          <button className="text-gray-400 hover:text-charcoal text-xl leading-none transition-colors" onClick={onClose}>×</button>
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
      <span className="tabnums">Page {page} of {pages} · {total} records</span>
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

// Skeleton — shimmering placeholder. `className` controls size (default one text
// line). `lines` (optional) renders that many stacked bars for list/table loads.
export function Skeleton({ className = "h-4 w-full", lines = 1 }) {
  if (lines > 1) {
    return (
      <div className="space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <div key={i} className={`skeleton ${className}`} />
        ))}
      </div>
    );
  }
  return <div className={`skeleton ${className}`} />;
}

export function PageHeader({ title, crumbs = [], actions }) {
  return (
    <div className="mb-5">
      <nav className="text-xs text-gray-400 mb-1.5 flex items-center gap-1.5 flex-wrap">
        <span className="font-medium text-gray-500">Home</span>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-1.5">
            <span className="text-gray-300">/</span>
            <span className={i === crumbs.length - 1 ? "text-charcoal font-semibold" : ""}>{c}</span>
          </span>
        ))}
      </nav>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h1 className="text-2xl font-extrabold tracking-tight">{title}</h1>
        {actions && <div className="flex gap-2 flex-wrap">{actions}</div>}
      </div>
    </div>
  );
}
