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
        <h3 className="card-title">AI summary</h3>
        <p>Run the paper engine first to generate a summary.</p>
      </div>
    );
  }

  const report = generate.data ?? stored.data;

  return (
    <div>
      <h3 className="card-title">AI summary</h3>

      <div className="segmented" role="radiogroup" style={{ marginBottom: 8 }}>
        <button
          type="button"
          role="radio"
          aria-checked={mode === "template"}
          className={`segmented-option ${mode === "template" ? "active" : ""}`}
          onClick={() => setMode("template")}
        >
          Automatic (no AI)
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={mode === "llm"}
          className={`segmented-option ${mode === "llm" ? "active" : ""}`}
          onClick={() => setMode("llm")}
        >
          AI-generated
        </button>
      </div>
      <p className="subtle" style={{ margin: "0 0 4px" }}>
        Automatic (no AI) -- sends nothing
      </p>
      <p className="subtle" style={{ margin: "0 0 12px" }}>
        AI-generated -- sends the numbers above to an external service
      </p>

      {stored.isLoading && <p>Checking for an existing summary...</p>}
      {stored.isError && <p className="error-text">Error: {(stored.error as Error).message}</p>}

      <button className="btn btn-secondary" disabled={generate.isPending} onClick={() => generate.mutate()}>
        {generate.isPending ? "Generating..." : report ? "Regenerate" : "Generate summary"}
      </button>
      {generate.isError && <p className="error-text">{(generate.error as Error).message}</p>}

      {report && (
        <div className="card card-tight" style={{ marginTop: 14, marginBottom: 0 }}>
          <h4 style={{ marginTop: 0, marginBottom: 8 }}>{report.heading}</h4>
          {report.fallback_occurred && (
            <p className="subtle">
              AI generation was unavailable or failed -- showing the automatic summary instead.
            </p>
          )}
          <p style={{ whiteSpace: "pre-wrap" }}>{report.report_text}</p>
        </div>
      )}
    </div>
  );
}
