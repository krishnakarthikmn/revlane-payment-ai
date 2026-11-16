const PAISE_PER_RUPEE = 100;
const LAKH_MINOR = 100_000 * PAISE_PER_RUPEE;
const CRORE_MINOR = 100 * LAKH_MINOR;

const rupees = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const rupeesAndPaise = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function oneDecimal(minor: number, unitMinor: number): string {
  return (Math.round((minor * 10) / unitMinor) / 10).toFixed(1);
}

/**
 * Formats integer paise for display. Under ₹1 lakh: in full with Indian digit
 * grouping (₹46,200). From ₹1 lakh: lakh with one decimal (₹2.6L). From
 * ₹1 crore: crore with one decimal (₹4.2 Cr).
 */
export function formatInr(minor: number): string {
  if (!Number.isInteger(minor)) {
    throw new TypeError("Money must be integer minor units (paise)");
  }
  const sign = minor < 0 ? "−" : "";
  const abs = Math.abs(minor);
  if (abs >= CRORE_MINOR) return `${sign}₹${oneDecimal(abs, CRORE_MINOR)} Cr`;
  if (abs >= LAKH_MINOR) return `${sign}₹${oneDecimal(abs, LAKH_MINOR)}L`;
  if (abs % PAISE_PER_RUPEE === 0) {
    return `${sign}₹${rupees.format(abs / PAISE_PER_RUPEE)}`;
  }
  return `${sign}₹${rupeesAndPaise.format(abs / PAISE_PER_RUPEE)}`;
}
