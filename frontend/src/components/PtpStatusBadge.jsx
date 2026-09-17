// Promise-to-Pay status pill. Colour-codes the PTP lifecycle statuses used across
// the Collections and Loan screens: pending (amber), honored (green),
// broken (red), partial (blue), rescheduled (teal), cancelled (gray).
const PTP_STYLES = {
  pending: "bg-amber-100 text-amber-700",
  honored: "bg-emerald-100 text-emerald-700",
  broken: "bg-red-100 text-red-700",
  partial: "bg-blue-100 text-blue-700",
  rescheduled: "bg-teal-100 text-teal-700",
  cancelled: "bg-gray-200 text-gray-600",
};

export default function PtpStatusBadge({ status }) {
  const cls = PTP_STYLES[status] || "bg-gray-200 text-gray-600";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold ring-1 ring-inset ring-black/5 ${cls}`}>
      {String(status || "—").replace(/_/g, " ")}
    </span>
  );
}
