import { describe, expect, it } from "vitest";

// Static scan, same idea as backend/tests/test_market_calendar.py's
// test_no_hardcoded_session_times_outside_the_mock_fixture -- Stage F plan
// point 5: no profit claims, no "you would have made". Uses Vite's raw-import
// glob (not node:fs -- this frontend has no @types/node) to read the page
// sources as plain strings at build/test time.
const sources = import.meta.glob("./{StrategyLab,VirtualTrades,Dashboard}.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const BANNED_PHRASES = [
  "you would have made",
  "you earned",
  "you made",
  "guaranteed",
  "profit", // "realized P&L" / "simulated result" instead
];

describe("result screens avoid profit-claim wording", () => {
  const entries = Object.entries(sources);

  it("found all three page files (the glob itself didn't silently match nothing)", () => {
    expect(entries.length).toBe(3);
  });

  for (const [path, source] of entries) {
    const lower = source.toLowerCase();

    it(`${path} contains no banned phrase`, () => {
      for (const phrase of BANNED_PHRASES) {
        expect(lower.includes(phrase), `${path} contains banned phrase "${phrase}"`).toBe(false);
      }
    });

    it(`${path} shows the "Simulated result" heading`, () => {
      expect(lower).toContain("simulated result");
    });
  }
});
