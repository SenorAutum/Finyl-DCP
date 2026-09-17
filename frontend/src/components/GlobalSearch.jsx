// Command-palette global search. Opens on ⌘K / Ctrl+K (or an external trigger),
// debounces the query (300ms) against GET /api/v1/search, groups hits by entity
// type, supports full keyboard navigation (↑/↓/Enter/Esc) and renders through a
// portal onto document.body so it floats above the whole app shell.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";

const TYPE_META = {
  client: { icon: "👥", label: "Clients" },
  loan: { icon: "📋", label: "Loans" },
  lead: { icon: "🧭", label: "Leads" },
  staff: { icon: "🧑\u200d💼", label: "Staff" },
  product: { icon: "⚙️", label: "Products" },
  command: { icon: "⚡", label: "Commands" },
};

// `open` / `onClose` let a topbar button control the palette; the component also
// self-manages the ⌘K shortcut so it works from anywhere in the app.
export default function GlobalSearch({ open, onClose }) {
  const nav = useNavigate();
  const [internalOpen, setInternalOpen] = useState(false);
  const isOpen = open ?? internalOpen;
  const close = onClose || (() => setInternalOpen(false));

  const [q, setQ] = useState("");
  const [hits, setHits] = useState([]);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  // Global ⌘K / Ctrl+K shortcut.
  useEffect(() => {
    const h = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        open == null ? setInternalOpen(true) : null;
      }
      if (e.key === "Escape" && isOpen) close();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [isOpen, open, close]);

  // Reset + focus each time the palette opens.
  useEffect(() => {
    if (isOpen) {
      setQ(""); setHits([]); setActive(0);
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [isOpen]);

  // Debounced search.
  useEffect(() => {
    if (!isOpen) return;
    if (q.trim().length < 2) { setHits([]); setLoading(false); return; }
    setLoading(true);
    const t = setTimeout(() => {
      api(`/api/v1/search?q=${encodeURIComponent(q.trim())}&limit=20`)
        .then((r) => { setHits(r.hits || []); setActive(0); })
        .catch(() => setHits([]))
        .finally(() => setLoading(false));
    }, 300);
    return () => clearTimeout(t);
  }, [q, isOpen]);

  const go = useCallback((hit) => {
    if (!hit) return;
    close();
    if (hit.route) nav(hit.route);
  }, [close, nav]);

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); go(hits[active]); }
  };

  // Keep the active row scrolled into view.
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  // Group hits by entity type while preserving rank order + a flat index map.
  const groups = useMemo(() => {
    const order = [];
    const byType = {};
    hits.forEach((h, idx) => {
      const t = h.entity_type || "command";
      if (!byType[t]) { byType[t] = []; order.push(t); }
      byType[t].push({ ...h, _idx: idx });
    });
    return order.map((t) => ({ type: t, items: byType[t] }));
  }, [hits]);

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-charcoal/40 backdrop-blur-sm p-4 pt-[10vh] animate-fade-in" onClick={close}>
      <div className="w-full max-w-xl bg-surface border border-border rounded-2xl shadow-card-hover overflow-hidden animate-slide-up"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
          <span className="text-gray-400 text-base leading-none">🔍</span>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search clients, loans, leads or type a command…"
            className="flex-1 bg-transparent text-sm text-charcoal placeholder:text-gray-400 focus:outline-none"
          />
          <kbd className="text-[10px] font-semibold text-gray-400 border border-border rounded px-1.5 py-0.5">ESC</kbd>
        </div>

        <div ref={listRef} className="max-h-[55vh] overflow-y-auto">
          {loading ? (
            <div className="p-6 text-center text-sm text-gray-400">Searching…</div>
          ) : q.trim().length < 2 ? (
            <div className="p-6 text-center text-sm text-gray-400">Type at least 2 characters to search.</div>
          ) : hits.length === 0 ? (
            <div className="p-6 text-center text-sm text-gray-400">No results for “{q}”.</div>
          ) : (
            groups.map((g) => {
              const meta = TYPE_META[g.type] || { icon: "•", label: g.type };
              return (
                <div key={g.type} className="py-1.5">
                  <div className="px-4 py-1 text-[10px] font-bold uppercase tracking-widest text-gray-400">{meta.label}</div>
                  {g.items.map((h) => (
                    <button
                      key={`${g.type}-${h.id}-${h._idx}`}
                      data-idx={h._idx}
                      onMouseEnter={() => setActive(h._idx)}
                      onClick={() => go(h)}
                      className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                        active === h._idx ? "bg-canvas" : "hover:bg-canvas/60"}`}
                    >
                      <span className="text-base leading-none w-5 text-center">{meta.icon}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-semibold truncate">{h.label}</span>
                        {h.sublabel && <span className="block text-[11px] text-gray-400 truncate">{h.sublabel}</span>}
                      </span>
                      <span className="text-[10px] text-gray-300">↵</span>
                    </button>
                  ))}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
