// WP13: the one place error (and success) notices appear, top right, on every
// screen including sign-in. Anything can call notify() from lib/api.js; the API
// client calls it itself when Finyl cannot be reached or fails on its side, and
// shows the reference Finyl's support team can look up.
import { useEffect, useRef, useState } from "react";

const TONES = {
  error: "border-red-200 bg-red-50 text-red-800",
  success: "border-emerald-200 bg-emerald-50 text-emerald-800",
  info: "border-blue-200 bg-blue-50 text-blue-800",
};
const ICONS = { error: "⚠", success: "✓", info: "ℹ" };

export default function Toaster() {
  const [items, setItems] = useState([]);
  const seq = useRef(0);
  const recent = useRef({});

  useEffect(() => {
    const on = (e) => {
      const { message, tone = "error", ref } = e.detail || {};
      if (!message) return;
      // The same failure from several requests at once shows once.
      const now = Date.now();
      if (recent.current[message] && now - recent.current[message] < 4000) return;
      recent.current[message] = now;
      const id = ++seq.current;
      setItems((xs) => [...xs.slice(-3), { id, message, tone, ref }]);
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), tone === "error" ? 10000 : 5000);
    };
    window.addEventListener("finyl:notify", on);
    return () => window.removeEventListener("finyl:notify", on);
  }, []);

  if (!items.length) return null;
  return (
    <div className="fixed top-3 right-3 z-[60] flex flex-col gap-2 w-[min(380px,calc(100vw-24px))]" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`rounded-xl border shadow-card-hover px-4 py-3 text-sm animate-slide-up ${TONES[t.tone] || TONES.info}`}>
          <div className="flex items-start gap-2">
            <span className="font-bold leading-5">{ICONS[t.tone] || ICONS.info}</span>
            <div className="flex-1 min-w-0">
              <div className="leading-5">{t.message}</div>
              {t.ref && (
                <div className="mt-1 text-xs opacity-80">
                  Reference <span className="font-mono font-semibold select-all">{t.ref}</span>
                  <button className="ml-2 underline" onClick={() => navigator.clipboard?.writeText(t.ref)}>copy</button>
                </div>
              )}
            </div>
            <button className="opacity-60 hover:opacity-100 leading-5" aria-label="Close"
              onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))}>×</button>
          </div>
        </div>
      ))}
    </div>
  );
}
