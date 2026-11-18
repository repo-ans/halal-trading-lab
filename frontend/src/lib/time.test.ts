import { describe, expect, it } from "vitest";
import { formatBarTime, formatSessionDate } from "./time";

// Both bars are "09:30 local" in America/New_York, but straddle the DST boundary:
// 2026-10-15 is EDT (UTC-4), 2026-11-16 is EST (UTC-5) -- the stored UTC timestamp
// is never shifted; only the DISPLAY timezone changes what clock time it shows as.
const EDT_BAR_UTC = Date.UTC(2026, 9, 15, 13, 30, 0) / 1000; // Oct 15, 13:30 UTC
const EST_BAR_UTC = Date.UTC(2026, 10, 16, 14, 30, 0) / 1000; // Nov 16, 14:30 UTC

describe("formatBarTime", () => {
  it("renders both the EDT and EST bar as 09:30 in America/New_York", () => {
    expect(formatBarTime(EDT_BAR_UTC, "America/New_York")).toBe("09:30");
    expect(formatBarTime(EST_BAR_UTC, "America/New_York")).toBe("09:30");
  });

  it("renders the same two bars as their actual distinct UTC clock times", () => {
    expect(formatBarTime(EDT_BAR_UTC, "UTC")).toBe("13:30");
    expect(formatBarTime(EST_BAR_UTC, "UTC")).toBe("14:30");
  });
});

describe("formatSessionDate", () => {
  it("renders as 'Tue 17 Nov 2026' in the exchange-local timezone", () => {
    const barUtc = Date.UTC(2026, 10, 17, 20, 55, 0) / 1000; // last bar of that session
    expect(formatSessionDate(barUtc, "America/New_York")).toBe("Tue 17 Nov 2026");
  });
});
