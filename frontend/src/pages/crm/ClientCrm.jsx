// WP17 — on a client's page: everything that has happened with them in one
// timeline, their open follow-ups, which segments they fall in, and a button to
// log a call or visit.
import { useCallback, useEffect, useState } from "react";
import { Badge } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate } from "../../lib/api";
import { LogContact, Timeline, closeTask, kes } from "./common";

const FILTERS = [["all", "Everything"], ["contact", "Contact"], ["money", "Loans & payments"], ["sms", "SMS"], ["task", "Follow-ups"]];
const IN = { contact: ["interaction", "call", "complaint", "promise"], money: ["loan", "payment"], sms: ["sms"], task: ["task"] };

export default function ClientCrm({ clientId, name }) {
  const { can, user } = useAuth();
  const [d, setD] = useState(null);
  const [logging, setLogging] = useState(false);
  const [filter, setFilter] = useState("all");
  const load = useCallback(() => { api(`/api/v1/crm-plus/clients/${clientId}`).then(setD).catch(() => setD(false)); }, [clientId]);
  useEffect(() => { if (user?.modules?.crm && can("crm.view")) load(); }, [load]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!user?.modules?.crm || !can("crm.view") || d === false) return null;
  const items = (d?.timeline || []).filter((e) => filter === "all" || IN[filter].includes(e.type));
  return (
    <div className="card overflow-hidden mb-5" data-testid="client-crm">
      <div className="px-5 py-3 border-b border-border flex flex-wrap items-center gap-3">
        <span className="font-bold text-base">Relationship</span>
        {d && <>
          <span className="text-xs text-gray-500">{d.figures.loans} loan{d.figures.loans === 1 ? "" : "s"} · {d.figures.repaid} repaid · cycle {d.figures.cycle}
            {d.figures.open > 0 && <> · {kes(d.figures.outstanding)} owed{d.figures.in_arrears && <span className="text-red-600 font-semibold"> (behind)</span>}</>}</span>
          <Badge value={d.marketing_consent ? "approved" : "pending"}>{d.marketing_consent ? "agreed to offers" : "no offers by SMS"}</Badge>
          {d.opted_out && <Badge value="rejected">opted out of SMS</Badge>}
          {d.segments.map((s) => <span key={s} className="text-[11px] font-semibold bg-canvas px-2 py-0.5 rounded-full">{s}</span>)}
        </>}
        <div className="flex-1" />
        {can("crm.log") && <button className="btn-primary !py-1" onClick={() => setLogging(true)}>Log contact</button>}
      </div>
      {d?.tasks?.length > 0 && (
        <div className="px-5 py-2 border-b border-border bg-amber-50/50">
          {d.tasks.map((t) => (
            <div key={t.id} className="flex items-center gap-2 text-sm py-1">
              <span className={t.overdue ? "text-red-600 font-semibold" : ""}>☑ {t.title}</span>
              <span className="text-xs text-gray-500">by {fmtDate(t.due_date)} · {t.assigned_to}</span>
              <div className="flex-1" />
              {can("crm.log") && <button className="text-xs font-semibold text-accent" onClick={() => closeTask(t.id, "done", null, load)}>Done</button>}
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-1 px-4 pt-2 overflow-x-auto">
        {FILTERS.map(([k, v]) => <button key={k} onClick={() => setFilter(k)}
          className={`text-xs font-semibold px-2.5 py-1 rounded-full ${filter === k ? "bg-charcoal text-white" : "bg-canvas text-gray-600"}`}>{v}</button>)}
      </div>
      <div className="max-h-[420px] overflow-y-auto">{d ? <Timeline items={items} /> : <div className="p-4 text-sm text-gray-400">Loading…</div>}</div>
      {logging && <LogContact borrowerId={clientId} name={name} onClose={() => setLogging(false)} onDone={() => { setLogging(false); load(); }} />}
    </div>
  );
}
