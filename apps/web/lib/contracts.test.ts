import { describe, expect, it, vi } from "vitest";

import { API_URL } from "./api";
import {
  type Contract,
  type ContractPayload,
  EMPTY_FORM,
  deleteContract,
  describeMethod,
  formatFixedFee,
  formatRate,
  listContracts,
  percentToBps,
  rupeesToMinor,
  saveContract,
  toForm,
  validate,
} from "./contracts";

const amex: Contract = {
  id: "c1",
  provider: "cashfree",
  account_ref: "default",
  method: "card",
  card_network: "amex",
  card_type: null,
  rate_bps: 200,
  fixed_fee_minor: 300,
  tax_rate_bps: 1800,
  tax_on_refunded_fee: false,
  allowed_fee_types: ["payment"],
  effective_from: "2026-01-01",
  effective_to: null,
};

function payloadOf(contract: Contract): ContractPayload {
  return {
    provider: contract.provider,
    method: contract.method,
    card_network: contract.card_network,
    card_type: contract.card_type,
    rate_bps: contract.rate_bps,
    fixed_fee_minor: contract.fixed_fee_minor,
    tax_rate_bps: contract.tax_rate_bps,
    tax_on_refunded_fee: contract.tax_on_refunded_fee,
    allowed_fee_types: contract.allowed_fee_types,
    effective_from: contract.effective_from,
    effective_to: contract.effective_to,
  };
}

describe("rates and amounts", () => {
  it("reads a percentage as whole basis points", () => {
    expect(percentToBps("2.15")).toBe(215);
    expect(percentToBps("2")).toBe(200);
    expect(percentToBps("1.9")).toBe(190);
    expect(percentToBps(" 0 ")).toBe(0);
    expect(percentToBps("100")).toBe(10_000);
    // 0.57 is 56.99999999999999 as a float times 100; the digits give 57.
    expect(percentToBps("0.57")).toBe(57);
  });

  it("refuses what is not a rate", () => {
    for (const text of ["", "2.155", "-1", "100.01", "two", "1e2", "2,15"]) {
      expect(percentToBps(text)).toBeNull();
    }
  });

  it("reads rupees as whole paise", () => {
    expect(rupeesToMinor("3")).toBe(300);
    expect(rupeesToMinor("3.5")).toBe(350);
    expect(rupeesToMinor("0.29")).toBe(29);
    expect(rupeesToMinor("3.505")).toBeNull();
    expect(rupeesToMinor("-3")).toBeNull();
  });

  it("writes them back with two decimals", () => {
    expect(formatRate(215)).toBe("2.15%");
    expect(formatRate(0)).toBe("0.00%");
    expect(formatFixedFee(305)).toBe("₹3.05");
    expect(() => formatRate(2.15)).toThrow(TypeError);
  });
});

describe("the contract form", () => {
  const filled = {
    ...EMPTY_FORM,
    provider: "cashfree",
    cardNetwork: "amex",
    ratePercent: "2.00",
    fixedFeeRupees: "3",
    effectiveFrom: "2026-01-01",
  };

  it("turns a filled form into what the API takes", () => {
    expect(validate(filled)).toEqual({
      ok: true,
      payload: {
        provider: "cashfree",
        method: "card",
        card_network: "amex",
        card_type: null,
        rate_bps: 200,
        fixed_fee_minor: 300,
        tax_rate_bps: 1800,
        tax_on_refunded_fee: false,
        allowed_fee_types: ["payment"],
        effective_from: "2026-01-01",
        effective_to: null,
      },
    });
  });

  it("drops the card fields for a method that is not a card", () => {
    const result = validate({ ...filled, method: "upi", cardType: "credit" });

    expect(result.ok && result.payload.card_network).toBeNull();
    expect(result.ok && result.payload.card_type).toBeNull();
  });

  it("says what is wrong with each field", () => {
    const result = validate({
      ...EMPTY_FORM,
      ratePercent: "2.155",
      fixedFeeRupees: "x",
      taxPercent: "",
      allowedFeeTypes: " , ",
    });

    expect(result.ok).toBe(false);
    expect(Object.keys(result.ok ? {} : result.errors).sort()).toEqual([
      "allowedFeeTypes",
      "effectiveFrom",
      "fixedFeeRupees",
      "ratePercent",
      "taxPercent",
    ]);
  });

  it("refuses an end date before the start date", () => {
    const result = validate({ ...filled, effectiveTo: "2025-12-31" });

    expect(result).toEqual({
      ok: false,
      errors: { effectiveTo: "The end date is before the start date." },
    });
  });

  it("fills the form from a stored contract and back again", () => {
    const form = toForm(amex);

    expect(form.ratePercent).toBe("2.00");
    expect(form.fixedFeeRupees).toBe("3.00");
    const payload = payloadOf(amex);
    expect(validate(form)).toEqual({ ok: true, payload });
  });

  it("describes the method in words", () => {
    expect(describeMethod(amex)).toBe("Card · Amex");
    expect(describeMethod({ ...amex, method: "upi", card_network: null })).toBe("UPI");
  });
});

describe("the fee contract API", () => {
  function respond(status: number, body: unknown): typeof fetch {
    return vi.fn(async () =>
      status === 204 ? new Response(null, { status }) : Response.json(body, { status }),
    ) as typeof fetch;
  }

  it("lists contracts", async () => {
    const fetcher = respond(200, { items: [amex], next_cursor: null });

    expect(await listContracts(fetcher)).toEqual({ ok: true, value: [amex] });
    expect(fetcher).toHaveBeenCalledWith(`${API_URL}/fee-contracts`, expect.anything());
  });

  it("creates with POST and updates with PUT", async () => {
    const payload = payloadOf(amex);
    const create = respond(201, amex);
    const update = respond(200, amex);

    await saveContract(payload, null, create);
    await saveContract(payload, "c1", update);

    expect(create).toHaveBeenCalledWith(
      `${API_URL}/fee-contracts`,
      expect.objectContaining({ method: "POST", body: JSON.stringify(payload) }),
    );
    expect(update).toHaveBeenCalledWith(
      `${API_URL}/fee-contracts/c1`,
      expect.objectContaining({ method: "PUT" }),
    );
  });

  it("deletes", async () => {
    expect(await deleteContract("c1", respond(204, null))).toEqual({ ok: true, value: null });
  });

  it("returns the API's own message when it refuses", async () => {
    const fetcher = respond(409, {
      error: { code: "conflict", message: "Another contract is already in force." },
    });
    const payload = payloadOf(amex);

    expect(await saveContract(payload, null, fetcher)).toEqual({
      ok: false,
      message: "Another contract is already in force.",
    });
  });

  it("says so when the API cannot be reached", async () => {
    const fetcher = vi.fn(async () => {
      throw new TypeError("fetch failed");
    }) as typeof fetch;

    expect(await listContracts(fetcher)).toEqual({
      ok: false,
      message: `No answer from ${API_URL}`,
    });
  });
});
