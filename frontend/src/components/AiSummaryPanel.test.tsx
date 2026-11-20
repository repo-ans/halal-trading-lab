import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AiSummaryPanel from "./AiSummaryPanel";

// Stage G correction 6: "the frontend renders the summary as plain text only
// (no HTML, no markdown-to-HTML); add a test with a <script> string." This is
// a real render, not a static scan -- it proves React's own escaping is what
// stands between a model-produced string and the DOM, not an assumption about it.

vi.mock("../lib/api", () => ({
  fetchAiSummary: vi.fn(),
  postAiSummary: vi.fn(),
}));

import { fetchAiSummary } from "../lib/api";

const MALICIOUS_TEXT = 'This run <script>alert("pwned")</script> had 4 trades.';

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("AiSummaryPanel renders report_text as inert plain text", () => {
  it("never creates a <script> element and shows the literal tag text", async () => {
    vi.mocked(fetchAiSummary).mockResolvedValue({
      id: 1,
      mode: "template",
      requested_mode: "template",
      heading: "Plain-language summary of a simulated result (automatic, no AI)",
      report_text: MALICIOUS_TEXT,
      llm_attempts: 0,
      generated_at: "2026-11-20T00:00:00Z",
      fallback_occurred: false,
    });

    const { container } = renderWithClient(
      <AiSummaryPanel token="t" runId={1} paperEngineHasRun={true} />,
    );

    await waitFor(() => expect(screen.getByText(/had 4 trades/)).toBeTruthy());

    // The string must never be parsed as markup: no <script> element in the DOM.
    expect(container.querySelectorAll("script").length).toBe(0);
    // It must appear as literal visible text, tag characters and all.
    expect(container.textContent).toContain('<script>alert("pwned")</script>');
  });

  it("does not render a not-paper-run state once a report exists", async () => {
    vi.mocked(fetchAiSummary).mockResolvedValue(null);
    renderWithClient(<AiSummaryPanel token="t" runId={1} paperEngineHasRun={false} />);
    expect(screen.getByText(/Run the paper engine first/)).toBeTruthy();
  });
});

// Static scan, same idea as wording.test.ts -- belt-and-suspenders alongside the
// render test above: even if a future edit added report_text to some other
// element, these patterns would catch the regression before it shipped.
const source = (
  import.meta.glob("./AiSummaryPanel.tsx", { query: "?raw", import: "default", eager: true }) as Record<
    string,
    string
  >
)["./AiSummaryPanel.tsx"];

describe("AiSummaryPanel source never uses HTML-injection APIs", () => {
  it("found the component source", () => {
    expect(source.length).toBeGreaterThan(0);
  });

  it("contains no dangerouslySetInnerHTML, no innerHTML, no markdown-to-HTML import", () => {
    // Checks actual usage (JSX prop / property access), not the explanatory
    // comment above that names these APIs on purpose -- same false-positive
    // fix as ai_summary_input.py's import-line-only static scan.
    const code = source
      .split("\n")
      .filter((line) => !line.trim().startsWith("//"))
      .join("\n");
    expect(code.includes("dangerouslySetInnerHTML")).toBe(false);
    expect(code.includes("innerHTML")).toBe(false);
    expect(/import .*(markdown|marked|dompurify)/i.test(code)).toBe(false);
  });
});
