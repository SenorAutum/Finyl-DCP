// WP14 — bank and M-Pesa reconciliation: upload the statement, let the system
// match what it can, then match, book or explain the rest until the difference
// between the statement and the books is zero.
import { useState } from "react";
import { Badge, Empty, Modal, PageHeader, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { api, fmtDate, notify, upload } from "../../lib/api";
import { AccountSelect, BooksTabs, ErrorNote, Field, Note, Tabs, acc, kes2, today, useLoad } from "./common";

export default function Reconciliation() {
  const { data, error, loading, reload } = useLoad("/api/v1/books/reconciliation");
  const [account, setAccount] = useState(null);
  return (
    <div>
      <PageHeader title="Bank & M-Pesa reconciliation" crumbs={["Accounting", "Reconciliation"]} />
      <BooksTabs />
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
          {(data?.accounts || []).map((a) => (
            <button key={a.account_id} onClick={() => setAccount(a)}
              className={`card p-4 text-left hover:border-accent ${account?.account_id === a.account_id ? "border-accent ring-1 ring-accent" : ""}`}>
              <div className="text-xs text-gray-500">{a.code}</div>
              <div className="font-bold">{a.name}</div>
              <div className="text-sm mt-2">Books: <b className="tabnums">{kes2(a.ledger_balance)}</b></div>
              <div className="text-xs text-gray-500">{a.last_statement_to ? `Statement to ${fmtDate(a.last_statement_to)}: ${kes2(a.last_statement_balance)}` : "No statement uploaded"}</div>
              {a.unmatched > 0 && <div className="text-xs text-amber-600 font-semibold mt-1">{a.unmatched} statement lines to match</div>}
            </button>
          ))}
        </div>
      )}
      {!account ? <Empty text="Choose a bank, M-Pesa or cash account above." /> : <AccountRecon key={account.account_id} a={account} onChange={reload} />}
    </div>
  );
}

function AccountRecon({ a, onChange }) {
  const { can } = useAuth();
  const [tab, setTab] = useState("report");
  const [asAt, setAsAt] = useState(today());
  const report = useLoad(`/api/v1/books/reconciliation/${a.account_id}?as_at=${asAt}`);
  const [importing, setImporting] = useState(false);
  const [matching, setMatching] = useState(null);
  const refresh = () => { report.reload(); onChange(); };
  const r = report.data;
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-end gap-3 mb-4">
        <div className="text-lg font-extrabold flex-1">{a.code} {a.name}</div>
        <label className="block"><span className="label">As at</span><input type="date" className="input !w-auto" value={asAt} onChange={(e) => setAsAt(e.target.value)} /></label>
        {can("accounting.post") && <>
          <button className="btn-ghost" onClick={async () => { const x = await api(`/api/v1/books/reconciliation/${a.account_id}/auto-match`, { method: "POST" }); notify(`${x.matched} matched`, { tone: "success" }); refresh(); }}>Match automatically</button>
          <button className="btn-primary" onClick={() => setImporting(true)}>Upload statement</button></>}
      </div>
      <Tabs tabs={[["report", "Reconciliation"], ["lines", "Statement lines"]]} value={tab} onChange={setTab} />
      {tab === "report" && (report.loading && !r ? <Spinner /> : r && (
        <>
          <table className="text-sm mb-4 max-w-xl w-full">
            <tbody>
              <tr><td className="py-1">Balance on the statement {r.statement_date && `(${fmtDate(r.statement_date)})`}</td>
                <td className="text-right tabnums font-semibold">{r.statement_balance == null ? "upload a statement" : kes2(r.statement_balance)}</td></tr>
              <tr><td className="py-1">+ In the books but not yet on the statement</td><td className="text-right tabnums">{acc(r.in_books_not_in_statement_total)}</td></tr>
              <tr><td className="py-1">− On the statement but not yet in the books</td><td className="text-right tabnums">{acc(r.in_statement_not_in_books_total)}</td></tr>
              <tr className="border-t border-border"><td className="py-1 font-semibold">Balance in the books</td><td className="text-right tabnums font-semibold">{kes2(r.ledger_balance)}</td></tr>
              <tr className="border-t-2 border-charcoal"><td className="py-1.5 font-bold">Difference</td>
                <td className={`text-right tabnums font-bold ${r.difference ? "text-red-600" : "text-emerald-700"}`}>{r.difference == null ? "—" : kes2(r.difference)}</td></tr>
            </tbody>
          </table>
          {r.reconciled && <div className="text-sm text-emerald-700 font-semibold mb-3">✓ Reconciled: the statement and the books agree.</div>}
          <div className="grid lg:grid-cols-2 gap-4">
            <div>
              <div className="font-bold mb-1">On the statement, not in the books</div>
              <div className="text-xs text-gray-500 mb-2">Match each to an entry, book it (bank charges, interest), or mark it explained.</div>
              {!r.in_statement_not_in_books.length ? <Empty text="None" /> : (
                <table className="w-full text-sm"><tbody>
                  {r.in_statement_not_in_books.map((x) => (
                    <tr key={x.id}><td className="td whitespace-nowrap">{fmtDate(x.date)}</td><td className="td">{x.description} <span className="text-gray-400">{x.reference}</span></td>
                      <td className="td text-right tabnums">{acc(x.amount)}</td>
                      <td className="td">{can("accounting.post") && <button className="text-xs font-semibold text-accent" onClick={() => setMatching(x)}>Resolve</button>}</td></tr>
                  ))}
                </tbody></table>
              )}
            </div>
            <div>
              <div className="font-bold mb-1">In the books, not on the statement</div>
              <div className="text-xs text-gray-500 mb-2">Usually money still in transit (cheques not yet cleared). Since the first statement {r.since ? fmtDate(r.since) : ""}.</div>
              {!r.in_books_not_in_statement.length ? <Empty text="None" /> : (
                <table className="w-full text-sm"><tbody>
                  {r.in_books_not_in_statement.slice(0, 200).map((x) => (
                    <tr key={x.line_id}><td className="td whitespace-nowrap">{fmtDate(x.date)}</td><td className="td">{x.description} <span className="text-gray-400 font-mono text-xs">{x.number}</span></td>
                      <td className="td text-right tabnums">{acc(x.amount)}</td></tr>
                  ))}
                </tbody></table>
              )}
            </div>
          </div>
        </>
      ))}
      {tab === "lines" && <Lines account={a.account_id} onResolve={setMatching} />}
      {importing && <ImportModal a={a} onClose={() => setImporting(false)} onDone={() => { setImporting(false); refresh(); }} />}
      {matching && <ResolveModal line={matching} onClose={() => setMatching(null)} onDone={() => { setMatching(null); refresh(); }} />}
    </div>
  );
}

function Lines({ account, onResolve }) {
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(`/api/v1/books/reconciliation/${account}/lines${status ? `?status=${status}` : ""}`);
  const unmatch = async (id) => { await api(`/api/v1/books/reconciliation/lines/${id}/unmatch`, { method: "POST" }); reload(); };
  return (
    <>
      <select className="input !w-auto text-sm mb-3" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">All lines</option><option value="unmatched">To match</option><option value="matched">Matched</option><option value="ignored">Explained</option>
      </select>
      <ErrorNote text={error} />
      {loading && !data ? <Spinner /> : !data?.lines?.length ? <Empty text="No statement lines yet." /> : (
        <table className="min-w-full text-sm"><thead><tr><th className="th">Date</th><th className="th">Description</th><th className="th">Reference</th>
          <th className="th text-right">Amount</th><th className="th">Status</th><th className="th" /></tr></thead>
          <tbody>{data.lines.map((l) => (
            <tr key={l.id}><td className="td whitespace-nowrap">{fmtDate(l.date)}</td><td className="td">{l.description}{l.note && <span className="text-gray-400"> · {l.note}</span>}</td>
              <td className="td">{l.reference}</td><td className="td text-right tabnums">{acc(l.amount)}</td>
              <td className="td"><Badge value={l.status === "matched" ? "success" : l.status === "ignored" ? "closed" : "pending"}>{l.status === "ignored" ? "explained" : l.status}</Badge></td>
              <td className="td">{l.status === "unmatched" ? <button className="text-xs font-semibold text-accent" onClick={() => onResolve(l)}>Resolve</button>
                : <button className="text-xs text-gray-500" onClick={() => unmatch(l.id)}>Undo</button>}</td></tr>
          ))}</tbody></table>
      )}
    </>
  );
}

function ImportModal({ a, onClose, onDone }) {
  const [file, setFile] = useState(null);
  const [closing, setClosing] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setErr(""); setBusy(true);
    try {
      const fd = new FormData(); fd.append("file", file);
      if (closing) fd.append("closing_balance", closing);
      const r = await upload(`/api/v1/books/reconciliation/${a.account_id}/import`, fd);
      notify(`${r.lines_added} lines added, ${r.auto_matched} matched automatically${r.duplicates_skipped ? `, ${r.duplicates_skipped} already uploaded` : ""}`, { tone: "success" });
      onDone();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal title={`Upload a statement for ${a.name}`} onClose={onClose}>
      <Note>Download the statement from your bank's internet banking or the M-Pesa business portal as CSV or Excel (.xlsx). Uploading the same
        statement twice is safe: lines already uploaded are skipped.</Note>
      <ErrorNote text={err} />
      <Field label="Statement file"><input type="file" accept=".csv,.xlsx" className="input" onChange={(e) => setFile(e.target.files?.[0])} /></Field>
      <div className="mt-3"><Field label="Closing balance on the statement" hint="Leave empty if the file has a balance column">
        <input type="number" step="0.01" className="input" value={closing} onChange={(e) => setClosing(e.target.value)} /></Field></div>
      <div className="flex justify-end gap-2 mt-4"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={!file || busy} onClick={go}>{busy ? "Reading…" : "Upload"}</button></div>
    </Modal>
  );
}

function ResolveModal({ line, onClose, onDone }) {
  const { data, error } = useLoad(`/api/v1/books/reconciliation/lines/${line.id}/candidates`);
  const [account, setAccount] = useState(null);
  const [desc, setDesc] = useState(line.description || "");
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const post = async (path, body) => {
    setErr("");
    try { await api(`/api/v1/books/reconciliation/lines/${line.id}/${path}`, { method: "POST", body }); notify("Done", { tone: "success" }); onDone(); }
    catch (e) { setErr(e.message); }
  };
  return (
    <Modal title="Resolve a statement line" onClose={onClose} wide>
      <div className="text-sm mb-3"><b>{fmtDate(line.date)}</b> · {line.description} · {line.reference} · <b className="tabnums">{acc(line.amount)}</b></div>
      <ErrorNote text={error || err} />
      <div className="font-bold mb-1">1. Match it to an entry already in the books</div>
      {!data ? <Spinner /> : !data.candidates.length ? <div className="text-sm text-gray-400 mb-3">No unmatched entries within 30 days.</div> : (
        <table className="w-full text-sm mb-4"><tbody>
          {data.candidates.map((c) => (
            <tr key={c.line_id} className={c.same_amount ? "" : "opacity-50"}><td className="td whitespace-nowrap">{fmtDate(c.date)}</td>
              <td className="td">{c.description} <span className="text-gray-400 font-mono text-xs">{c.number}</span></td>
              <td className="td text-right tabnums">{acc(c.amount)}</td>
              <td className="td">{c.same_amount && <button className="text-xs font-semibold text-accent" onClick={() => post("match", { journal_line_id: c.line_id })}>Match</button>}</td></tr>
          ))}
        </tbody></table>
      )}
      <div className="font-bold mb-1">2. Or put it in the books now</div>
      <div className="grid sm:grid-cols-3 gap-3 items-end mb-4">
        <div className="sm:col-span-1"><Field label="Other account"><AccountSelect value={account} onChange={setAccount} filter={(x) => x.id !== line.account_id} /></Field></div>
        <Field label="Description"><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
        <button className="btn-primary" disabled={!account} onClick={() => post("journal", { account_id: account, description: desc })}>Book it</button>
      </div>
      <div className="text-xs text-gray-500 mb-4">Bank charges → 6140 M-Pesa and Bank Charges. Interest the bank paid you → 4090 Other Income.</div>
      <div className="font-bold mb-1">3. Or mark it explained (no entry needed)</div>
      <div className="flex gap-3 items-end">
        <div className="flex-1"><Field label="Explanation"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. reversed by the bank the same day" /></Field></div>
        <button className="btn-ghost" disabled={note.length < 3} onClick={() => post("ignore", { note })}>Mark explained</button>
      </div>
      <div className="text-right mt-4"><button className="btn-ghost" onClick={onClose}>Close</button></div>
    </Modal>
  );
}
