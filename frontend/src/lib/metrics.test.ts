import { describe, expect, it } from "vitest";
import { formatMoney, formatPercent } from "./metrics";

describe("formatPercent", () => {
  it("renders null as 'no trades', never 0% or NaN", () => {
    expect(formatPercent(null)).toBe("no trades");
  });

  it("formats a real ratio as a percentage", () => {
    expect(formatPercent("0.5000")).toBe("50.00%");
    expect(formatPercent("-0.0401")).toBe("-4.01%");
    expect(formatPercent("0")).toBe("0.00%"); // a real zero IS defined (e.g. max_drawdown)
  });
});

describe("formatMoney", () => {
  it("renders null as 'no trades', never $0.00", () => {
    expect(formatMoney(null)).toBe("no trades");
  });

  it("formats a real amount with a dollar sign, negatives included", () => {
    expect(formatMoney("12.3400")).toBe("$12.3400");
    expect(formatMoney("-4.0000")).toBe("$-4.0000");
  });
});
