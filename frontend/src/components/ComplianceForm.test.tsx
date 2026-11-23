import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WatchlistRow } from "../types";
import ComplianceForm from "./ComplianceForm";

// Zoya integration: the lookup is a SUGGESTION only -- it must pre-fill the
// form, never submit anything on its own, and must say plainly when the
// result is sandbox (dummy) data.

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, fetchZoyaLookup: vi.fn(), postCompliance: vi.fn() };
});

import { fetchZoyaLookup, postCompliance } from "../lib/api";

const ROW: WatchlistRow = {
  id: 2, symbol: "AAPL", exchange: "XNAS", status: null, blocking_reason: "no_record",
  last_checked_at: null, expires_at: null, days_until_expiry: null, latest_bar_ts: null,
  latest_close: null, latest_close_restricted: false, evidence_url: null,
};

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function selectInstrument() {
  fireEvent.change(screen.getByDisplayValue("Instrument..."), { target: { value: "2" } });
}

describe("ComplianceForm's Zoya lookup", () => {
  it("pre-fills status/source and clearly labels a sandbox result, without ever submitting", async () => {
    vi.mocked(fetchZoyaLookup).mockResolvedValue({
      available: true, is_sandbox: true, status: "NON_COMPLIANT", purification_ratio: 0.12,
      report_date: "2026-02-18T00:00:00.000Z", zoya_name: "Apple Inc", zoya_exchange: "XNGS",
    });

    renderWithClient(<ComplianceForm token="t" rows={[ROW]} />);
    selectInstrument();
    fireEvent.click(screen.getByRole("button", { name: /look up via zoya/i }));

    await waitFor(() => expect(screen.getByText(/SANDBOX RESULT/)).toBeTruthy());
    expect(screen.getByText(/randomized dummy data, not a real ruling/)).toBeTruthy();
    expect(screen.getByDisplayValue("NON_COMPLIANT")).toBeTruthy();
    expect(screen.getByDisplayValue("zoya")).toBeTruthy();
    expect(postCompliance).not.toHaveBeenCalled();
  });

  it("shows 'no coverage' when Zoya has no report for the symbol, and doesn't change the form", async () => {
    vi.mocked(fetchZoyaLookup).mockResolvedValue({
      available: false, is_sandbox: true, status: null, purification_ratio: null,
      report_date: null, zoya_name: null, zoya_exchange: null,
    });

    renderWithClient(<ComplianceForm token="t" rows={[ROW]} />);
    selectInstrument();
    fireEvent.click(screen.getByRole("button", { name: /look up via zoya/i }));

    await waitFor(() => expect(screen.getByText(/no coverage for this symbol/)).toBeTruthy());
    expect(screen.getByDisplayValue("COMPLIANT")).toBeTruthy(); // untouched default
    expect(screen.getByDisplayValue("manual")).toBeTruthy(); // untouched default
  });

  it("evidence URL placeholder reminds the admin Zoya gives no evidence link", async () => {
    vi.mocked(fetchZoyaLookup).mockResolvedValue({
      available: true, is_sandbox: true, status: "COMPLIANT", purification_ratio: 0,
      report_date: "2026-02-18T00:00:00.000Z", zoya_name: "Apple Inc", zoya_exchange: "XNGS",
    });

    renderWithClient(<ComplianceForm token="t" rows={[ROW]} />);
    selectInstrument();
    fireEvent.click(screen.getByRole("button", { name: /look up via zoya/i }));

    await waitFor(() =>
      expect(screen.getByPlaceholderText(/Zoya gives no evidence link/)).toBeTruthy());
  });
});
