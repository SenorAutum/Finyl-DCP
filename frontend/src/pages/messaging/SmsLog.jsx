// SMS Log — read-only viewer over the dispatched-message history. Consumes the
// existing reporting endpoint GET /api/v1/integrations/sms/logs (auto-scoped to
// the caller's tenant; super-admin sees every DCP). Filters by delivery status,
// send status and trigger type, with server-side pagination.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Badge, Empty, PageHeader, Pagination, Spinner } from "../../components/ui";

const PAGE_SIZE = 25;
const fmtDT = (d) => (d ? new Date(d).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }) : "—");

export default function SmsLog() {
  const [data, setData] = useState(null);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [delivery, setDelivery] = useState("");
  const [trigger, setTrigger] = useState("");
  const [phone, setPhone] = useState("");
  const [phoneQ, setPhoneQ] = useState("");
  const [err, setErr] = useState("");

  const load = () => {
    setData(null); setErr("");
    const qs = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (status) qs.set("status", status);
    if (delivery) qs.set("delivery_status", delivery);
    if (trigger) qs.set("trigger_type", trigger);
    if (phoneQ) qs.set("phone", phoneQ);
    api(`/api/v1/integrations/sms/logs?${qs.toString()}`)
      .then(setData)
      .catch((e) => { setErr(e.detail || "Failed to load SMS log."); setData({ rows: [], total: 0 }); });
  };
  useEffect(load, [page, status, delivery, trigger, phoneQ]);

  const applyPhone = (e) => { e.preventDefault(); setPage(1); setPhoneQ(phone.trim()); };
  const onFilter = (setter) => (e) => { setPage(1); setter(e.target.value); };

  return (
    <div>
      <PageHeader title="SMS Log" crumbs={["Administration", "Messaging", "SMS Log"]} />

      {err && <div className="mb-3 text-sm text-red-600 bg-red-50 rounded-lg p-3">{err}</div>}

      <div className="card p-3 mb-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="label">Send status</label>
          <select className="input max-w-[150px]" value={status} onChange={onFilter(setStatus)}>
            <option value="">All</option>
            <option value="sent">Sent</option>
            <option value="failed">Failed</option>
            <option value="not_configured">Not configured</option>
          </select>
        </div>
        <div>
          <label className="label">Delivery</label>
          <select className="input max-w-[150px]" value={delivery} onChange={onFilter(setDelivery)}>
            <option value="">All</option>
            <option value="delivered">Delivered</option>
            <option value="undelivered">Undelivered</option>
            <option value="failed">Failed</option>
            <option value="unknown">Unknown</option>
          </select>
        </div>
        <div>
          <label className="label">Trigger</label>
          <select className="input max-w-[170px]" value={trigger} onChange={onFilter(setTrigger)}>
            <option value="">All</option>
            <option value="bulk">Bulk / compose</option>
            <option value="manual">Manual</option>
            <option value="otp">OTP</option>
            <option value="consent">Consent</option>
            <option value="loan_qualified">Loan qualified</option>
            <option value="loan_disbursed">Loan disbursed</option>
            <option value="repayment_reminder">Repayment reminder</option>
            <option value="overdue_alert">Overdue alert</option>
            <option value="defaulted">Defaulted</option>
            <option value="payment_receipt">Payment receipt</option>
            <option value="ptp_reminder">PTP reminder</option>
          </select>
        </div>
        <form onSubmit={applyPhone} className="flex items-end gap-2">
          <div>
            <label className="label">Phone</label>
            <input className="input max-w-[180px]" placeholder="2547XXXXXXXX" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </div>
          <button className="btn-ghost" type="submit">Search</button>
        </form>
      </div>

      <div className="card overflow-hidden">
        {!data ? <Spinner /> : (data.rows || []).length === 0 ? <Empty text="No SMS messages found" /> : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr>
                  <th className="th">Sent</th>
                  <th className="th">Recipient</th>
                  <th className="th">Message</th>
                  <th className="th">Trigger</th>
                  <th className="th">Status</th>
                  <th className="th">Delivery</th>
                </tr></thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.id} className="border-t border-border hover:bg-canvas/60 align-top">
                      <td className="td whitespace-nowrap">{fmtDT(r.sent_at)}</td>
                      <td className="td font-medium whitespace-nowrap">{r.recipient_phone}</td>
                      <td className="td max-w-[360px]"><span className="line-clamp-2" title={r.message}>{r.message}</span></td>
                      <td className="td capitalize whitespace-nowrap">{(r.trigger_type || "—").replace(/_/g, " ")}</td>
                      <td className="td"><Badge value={r.status} /></td>
                      <td className="td"><Badge value={r.delivery_status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={data.page || page} total={data.total || 0} pageSize={PAGE_SIZE} onPage={setPage} />
          </>
        )}
      </div>
    </div>
  );
}
