// Compose SMS — ad-hoc bulk send to a pasted list of recipients (one per line
// or comma-separated). Posts to POST /api/v1/messaging/compose, which persists
// each message to the SMS log and returns a per-recipient result summary.
import { useMemo, useState } from "react";
import { api } from "../../lib/api";
import { PageHeader, Badge } from "../../components/ui";

const MAX_RECIPIENTS = 1000;
const SMS_SEGMENT = 160;

const parseRecipients = (raw) =>
  Array.from(new Set(
    (raw || "")
      .split(/[\s,;]+/)
      .map((s) => s.trim())
      .filter(Boolean)
  ));

export default function ComposeSms() {
  const [recipientsRaw, setRecipientsRaw] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState("");

  const recipients = useMemo(() => parseRecipients(recipientsRaw), [recipientsRaw]);
  const segments = message.length === 0 ? 0 : Math.ceil(message.length / SMS_SEGMENT);
  const tooMany = recipients.length > MAX_RECIPIENTS;
  const canSend = recipients.length > 0 && message.trim().length > 0 && !tooMany && !busy;

  const send = async () => {
    if (!canSend) return;
    if (!confirm(`Send this SMS to ${recipients.length} recipient(s)?`)) return;
    setBusy(true); setErr(""); setResult(null);
    try {
      const res = await api("/api/v1/messaging/compose", {
        method: "POST",
        body: { recipients, message: message.trim() },
      });
      setResult(res);
    } catch (e) {
      setErr(e.detail || "Failed to send SMS.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader title="Compose SMS" crumbs={["Administration", "Messaging", "Compose"]} />

      {err && <div className="mb-3 text-sm text-red-600 bg-red-50 rounded-lg p-3">{err}</div>}

      {result && (
        <div className="card p-4 mb-4">
          <h3 className="font-bold mb-2">Send summary</h3>
          <div className="flex flex-wrap gap-4 text-sm mb-3">
            <span>Total: <b>{result.total}</b></span>
            <span className="text-accent">Sent: <b>{result.sent}</b></span>
            <span className="text-red-600">Failed: <b>{result.failed}</b></span>
          </div>
          <div className="overflow-x-auto max-h-64 overflow-y-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Recipient</th><th className="th">Status</th><th className="th">Error</th>
              </tr></thead>
              <tbody>
                {(result.results || []).map((r, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="td font-medium">{r.phone}</td>
                    <td className="td"><Badge value={r.status} /></td>
                    <td className="td text-xs text-gray-500">{r.error || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card p-4">
        <div className="mb-4">
          <label className="label">Recipients</label>
          <textarea
            className="input font-mono text-sm min-h-[140px]"
            placeholder={"One number per line (or comma-separated)\n2547XXXXXXXX\n2547YYYYYYYY"}
            value={recipientsRaw}
            onChange={(e) => setRecipientsRaw(e.target.value)}
          />
          <div className={`mt-1 text-xs ${tooMany ? "text-red-600" : "text-gray-500"}`}>
            {recipients.length} unique recipient(s){tooMany ? ` — exceeds max of ${MAX_RECIPIENTS}` : ""}
          </div>
        </div>

        <div className="mb-4">
          <label className="label">Message</label>
          <textarea
            className="input min-h-[120px]"
            placeholder="Type your message…"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          <div className="mt-1 text-xs text-gray-500">
            {message.length} characters · {segments} SMS segment(s)
          </div>
        </div>

        <button className="btn-primary" disabled={!canSend} onClick={send}>
          {busy ? "Sending…" : `Send to ${recipients.length} recipient(s)`}
        </button>
      </div>
    </div>
  );
}
