import { describe, expect, it } from "vitest";

import { formatInr } from "./format";

describe("formatInr", () => {
  it("shows amounts under one lakh in full with Indian grouping", () => {
    expect(formatInr(4_620_000)).toBe("₹46,200");
    expect(formatInr(9_999_900)).toBe("₹99,999");
    expect(formatInr(0)).toBe("₹0");
  });

  it("keeps paise when there are any", () => {
    expect(formatInr(123_456)).toBe("₹1,234.56");
  });

  it("shows lakh with one decimal from one lakh", () => {
    expect(formatInr(10_000_000)).toBe("₹1.0L");
    expect(formatInr(26_000_000)).toBe("₹2.6L");
    expect(formatInr(226_000_000)).toBe("₹22.6L");
  });

  it("shows crore with one decimal from one crore", () => {
    expect(formatInr(4_200_000_000)).toBe("₹4.2 Cr");
  });

  it("refuses floats", () => {
    expect(() => formatInr(2.6)).toThrow(TypeError);
  });
});
