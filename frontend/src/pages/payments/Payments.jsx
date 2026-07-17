// Payments & SMS hub: M-Pesa transaction ledger + SMS dispatch log +
// notification jobs (repayment reminders / overdue alerts) + manual SMS.
import { useEffect, useState } from "react";
import { api, fmtKES } from "../../lib/api";
import { Badge, Empty, Modal, PageHeader, Pagination, Spinner } from "../../components/ui";

const fmtDT = (d) => (d ? new Date(d).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }) : "—");

function TxTable() {
  const [data, setData] = useState(null);
  const [type, setType] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => {
    api(`/api/v1/payments/transactions?type=${type}&page=${page}`).then(setData).catch(() => {});
  }, [type, page]);
  return (
    <>
      <div className="p-3 border-b border-border">
        <select className="input !w-auto" value={type} onChange={(e) => { setType(e.target.value); setPage(1); }}>
          <option value="">All types</option>
          <option value="b2c">B2C disbursement</option>
          <option value="c2b">C2B repayment</option>
          <option value="stk_push">STK push</option>
        </select>
      </div>
      {!data ? <Spinner /> : data.items.length === 0 ? <Empty /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr>
              <th className="th">When</th><th className="th">Type</th><th className="th">Loan</th>
              <th className="th">Amount</th><th className="th">Phone</th><th className="th">M-Pesa Ref</th><th className="th">Status</th>
            </tr></thead>
            <tbody>{data.items.map((t) => (
              <tr key={t.id} className="hover:bg-canvas/60">
                <td className="td">{fmtDT(t.created_at)}</td>
                <td className="td uppercase text-xs font-bold text-gray-500">{t.type.replace("_", " ")}</td>
                <td className="td">#{t.loan_id ?? "—"}</td>
                <td className="td font-semibold">{fmtKES(t.amount)}</td>
                <td className="td">{t.phone}</td>
                <td className="td font-mono text-xs">{t.mpesa_ref}</td>
                <td className="td"><Badge value={t.status} /></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {data && <Pagination page={page} total={data.total} onPage={setPage} />}
    </>
  );
}

function SmsTable({ reloadKey }) {
  const [data, setData] = useState(null);
  const [trigger, setTrigger] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => {
    api(`/api/v1/notifications/sms-logs?trigger_type=${trigger}&page=${page}`).then(setData).catch(() => {});
  }, [trigger, page, reloadKey]);
  return (
    <>
      <div className="p-3 border-b border-border">
        <select className="input !w-auto" value={trigger} onChange={(e) => { setTrigger(e.target.value); setPage(1); }}>
          <option value="">All triggers</option>
          {["loan_approval", "payment_receipt", "repayment_reminder", "overdue_alert", "ticket_resolution", "manual"].map((t) => (
            <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
          ))}
        </select>
      </div>
      {!data ? <Spinner /> : data.items.length === 0 ? <Empty /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr>
              <th className="th">Sent</th><th className="th">Recipient</th><th className="th">Trigger</th>
              <th className="th">Message</th><th className="th">Status</th>
            </tr></thead>
            <tbody>{data.items.map((s) => (
              <tr key={s.id} className="hover:bg-canvas/60">
                <td className="td whitespace-nowrap">{fmtDT(s.sent_at)}</td>
                <td className="td">{s.recipient_phone}</td>
                <td className="td"><Badge value={s.trigger_type}>{s.trigger_type.replace(/_/g, " ")}</Badge></td>
                <td className="td max-w-md"><span className="line-clamp-2 text-gray-600">{s.message}</span></td>
                <td className="td"><Badge value={s.status} /></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {data && <Pagination page={page} total={data.total} onPage={setPage} />}
    </>
  );
}

export default function Payments() {
  const [tab, setTab] = useState("tx");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [smsModal, setSmsModal] = useState(false);
  const [smsForm, setSmsForm] = useState({ phone: "", message: "" });
  const [reloadKey, setReloadKey] = useState(0);

  const runJob = async (path, label) => {
    setBusy(true); setMsg("");
    try {
      const res = await api(path, { method: "POST" });
      setMsg(`${label}: ${JSON.stringify(res)}`);
      setReloadKey((k) => k + 1);
    } catch (e) { setMsg(`${label} failed: ${e.detail}`); }
    finally { setBusy(false); }
  };

  const sendSms = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/api/v1/notifications/send-sms", { method: "POST", body: { ...smsForm, trigger_type: "manual" } });
      setMsg(`Manual SMS dispatched to ${smsForm.phone}.`);
      setSmsModal(false); setSmsForm({ phone: "", message: "" });
      setReloadKey((k) => k + 1); setTab("sms");
    } catch (e2) { setMsg(`SMS failed: ${e2.detail}`); }
    finally { setBusy(false); }
  };

  return (
    <div>
      <PageHeader title="Payments & SMS Hub" crumbs={["Transactions", "Payments"]}
        actions={<>
          <button className="btn-ghost" disabled={busy} onClick={() => runJob("/api/v1/notifications/jobs/run-repayment-reminders", "Repayment reminders")}>⏰ Run reminders</button>
          <button className="btn-ghost" disabled={busy} onClick={() => runJob("/api/v1/notifications/jobs/run-overdue-alerts", "Overdue alerts")}>🚨 Run overdue alerts</button>
          <button className="btn-primary" onClick={() => setSmsModal(true)}>✉ Send SMS</button>
        </>} />

      {msg && <div className="mb-3 text-sm text-teal bg-teal-50 rounded-lg p-3 font-mono">{msg}</div>}

      <div className="card overflow-hidden">
        <div className="flex border-b border-border">
          {[["tx", "M-Pesa Transactions"], ["sms", "SMS Dispatch Log"]].map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)}
              className={`px-5 py-3 text-sm font-semibold border-b-2 -mb-px ${tab === k ? "border-accent text-accent" : "border-transparent text-gray-400 hover:text-charcoal"}`}>
              {label}
            </button>
          ))}
        </div>
        {tab === "tx" ? <TxTable key={reloadKey} /> : <SmsTable reloadKey={reloadKey} />}
      </div>

      {smsModal && (
        <Modal title="Send Manual SMS" onClose={() => setSmsModal(false)}>
          <form onSubmit={sendSms} className="space-y-3">
            <div><label className="label">Phone</label>
              <input className="input" required placeholder="2547XXXXXXXX" value={smsForm.phone}
                onChange={(e) => setSmsForm({ ...smsForm, phone: e.target.value })} /></div>
            <div><label className="label">Message</label>
              <textarea className="input" rows={4} required maxLength={320} value={smsForm.message}
                onChange={(e) => setSmsForm({ ...smsForm, message: e.target.value })} />
              <p className="text-xs text-gray-400 mt-1">{smsForm.message.length}/320 characters</p></div>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setSmsModal(false)}>Cancel</button>
              <button className="btn-primary" disabled={busy}>Send</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
