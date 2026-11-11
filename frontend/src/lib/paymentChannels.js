// WP12: every way a client can pay, and readable names for how a repayment
// arrived. The server's list (GET /api/v1/payments/channels) is the source of
// truth for the payment forms; this copy labels stored repayment methods.
export const CHANNEL_LABELS = {
  mpesa_paybill: "M-Pesa paybill",
  mpesa_till: "M-Pesa till (Buy Goods)",
  mpesa_pochi: "M-Pesa Pochi la Biashara",
  mpesa_send: "M-Pesa Send Money",
  bank_paybill: "Bank paybill",
  bank_deposit: "Bank deposit",
  bank_eft: "Bank transfer (EFT)",
  bank_rtgs: "Bank transfer (RTGS)",
  pesalink: "PesaLink",
  cheque: "Cheque",
  bank: "Bank",
  cash: "Cash",
  other: "Other",
};

const METHOD_LABELS = {
  mpesa_c2b: "M-Pesa paybill",
  mpesa_till: "M-Pesa till",
  stk_push: "M-Pesa prompt (STK)",
  suspense_allocation: "From suspense",
  bank_notice: "Bank notification",
};

export function methodLabel(method) {
  if (!method) return "—";
  if (METHOD_LABELS[method]) return METHOD_LABELS[method];
  if (method.startsWith("manual_")) {
    const ch = method.slice(7);
    return `${CHANNEL_LABELS[ch] || ch.replace(/_/g, " ")} (recorded by staff)`;
  }
  return method.replace(/_/g, " ");
}
