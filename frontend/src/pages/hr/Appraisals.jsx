// WP15 — performance appraisals: goals with weights and 1–5 scores, shared with
// the employee, who acknowledges with a comment.
import { useState } from "react";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, notify } from "../../lib/api";
import { ErrorNote, Field, HrTabs, useLoad } from "./common";

export default function Appraisals() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useLoad("/api/v1/hr/appraisals");
  const emps = useLoad("/api/v1/hr/employees");
  const [edit, setEdit] = useState(null);
  const [err, setErr] = useState("");
  const save = async (share) => {
    setErr("");
    try {
      const body = { employee_id: Number(edit.employee_id), period: edit.period, goals: edit.goals, strengths: edit.strengths, improvements: edit.improvements };
      const r = await api(edit.id ? `/api/v1/hr/appraisals/${edit.id}` : "/api/v1/hr/appraisals", { method: edit.id ? "PUT" : "POST", body });
      if (share) await api(`/api/v1/hr/appraisals/${edit.id || r.id}/share`, { method: "POST" });
      notify(share ? "Shared with the employee" : "Saved", { tone: "success" }); setEdit(null); reload();
    } catch (e) { setErr(e.message); }
  };
  const setGoal = (i, k, v) => setEdit({ ...edit, goals: edit.goals.map((g, j) => (j === i ? { ...g, [k]: v } : g)) });
  const rows = data?.appraisals || [];
  return (
    <div>
      <PageHeader title="Appraisals" crumbs={["HR", "Appraisals"]}
        actions={can("appraisals.manage") && <button className="btn-primary" onClick={() => setEdit({ employee_id: "", period: "", goals: [{ goal: "", weight: 100, score: "", comment: "" }], strengths: "", improvements: "" })}>New appraisal</button>} />
      <HrTabs />
      <div className="card overflow-hidden">
        <ErrorNote text={error} />
        {loading && !data ? <Spinner /> : !rows.length ? <Empty text="No appraisals yet." /> : (
          <table className="min-w-full text-sm"><thead><tr><th className="th">Employee</th><th className="th">Period</th><th className="th text-right">Score</th><th className="th">Status</th><th className="th">Their comment</th><th className="th" /></tr></thead>
            <tbody>{rows.map((a) => (
              <tr key={a.id}><td className="td font-semibold">{a.employee}</td><td className="td">{a.period}</td><td className="td text-right font-bold">{a.score ?? "—"}</td>
                <td className="td"><Badge value={a.status === "acknowledged" ? "success" : a.status === "shared" ? "approved" : "draft"}>{a.status}</Badge></td>
                <td className="td whitespace-normal text-gray-500">{a.employee_comment}</td>
                <td className="td">{a.status === "draft" && can("appraisals.manage") && <button className="text-xs font-semibold text-accent" onClick={() => setEdit({ ...a })}>Edit</button>}</td></tr>
            ))}</tbody></table>
        )}
      </div>
      {edit && (
        <Modal title={edit.id ? `Appraisal: ${edit.employee}` : "New appraisal"} onClose={() => setEdit(null)} wide>
          <ErrorNote text={err} />
          <div className="grid grid-cols-2 gap-3 mb-3">
            <Field label="Employee"><select className="input" disabled={!!edit.id} value={edit.employee_id} onChange={(e) => setEdit({ ...edit, employee_id: e.target.value })}>
              <option value="">Choose…</option>{(emps.data?.employees || []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="Period"><input className="input" value={edit.period} placeholder="e.g. 2026 H2" onChange={(e) => setEdit({ ...edit, period: e.target.value })} /></Field>
          </div>
          <table className="w-full text-sm"><thead><tr><th className="th">Goal</th><th className="th w-24">Weight %</th><th className="th w-28">Score 1–5</th><th className="th">Comment</th><th className="th w-8" /></tr></thead>
            <tbody>{edit.goals.map((g, i) => (
              <tr key={i}><td className="td"><input className="input" value={g.goal} onChange={(e) => setGoal(i, "goal", e.target.value)} placeholder="e.g. Disburse KES 2m with PAR30 under 5%" /></td>
                <td className="td"><input type="number" className="input" value={g.weight} onChange={(e) => setGoal(i, "weight", e.target.value)} /></td>
                <td className="td"><select className="input" value={g.score ?? ""} onChange={(e) => setGoal(i, "score", e.target.value)}><option value="">—</option>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}</select></td>
                <td className="td"><input className="input" value={g.comment || ""} onChange={(e) => setGoal(i, "comment", e.target.value)} /></td>
                <td className="td">{edit.goals.length > 1 && <button className="text-gray-400" onClick={() => setEdit({ ...edit, goals: edit.goals.filter((_, j) => j !== i) })}>✕</button>}</td></tr>
            ))}</tbody></table>
          <button className="text-sm text-accent font-semibold mt-2" onClick={() => setEdit({ ...edit, goals: [...edit.goals, { goal: "", weight: 0, score: "", comment: "" }] })}>+ Add a goal</button>
          <div className="grid grid-cols-2 gap-3 mt-3">
            <Field label="Strengths"><textarea className="input" rows={3} value={edit.strengths || ""} onChange={(e) => setEdit({ ...edit, strengths: e.target.value })} /></Field>
            <Field label="To improve"><textarea className="input" rows={3} value={edit.improvements || ""} onChange={(e) => setEdit({ ...edit, improvements: e.target.value })} /></Field>
          </div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={() => setEdit(null)}>Cancel</button>
            <button className="btn-ghost" disabled={!edit.employee_id || edit.period.length < 2} onClick={() => save(false)}>Save draft</button>
            <button className="btn-primary" disabled={!edit.employee_id || edit.period.length < 2} onClick={() => save(true)}>Save and share</button></div>
        </Modal>
      )}
    </div>
  );
}
