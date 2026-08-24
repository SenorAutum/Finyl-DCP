// Loan book: filterable list, application modal with the impact-survey gate
// (HTTP 428 IMPACT_SURVEY_REQUIRED → forced survey before resubmitting).
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtKES } from "../../lib/api";
import { Badge, Empty, Modal, PageHeader, Pagination, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";

const STATUSES = ["", "pending", "underwriting", "approved", "active", "overdue", "paid", "defaulted", "rejected"];

// Exact 2-decimal KES for pricing (fmtKES rounds to whole shillings).
const kes2 = (n) =>
  n == null ? "—" : `KES ${Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pctOf = (frac) => (frac == null ? "—" : `${(Number(frac) * 100).toFixed(2)}%`);

// Loan-origination pricing preview. Debounced call to POST /lending/quote as the
// officer picks a product / principal / client. Preview only — never alters submit.
function QuotePreview({ productId, principal, borrowerId }) {
  const [quote, setQuote] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const pid = Number(productId);
    const amt = Number(principal);
    if (!pid || !amt || amt <= 0) { setQuote(null); setErr(""); setLoading(false); return; }
    let cancelled = false;
    setLoading(true);
    const t = setTimeout(() => {
      const body = { product_id: pid, principal: amt };
      if (borrowerId) body.borrower_id = Number(borrowerId);
      api("/api/v1/lending/quote", { method: "POST", body })
        .then((r) => { if (!cancelled) { setQuote(r); setErr(""); } })
        .catch((e) => { if (!cancelled) { setQuote(null); setErr(e.detail || "Could not price this amount."); } })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, 400);
    return () => { cancelled = true; clearTimeout(t); };
  }, [productId, principal, borrowerId]);

  const pid = Number(productId), amt = Number(principal);
  if (!pid || !amt || amt <= 0) return null;

  return (
    <div className="rounded-lg border border-border bg-canvas/60 p-3">
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-xs font-bold uppercase tracking-wider text-gray-500">Pricing preview</div>
        {loading && <span className="text-[11px] text-gray-400">Calculating…</span>}
      </div>
      {err ? (
        <div className="text-xs text-amber-700 bg-amber-50 rounded p-2">{err}</div>
      ) : !quote ? (
        !loading && <div className="text-xs text-gray-400">—</div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <Row label="Interest" value={kes2(quote.quote.interest)} />
            <Row label="Total fees" value={kes2(quote.quote.total_fees)} />
            <Row label="Excise duty" value={`${kes2(quote.quote.excise_duty)} (${pctOf(quote.quote.excise_rate)})`} />
            <Row label="Total cost of credit" value={kes2(quote.quote.total_cost_of_credit)} />
            <Row label="Effective annual rate" value={pctOf(quote.quote.effective_annual_rate)} />
            <Row label="Total repayable" value={<span className="font-bold text-charcoal">{kes2(quote.quote.total_repayable)}</span>} />
          </div>
          {quote.suggested_limit && (
            <div className="mt-2 pt-2 border-t border-border text-xs">
              <span className="text-gray-500">Suggested limit: </span>
              <span className="font-semibold">{kes2(quote.suggested_limit.suggested_limit)}</span>
              {amt > Number(quote.suggested_limit.suggested_limit) && (
                <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 font-bold text-[11px]">
                  ⚠ Principal exceeds suggested limit
                </span>
              )}
              {quote.suggested_limit.rationale && (
                <div className="text-[11px] text-gray-400 mt-0.5">{quote.suggested_limit.rationale}</div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-gray-500">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

function SurveyModal({ borrowerId, onDone, onClose }) {
  // Forced impact survey for 2nd+ loan-cycle applicants.
  const [f, setF] = useState({ monthly_sales_pre: 0, monthly_sales_post: 0, jobs_created: 0, sales_improved: true, next_capital_plan: "" });
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/v1/impact/surveys", { method: "POST", body: {
        borrower_id: borrowerId,
        monthly_sales_pre: Number(f.monthly_sales_pre), monthly_sales_post: Number(f.monthly_sales_post),
        jobs_created: Number(f.jobs_created), sales_improved: !!f.sales_improved,
        next_capital_plan: f.next_capital_plan || null,
      }});
      onDone();
    } catch (e2) { setErr(e2.detail); }
  };
  return (
    <Modal title="Impact Survey Required" onClose={onClose}>
      <p className="text-sm text-gray-500 mb-4">
        This client is applying for a <b>repeat loan cycle</b>. Per policy, an impact survey
        must be captured before the application can proceed.
      </p>
      <form onSubmit={submit} className="space-y-3">
        {err && <div className="text-sm text-red-600 bg-red-50 rounded-lg p-2">{err}</div>}
        <div className="grid grid-cols-2 gap-3">
          <div><label className="label">Monthly sales — before loan (KES)</label>
            <input type="number" className="input" value={f.monthly_sales_pre} onChange={(e) => setF({ ...f, monthly_sales_pre: e.target.value })} /></div>
          <div><label className="label">Monthly sales — now (KES)</label>
            <input type="number" className="input" value={f.monthly_sales_post} onChange={(e) => setF({ ...f, monthly_sales_post: e.target.value })} /></div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div><label className="label">Jobs created</label>
            <input type="number" className="input" value={f.jobs_created} onChange={(e) => setF({ ...f, jobs_created: e.target.value })} /></div>
          <div><label className="label">Sales improved?</label>
            <select className="input" value={f.sales_improved ? "yes" : "no"} onChange={(e) => setF({ ...f, sales_improved: e.target.value === "yes" })}>
              <option value="yes">Yes</option><option value="no">No</option>
            </select></div>
        </div>
        <div><label className="label">Plan for next capital</label>
          <textarea className="input" rows={2} value={f.next_capital_plan} onChange={(e) => setF({ ...f, next_capital_plan: e.target.value })} /></div>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary">Submit survey &amp; continue</button>
        </div>
      </form>
    </Modal>
  );
}

function ApplyModal({ onClose, onCreated }) {
  const [borrowers, setBorrowers] = useState([]);
  const [products, setProducts] = useState([]);
  const [staff, setStaff] = useState([]);
  const [f, setF] = useState({ borrower_id: "", product_id: "", principal: "", staff_id: "" });
  const [bSearch, setBSearch] = useState("");
  const [err, setErr] = useState("");
  const [surveyFor, setSurveyFor] = useState(null); // borrower_id when 428 hit

  useEffect(() => {
    api(`/api/v1/clients?search=${encodeURIComponent(bSearch)}&page_size=50`)
      .then((d) => setBorrowers(d.items)).catch(() => {});
  }, [bSearch]);
  useEffect(() => {
    api("/api/v1/lending/products").then((p) => setProducts(p.filter((x) => x.active))).catch(() => {});
    api("/api/v1/lending/org").then((o) => setStaff(o.staff.filter((s) => s.role === "loan_officer"))).catch(() => {});
  }, []);

  const product = products.find((p) => p.id === Number(f.product_id));

  const submit = async (e) => {
    e?.preventDefault();
    setErr("");
    try {
      await api("/api/v1/lending/loans/apply", { method: "POST", body: {
        borrower_id: Number(f.borrower_id), product_id: Number(f.product_id),
        principal: Number(f.principal), staff_id: f.staff_id ? Number(f.staff_id) : null,
      }});
      onCreated();
    } catch (e2) {
      // 428 Precondition Required → forced impact survey for repeat cycles
      if (e2.status === 428) setSurveyFor(Number(f.borrower_id));
      else setErr(e2.detail);
    }
  };

  if (surveyFor) {
    return <SurveyModal borrowerId={surveyFor}
      onDone={() => { setSurveyFor(null); submit(); }}
      onClose={() => setSurveyFor(null)} />;
  }

  return (
    <Modal title="New Loan Application" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        {err && <div className="text-sm text-red-600 bg-red-50 rounded-lg p-2">{err}</div>}
        <div>
          <label className="label">Client *</label>
          <input className="input mb-1.5" placeholder="Type to filter clients…" value={bSearch} onChange={(e) => setBSearch(e.target.value)} />
          <select className="input" required value={f.borrower_id} onChange={(e) => setF({ ...f, borrower_id: e.target.value })}>
            <option value="">Select client…</option>
            {borrowers.map((b) => <option key={b.id} value={b.id}>{b.full_name} — {b.phone}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Product *</label>
          <select className="input" required value={f.product_id} onChange={(e) => setF({ ...f, product_id: e.target.value })}>
            <option value="">Select product…</option>
            {products.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.interest_rate}% / {p.tenure_value} {p.tenure_unit})</option>)}
          </select>
          {product && <p className="text-xs text-gray-400 mt-1">Range: {fmtKES(product.min_amount)} – {fmtKES(product.max_amount)}</p>}
        </div>
        <div>
          <label className="label">Principal (KES) *</label>
          <input type="number" className="input" required value={f.principal} onChange={(e) => setF({ ...f, principal: e.target.value })} />
        </div>
        <QuotePreview productId={f.product_id} principal={f.principal} borrowerId={f.borrower_id} />
        <div>
          <label className="label">Loan officer</label>
          <select className="input" value={f.staff_id} onChange={(e) => setF({ ...f, staff_id: e.target.value })}>
            <option value="">Unassigned</option>
            {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary">Submit application</button>
        </div>
      </form>
    </Modal>
  );
}

export default function Loans() {
  const { can } = useAuth();
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [applying, setApplying] = useState(false);

  const load = () => {
    api(`/api/v1/lending/loans?status=${status}&search=${encodeURIComponent(search)}&page=${page}`)
      .then(setData).catch(() => {});
  };
  useEffect(load, [status, search, page]);

  return (
    <div>
      <PageHeader title="Loan Book" crumbs={["Lending", "Loans"]}
        actions={can("loans.create") && <button className="btn-primary" onClick={() => setApplying(true)}>+ New Application</button>} />

      <div className="card overflow-hidden">
        <div className="p-3 border-b border-border flex flex-wrap gap-2">
          <select className="input !w-auto" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            {STATUSES.map((s) => <option key={s} value={s}>{s ? s : "All statuses"}</option>)}
          </select>
          <input className="input max-w-xs" placeholder="Search account / client…"
            value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
        </div>
        {!data ? <Spinner /> : data.items.length === 0 ? <Empty /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Account</th><th className="th">Client</th><th className="th">Product</th>
                <th className="th">Principal</th><th className="th">Outstanding</th><th className="th">Status</th>
                <th className="th">Cycle</th><th className="th">Due date</th>
              </tr></thead>
              <tbody>
                {data.items.map((l) => (
                  <tr key={l.id} className="hover:bg-canvas/60">
                    <td className="td">
                      <Link to={`/loans/${l.id}`} className="font-semibold text-teal hover:underline">{l.account_number}</Link>
                    </td>
                    <td className="td">{l.borrower_name}</td>
                    <td className="td">{l.product_name}</td>
                    <td className="td">{fmtKES(l.principal)}</td>
                    <td className="td">{fmtKES(l.outstanding_balance)}</td>
                    <td className="td"><Badge value={l.status} /></td>
                    <td className="td">#{l.loan_cycle_number}</td>
                    <td className="td">{fmtDate(l.due_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && <Pagination page={page} total={data.total} onPage={setPage} />}
      </div>

      {applying && can("loans.create") && (
        <ApplyModal onClose={() => setApplying(false)} onCreated={() => { setApplying(false); load(); }} />
      )}
    </div>
  );
}
