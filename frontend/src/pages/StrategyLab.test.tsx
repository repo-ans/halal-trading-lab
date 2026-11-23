import type { Session } from "@supabase/supabase-js";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import type { Instrument, WatchlistRow } from "../types";
import StrategyLab, { blockedInstrumentsFromError } from "./StrategyLab";

// Bug report: Strategy Lab rendered "API 422: [object Object]" when a run was
// blocked outright, since the server's 422 detail is an object, not a string.
// blockedInstrumentsFromError is the pure piece (easy to test exhaustively);
// the render test below proves the actual page wires it up correctly too.

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    fetchWatchlist: vi.fn(),
    fetchInstruments: vi.fn(),
    createStrategyRun: vi.fn(),
    postPaperRun: vi.fn(),
  };
});

import { createStrategyRun, fetchInstruments, fetchWatchlist } from "../lib/api";

const FAKE_SESSION = { access_token: "t" } as unknown as Session;

const WATCHLIST_ROW: WatchlistRow = {
  id: 2, symbol: "AAPL", exchange: "XNAS", status: null, blocking_reason: "no_record",
  last_checked_at: null, expires_at: null, days_until_expiry: null, latest_bar_ts: null,
  latest_close: null, latest_close_restricted: false, evidence_url: null,
};

const INSTRUMENT: Instrument = {
  id: 2, symbol: "AAPL", exchange: "XNAS", currency: "USD", timezone: "America/New_York",
  latest_bar_ts: null, bar_count: 0, compliance: null,
};

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("blockedInstrumentsFromError (pure)", () => {
  it("extracts blocked_instruments from an all_instruments_blocked ApiError", () => {
    const err = new ApiError(422, {
      error: "all_instruments_blocked", blocked_instruments: { "2": "no_record", "3": "expired" },
    }, "Request failed (422): ...");
    expect(blockedInstrumentsFromError(err)).toEqual({ "2": "no_record", "3": "expired" });
  });

  it("returns null for a different error shape", () => {
    const err = new ApiError(404, "instrument not found", "API 404: instrument not found");
    expect(blockedInstrumentsFromError(err)).toBeNull();
  });

  it("returns null for a plain Error (not an ApiError)", () => {
    expect(blockedInstrumentsFromError(new Error("network down"))).toBeNull();
  });

  it("returns null when detail is an object but not this specific shape", () => {
    const err = new ApiError(500, { weird: true }, "Request failed (500)");
    expect(blockedInstrumentsFromError(err)).toBeNull();
  });
});

describe("StrategyLab renders the all-instruments-blocked error", () => {
  it("shows the instrument's symbol and a plain-language reason, never [object Object], and a working Go to Watchlist button", async () => {
    vi.mocked(fetchWatchlist).mockResolvedValue([WATCHLIST_ROW]);
    vi.mocked(fetchInstruments).mockResolvedValue([INSTRUMENT]);
    vi.mocked(createStrategyRun).mockRejectedValue(
      new ApiError(422, {
        id: 1, status: "failed", error: "all_instruments_blocked",
        blocked_instruments: { "2": "no_record" }, compliance_check_ids: {},
      }, "Request failed (422): failed, all_instruments_blocked"),
    );
    const onGoToWatchlist = vi.fn();

    renderWithClient(
      <StrategyLab session={FAKE_SESSION} onRunReady={vi.fn()} onGoToWatchlist={onGoToWatchlist} />,
    );

    const checkbox = (await screen.findAllByRole("checkbox"))[0];
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: /run strategy/i }));

    await waitFor(() => expect(screen.getByText(/no halal record yet/)).toBeTruthy());
    // AAPL also appears in the watchlist table above -- check it specifically
    // alongside the reason, in the same list item.
    expect(screen.getByText((_, el) => el?.tagName === "LI" && /AAPL/.test(el.textContent ?? "")
      && /no halal record yet/.test(el.textContent ?? ""))).toBeTruthy();
    expect(screen.getByText(/No trades were run/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("[object Object]");

    fireEvent.click(screen.getByRole("button", { name: /go to watchlist/i }));
    expect(onGoToWatchlist).toHaveBeenCalledTimes(1);
  });

  it("falls back to the plain error message for a non-all-blocked failure", async () => {
    vi.mocked(fetchWatchlist).mockResolvedValue([WATCHLIST_ROW]);
    vi.mocked(fetchInstruments).mockResolvedValue([INSTRUMENT]);
    vi.mocked(createStrategyRun).mockRejectedValue(
      new ApiError(500, { oops: "unexpected" }, "Request failed (500): unexpected"),
    );

    renderWithClient(
      <StrategyLab session={FAKE_SESSION} onRunReady={vi.fn()} onGoToWatchlist={vi.fn()} />,
    );

    const checkbox = (await screen.findAllByRole("checkbox"))[0];
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: /run strategy/i }));

    await waitFor(() => expect(screen.getByText(/Request failed \(500\)/)).toBeTruthy());
    expect(document.body.textContent).not.toContain("[object Object]");
  });
});
