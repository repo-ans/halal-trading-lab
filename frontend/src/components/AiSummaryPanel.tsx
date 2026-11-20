import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { fetchAiSummary, postAiSummary } from "../lib/api";
import type { AiSummaryMode } from "../types";

interface AiSummaryPanelProps {
  token: string;
  runId: number;
  paperEngineHasRun: boolean;
}

// Renders report_text as PLAIN TEXT ONLY -- React's default children-text
// escaping, never dangerouslySetInnerHTML, never a markdown-to-HTML step (see
// wording.test.ts's static scan proving neither appears in this file). A
// <script> string in report_text renders as inert literal text, not markup.
export default function AiSummaryPanel({ token, runId, paperEngineHasRun }: AiSummaryPanelProps) {
  const [mode, setMode] = useState<AiSummaryMode>("template");

  const stored = useQuery({
    queryKey: ["ai-summary", runId, mode],
    queryFn: () => fetchAiSummary(runId, mode, token),
    enabled: paperEngineHasRun,
  });

  const generate = useMutation({
    mutationFn: () => postAiSummary(runId, mode, token),
    onSuccess: () => stored.refetch(),
  });

  if (!paperEngineHasRun) {
    return (
      <div>
        <h3>AI summary</h3>
        <p>Run the paper engine first to generate a summary.</p>
      </div>
    );
  }

  const report = generate.data ?? stored.data;

  return (
    <div>
      <h3>AI summary</h3>

      <div style={{ display: "flex", gap: 16, marginBottom: 8 }}>
        <label>
          <input
            type="radio"
            name="ai-summary-mode"
            checked={mode === "template"}
            onChange={() => setMode("template")}
          />{" "}
          Automatic (no AI) -- sends nothing
        </label>
        <label>
          <input
            type="radio"
            name="ai-summary-mode"
            checked={mode === "llm"}
            onChange={() => setMode("llm")}
          />{" "}
          AI-generated -- sends the numbers above to an external service
        </label>
      </div>

      {stored.isLoading && <p>Checking for an existing summary...</p>}
      {stored.isError && <p>Error: {(stored.error as Error).message}</p>}

      <button disabled={generate.isPending} onClick={() => generate.mutate()}>
        {generate.isPending ? "Generating..." : report ? "Regenerate" : "Generate summary"}
      </button>
      {generate.isError && <p style={{ color: "#b02a37" }}>{(generate.error as Error).message}</p>}

      {report && (
        <div style={{ marginTop: 12, border: "1px solid #ddd", borderRadius: 6, padding: 12 }}>
          <h4 style={{ marginTop: 0 }}>{report.heading}</h4>
          {report.fallback_occurred && (
            <p style={{ fontSize: 12, color: "#666" }}>
              AI generation was unavailable or failed -- showing the automatic summary instead.
            </p>
          )}
          <p style={{ whiteSpace: "pre-wrap" }}>{report.report_text}</p>
        </div>
      )}
    </div>
  );
}
