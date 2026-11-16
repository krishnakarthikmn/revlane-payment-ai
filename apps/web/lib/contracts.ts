import { API_URL } from "./api";

/** One fee contract row, as the API returns it. Rates are basis points, amounts paise. */
export type Contract = {
  id: string;
  provider: string;
  account_ref: string;
  method: string;
  card_network: string | null;
  card_type: string | null;
  rate_bps: number;
  fixed_fee_minor: number;
  tax_rate_bps: number;
  tax_on_refunded_fee: boolean;
  allowed_fee_types: string[];
  effective_from: string;
  effective_to: string | null;
};

export type ContractPayload = Omit<Contract, "id" | "account_ref">;

/** The form's fields, all as the person typed them. */
export type ContractForm = {
  provider: string;
  method: string;
  cardNetwork: string;
  cardType: string;
  ratePercent: string;
  fixedFeeRupees: string;
  taxPercent: string;
  providerKeepsTax: boolean;
  allowedFeeTypes: string;
  effectiveFrom: string;
  effectiveTo: string;
};

export const PROVIDERS = [
  { value: "razorpay", label: "Razorpay" },
  { value: "payu", label: "PayU" },
  { value: "cashfree", label: "Cashfree" },
  { value: "marketplace", label: "Marketplace" },
] as const;

export const METHODS = [
  { value: "upi", label: "UPI" },
  { value: "card", label: "Card" },
  { value: "netbanking", label: "Netbanking" },
  { value: "wallet", label: "Wallet" },
  { value: "commission", label: "Marketplace commission" },
] as const;

export const CARD_NETWORKS = [
  { value: "", label: "Any network" },
  { value: "visa", label: "Visa" },
  { value: "mastercard", label: "Mastercard" },
  { value: "rupay", label: "RuPay" },
  { value: "amex", label: "Amex" },
] as const;

export const CARD_TYPES = [
  { value: "", label: "Credit and debit" },
  { value: "credit", label: "Credit" },
  { value: "debit", label: "Debit" },
] as const;

export const EMPTY_FORM: ContractForm = {
  provider: "razorpay",
  method: "card",
  cardNetwork: "",
  cardType: "",
  ratePercent: "",
  fixedFeeRupees: "0",
  taxPercent: "18",
  providerKeepsTax: false,
  allowedFeeTypes: "payment",
  effectiveFrom: "",
  effectiveTo: "",
};

const TWO_DECIMALS = /^(\d+)(?:\.(\d{1,2}))?$/;

/** "2.15" becomes 215. Digits only, so no float ever holds a rate or an amount. */
function hundredths(text: string): number | null {
  const match = TWO_DECIMALS.exec(text.trim());
  if (!match) return null;
  const value = Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0"));
  return Number.isSafeInteger(value) ? value : null;
}

/** A percentage with up to two decimals, as basis points. Null if it is not one. */
export function percentToBps(text: string): number | null {
  const bps = hundredths(text);
  return bps !== null && bps <= 10_000 ? bps : null;
}

/** Rupees with up to two decimals, as paise. Null if it is not an amount. */
export function rupeesToMinor(text: string): number | null {
  return hundredths(text);
}

function fromHundredths(value: number): string {
  if (!Number.isInteger(value) || value < 0) {
    throw new TypeError("Rates and amounts are whole basis points or paise");
  }
  const whole = Math.floor(value / 100);
  return `${whole}.${String(value % 100).padStart(2, "0")}`;
}

/** 215 becomes "2.15%". */
export function formatRate(bps: number): string {
  return `${fromHundredths(bps)}%`;
}

/** 300 becomes "₹3.00". */
export function formatFixedFee(minor: number): string {
  return `₹${fromHundredths(minor)}`;
}

const LABELS: Record<string, string> = Object.fromEntries(
  [...PROVIDERS, ...METHODS, ...CARD_NETWORKS, ...CARD_TYPES].map((option) => [
    option.value,
    option.label,
  ]),
);

export function label(value: string): string {
  return LABELS[value] ?? value;
}

/** "Card · Amex · Credit", or just "UPI". */
export function describeMethod(contract: Contract): string {
  return [contract.method, contract.card_network, contract.card_type]
    .filter((part): part is string => Boolean(part))
    .map(label)
    .join(" · ");
}

export function toForm(contract: Contract): ContractForm {
  return {
    provider: contract.provider,
    method: contract.method,
    cardNetwork: contract.card_network ?? "",
    cardType: contract.card_type ?? "",
    ratePercent: fromHundredths(contract.rate_bps),
    fixedFeeRupees: fromHundredths(contract.fixed_fee_minor),
    taxPercent: fromHundredths(contract.tax_rate_bps),
    providerKeepsTax: contract.tax_on_refunded_fee,
    allowedFeeTypes: contract.allowed_fee_types.join(", "),
    effectiveFrom: contract.effective_from,
    effectiveTo: contract.effective_to ?? "",
  };
}

export type FormErrors = Partial<Record<keyof ContractForm, string>>;

export type Validated =
  | { ok: true; payload: ContractPayload }
  | { ok: false; errors: FormErrors };

/** Checks the form and turns it into what the API takes. */
export function validate(form: ContractForm): Validated {
  const errors: FormErrors = {};
  const rate = percentToBps(form.ratePercent);
  const fixed = rupeesToMinor(form.fixedFeeRupees);
  const tax = percentToBps(form.taxPercent);
  const feeTypes = form.allowedFeeTypes
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);

  if (rate === null) {
    errors.ratePercent = "Enter a rate from 0 to 100 with up to two decimals, such as 1.86.";
  }
  if (fixed === null) {
    errors.fixedFeeRupees = "Enter an amount in rupees with up to two decimals, or 0.";
  }
  if (tax === null) {
    errors.taxPercent = "Enter the tax rate as a percentage, such as 18.";
  }
  if (feeTypes.length === 0) {
    errors.allowedFeeTypes = "Name at least one fee the contract allows, such as payment.";
  }
  if (!form.effectiveFrom) {
    errors.effectiveFrom = "Choose the date the contract starts.";
  } else if (form.effectiveTo && form.effectiveTo < form.effectiveFrom) {
    errors.effectiveTo = "The end date is before the start date.";
  }
  if (rate === null || fixed === null || tax === null || Object.keys(errors).length > 0) {
    return { ok: false, errors };
  }

  const isCard = form.method === "card";
  return {
    ok: true,
    payload: {
      provider: form.provider,
      method: form.method,
      card_network: isCard && form.cardNetwork ? form.cardNetwork : null,
      card_type: isCard && form.cardType ? form.cardType : null,
      rate_bps: rate,
      fixed_fee_minor: fixed,
      tax_rate_bps: tax,
      tax_on_refunded_fee: form.providerKeepsTax,
      allowed_fee_types: feeTypes,
      effective_from: form.effectiveFrom,
      effective_to: form.effectiveTo || null,
    },
  };
}

export type ApiResult<T> = { ok: true; value: T } | { ok: false; message: string };

type ErrorBody = { error?: { code?: string; message?: string } };

async function call<T>(
  path: string,
  init: RequestInit | undefined,
  fetcher: typeof fetch,
  read: (response: Response) => Promise<T>,
): Promise<ApiResult<T>> {
  try {
    const response = await fetcher(`${API_URL}/fee-contracts${path}`, {
      cache: "no-store",
      credentials: "include",
      ...init,
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as ErrorBody;
      return {
        ok: false,
        message: body.error?.message ?? `The API answered ${response.status}`,
      };
    }
    return { ok: true, value: await read(response) };
  } catch {
    return { ok: false, message: `No answer from ${API_URL}` };
  }
}

/** The calls below never throw: a refusal or an unreachable API is a result. */
export function listContracts(fetcher: typeof fetch = fetch): Promise<ApiResult<Contract[]>> {
  return call("", undefined, fetcher, async (response) => {
    const body = (await response.json()) as { items: Contract[] };
    return body.items;
  });
}

export function saveContract(
  payload: ContractPayload,
  id: string | null,
  fetcher: typeof fetch = fetch,
): Promise<ApiResult<Contract>> {
  return call(
    id ? `/${id}` : "",
    { method: id ? "PUT" : "POST", body: JSON.stringify(payload) },
    fetcher,
    async (response) => (await response.json()) as Contract,
  );
}

export function deleteContract(
  id: string,
  fetcher: typeof fetch = fetch,
): Promise<ApiResult<null>> {
  return call(`/${id}`, { method: "DELETE" }, fetcher, async () => null);
}
