// AI Financial Health Agent — slide-in chat panel (tenant_admin / super_admin).
import { useRef, useState, useEffect } from "react";
import { api } from "../lib/api";

const SUGGESTIONS = [
  "Which region has the worst PAR 30 and what should I do?",
  "Which staff are underperforming on net margin?",
  "Are we at risk of breaching complaint SLAs?",
  "Summarise our AML exposure this month.",
];

export default function AiPanel({ onClose }) {
  const [messages, setMessages] = useState([
    { role: "assistant", content: "Hi! I'm your Financial Health Agent. I have a live snapshot of your portfolio — PAR by region/product, staff margins, SLA and AML status. Ask me anything." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const send = async (text) => {
    const q = (text || input).trim();
    if (!q || busy) return;
    setInput("");
    const history = messages;
    setMessages((m) => [...m, { role: "user", content: q }]);
    setBusy(true);
    try {
      const { answer } = await api("/api/v1/ai/chat", { method: "POST", body: { message: q, history } });
      setMessages((m) => [...m, { role: "assistant", content: answer }]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: `⚠ ${e.detail || e.message}` }]);
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="absolute inset-y-0 right-0 w-full sm:w-[440px] bg-surface shadow-2xl flex flex-col">
        <div className="px-5 py-4 bg-charcoal text-white flex items-center justify-between">
          <div>
            <div className="font-bold">✦ AI Financial Health Agent</div>
            <div className="text-[11px] text-gray-400">Grounded in your live portfolio analytics</div>
          </div>
          <button className="text-gray-300 hover:text-white text-xl" onClick={onClose}>×</button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {messages.map((m, i) => (
            <div key={i} className={`max-w-[90%] rounded-xl px-3.5 py-2.5 text-sm whitespace-pre-wrap ${
              m.role === "user" ? "ml-auto bg-accent text-white" : "bg-canvas border border-border"}`}>
              {m.content}
            </div>
          ))}
          {busy && <div className="bg-canvas border border-border rounded-xl px-3.5 py-2.5 text-sm text-gray-400 w-fit">Analysing portfolio…</div>}
          <div ref={endRef} />
        </div>
        {messages.length <= 1 && (
          <div className="px-4 pb-2 flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((s) => (
              <button key={s} className="text-[11px] px-2.5 py-1.5 rounded-full border border-border bg-canvas hover:border-accent text-left" onClick={() => send(s)}>{s}</button>
            ))}
          </div>
        )}
        <div className="p-3 border-t border-border flex gap-2">
          <input className="input" placeholder="Ask about your portfolio…" value={input}
            onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
          <button className="btn-primary" disabled={busy} onClick={() => send()}>Send</button>
        </div>
      </div>
    </div>
  );
}
