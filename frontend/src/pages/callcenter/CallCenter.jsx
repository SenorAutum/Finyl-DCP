// Call Center Tracker: interaction log + agent scorecard where
// Collection Efficiency = promises kept (repayment within 3 days of PTP date).
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, fmtDate, fmtKES } from "../../lib/api";
import { Badge, Empty, Modal, PageHeader, Pagination, Spinner } from "../../components/ui";

const fmtDT = (d) => (d ? new Date(d).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }) : "—");

export default function CallCenter() {
  const [tab, setTab] = useState("scorecard");
  const [meta, setMeta] = useState({ outcomes: [], agents: [] });
  const [scorecard, setScorecard] = useState(null);
  const [calls, setCalls] = useState(null);
  const [agentId, setAgentId] = useState(0);
  const [outcome, setOutcome] = useState("");
  const [page, setPage] = useState(1);
  const [logging, setLogging] = useState(false);
  const [borrowers, setBorrowers] = useState([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api("/api/v1/call-center/meta").then(setMeta).catch(() => {});
    api("/api/v1/call-center/scorecard").then(setScorecard).catch(() => {});
    api("/api/v1/lending/borrowers?page_size=100").then((d) => setBorrowers(d.items)).catch(() => {});
  }, []);
  useEffect(() => {
    api(`/api/v1/call-center/calls?agent_id=${agentId}&outcome=${outcome}&page=${page}`).then(setCalls).catch(() => {});
  }, [agentId, outcome, page]);

  const [form, setForm] = useState({ borrower_id: "", duration_seconds: "", call_outcome: "no_answer", promise_to_pay_date: "", promise_amount: "", notes: "" });
  const logCall = async (e) => {
    e.preventDefault(); setErr("");
    try {
      await api("/api/v1/call-center/calls", { method: "POST", body: {
        borrower_id: Number(form.borrower_id), duration_seconds: Number(form.duration_seconds || 0),
        call_outcome: form.call_outcome,
        promise_to_pay_date: form.call_outcome === "promise_to_pay" ? form.promise_to_pay_date || null : null,
        promise_amount: form.call_outcome === "promise_to_pay" && form.promise_amount ? Number(form.promise_amount) : null,
        notes: form.notes || null,
      }});
      setLogging(false);
      setForm({ borrower_id: "", duration_seconds: "", call_outcome: "no_answer", promise_to_pay_date: "", promise_amount: "", notes: "" });
      api(`/api/v1/call-center/calls?agent_id=${agentId}&outcome=${outcome}&page=1`).then(setCalls);
      api("/api/v1/call-center/scorecard").then(setScorecard);
      setPage(1);
    } catch (e2) { setErr(e2.detail); }
  };

  return (
    <div>
      <PageHeader title="Call Center" crumbs={["Engagement", "Call Center"]}
        actions={<button className="btn-primary" onClick={() => setLogging(true)}>+ Log Call</button>} />

      <div className="card overflow-hidden">
        <div className="flex border-b border-border">
          {[["scorecard", "Agent Scorecard"], ["calls", "Call Log"]].map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)}
              className={`px-5 py-3 text-sm font-semibold border-b-2 -mb-px ${tab === k ? "border-accent text-accent" : "border-transparent text-gray-400 hover:text-charcoal"}`}>
              {label}
            </button>
          ))}
        </div>

        {tab === "scorecard" && (
          !scorecard ? <Spinner /> : scorecard.length === 0 ? <Empty text="No agent activity yet" /> : (
            <div className="p-4 space-y-5">
              <div>
                <h4 className="text-sm font-bold mb-1">Collection Efficiency by Agent</h4>
                <p className="text-xs text-gray-400 mb-3">Promises kept ÷ promises made — a promise counts as kept when an M-Pesa repayment lands within 3 days of the promise-to-pay date.</p>
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={scorecard} margin={{ left: -15 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                    <XAxis dataKey="agent_name" tick={{ fontSize: 11 }} interval={0} angle={-15} textAnchor="end" height={50} />
                    <YAxis tick={{ fontSize: 11 }} unit="%" />
                    <Tooltip formatter={(v) => `${v}%`} />
                    <Bar dataKey="collection_efficiency" name="Collection efficiency" fill="#10B981" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr>
                    <th className="th">#</th><th className="th">Agent</th><th className="th">Calls</th>
                    <th className="th">Avg talk time</th><th className="th">Promises</th>
                    <th className="th">Kept</th><th className="th">Collection Efficiency</th>
                  </tr></thead>
                  <tbody>{[...scorecard].sort((a, b) => b.collection_efficiency - a.collection_efficiency).map((a, i) => (
                    <tr key={a.agent_id} className={`hover:bg-canvas/60 ${i === 0 ? "bg-emerald-50/60" : ""}`}>
                      <td className="td font-bold text-gray-400">{i + 1}{i === 0 && " 🏆"}</td>
                      <td className="td font-semibold">{a.agent_name}</td>
                      <td className="td">{a.total_calls}</td>
                      <td className="td">{Math.round(a.avg_talk_time_sec)}s</td>
                      <td className="td">{a.promises}</td>
                      <td className="td">{a.promises_kept}</td>
                      <td className="td">
                        <div className="flex items-center gap-2">
                          <div className="w-24 h-1.5 bg-canvas rounded-full overflow-hidden">
                            <div className="h-full bg-accent" style={{ width: `${a.collection_efficiency}%` }} />
                          </div>
                          <span className="font-bold">{a.collection_efficiency}%</span>
                        </div>
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )
        )}

        {tab === "calls" && (
          <>
            <div className="p-3 border-b border-border flex flex-wrap gap-2">
              <select className="input !w-auto" value={agentId} onChange={(e) => { setAgentId(Number(e.target.value)); setPage(1); }}>
                <option value={0}>All agents</option>
                {meta.agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
              <select className="input !w-auto" value={outcome} onChange={(e) => { setOutcome(e.target.value); setPage(1); }}>
                <option value="">All outcomes</option>
                {meta.outcomes.map((o) => <option key={o} value={o}>{o.replace(/_/g, " ")}</option>)}
              </select>
            </div>
            {!calls ? <Spinner /> : calls.items.length === 0 ? <Empty /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr>
                    <th className="th">When</th><th className="th">Agent</th><th className="th">Borrower</th>
                    <th className="th">Duration</th><th className="th">Outcome</th><th className="th">Promise</th><th className="th">Notes</th>
                  </tr></thead>
                  <tbody>{calls.items.map((c) => (
                    <tr key={c.id} className="hover:bg-canvas/60">
                      <td className="td whitespace-nowrap">{fmtDT(c.call_date)}</td>
                      <td className="td">{c.agent_name}</td>
                      <td className="td">{c.borrower_name}</td>
                      <td className="td">{c.duration_seconds}s</td>
                      <td className="td"><Badge value={c.call_outcome === "promise_to_pay" ? "approved" : c.call_outcome}>{c.call_outcome.replace(/_/g, " ")}</Badge></td>
                      <td className="td">{c.promise_amount ? `${fmtKES(c.promise_amount)} by ${fmtDate(c.promise_to_pay_date)}` : "—"}</td>
                      <td className="td max-w-xs"><span className="line-clamp-1 text-gray-500">{c.notes || "—"}</span></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            {calls && <Pagination page={page} total={calls.total} onPage={setPage} />}
          </>
        )}
      </div>

      {logging && (
        <Modal title="Log Call" onClose={() => setLogging(false)}>
          <form onSubmit={logCall} className="space-y-3">
            {err && <div className="text-sm text-red-600 bg-red-50 rounded-lg p-2">{err}</div>}
            <div><label className="label">Borrower *</label>
              <select className="input" required value={form.borrower_id} onChange={(e) => setForm({ ...form, borrower_id: e.target.value })}>
                <option value="">Select borrower…</option>
                {borrowers.map((b) => <option key={b.id} value={b.id}>{b.full_name} — {b.phone}</option>)}
              </select></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="label">Duration (seconds)</label>
                <input type="number" className="input" value={form.duration_seconds} onChange={(e) => setForm({ ...form, duration_seconds: e.target.value })} /></div>
              <div><label className="label">Outcome</label>
                <select className="input" value={form.call_outcome} onChange={(e) => setForm({ ...form, call_outcome: e.target.value })}>
                  {meta.outcomes.map((o) => <option key={o} value={o}>{o.replace(/_/g, " ")}</option>)}
                </select></div>
            </div>
            {form.call_outcome === "promise_to_pay" && (
              <div className="grid grid-cols-2 gap-3 bg-emerald-50/60 rounded-lg p-3">
                <div><label className="label">Promise date *</label>
                  <input type="date" className="input" required value={form.promise_to_pay_date} onChange={(e) => setForm({ ...form, promise_to_pay_date: e.target.value })} /></div>
                <div><label className="label">Promise amount (KES) *</label>
                  <input type="number" className="input" required value={form.promise_amount} onChange={(e) => setForm({ ...form, promise_amount: e.target.value })} /></div>
              </div>
            )}
            <div><label className="label">Notes</label>
              <textarea className="input" rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></div>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setLogging(false)}>Cancel</button>
              <button className="btn-primary">Save call</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
