import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { postCompliance } from "../lib/api";
import type { ComplianceStatus, WatchlistRow } from "../types";

interface ComplianceFormProps {
  token: string;
  rows: WatchlistRow[];
}

export default function ComplianceForm({ token, rows }: ComplianceFormProps) {
  const queryClient = useQueryClient();
  const [instrumentId, setInstrumentId] = useState<number | "">("");
  const [status, setStatus] = useState<ComplianceStatus>("halal");
  const [source, setSource] = useState<"manual" | "zoya" | "musaffa">("manual");
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [notes, setNotes] = useState("");
  const [expiresAt, setExpiresAt] = useState("");

  const mutation = useMutation({
    mutationFn: () => {
      if (instrumentId === "") throw new Error("choose an instrument first");
      return postCompliance(
        {
          instrument_id: instrumentId,
          status,
          source,
          evidence_url: evidenceUrl || null,
          notes: notes || null,
          checked_at: new Date().toISOString(),
          expires_at: expiresAt ? new Date(`${expiresAt}T00:00:00Z`).toISOString() : null,
        },
        token,
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchlist"] });
      setEvidenceUrl("");
      setNotes("");
      setExpiresAt("");
    },
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        mutation.mutate();
      }}
      className="card"
    >
      <h3 className="card-title">Add compliance record</h3>
      <div className="field-row" style={{ marginBottom: 0 }}>
        <select
          value={instrumentId}
          onChange={(e) => setInstrumentId(e.target.value ? Number(e.target.value) : "")}
        >
          <option value="">Instrument...</option>
          {rows.map((r) => (
            <option key={r.id} value={r.id}>
              {r.symbol} ({r.exchange})
            </option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value as ComplianceStatus)}>
          <option value="halal">halal</option>
          <option value="not_halal">not_halal</option>
          <option value="doubtful">doubtful</option>
          <option value="not_covered">not_covered</option>
        </select>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value as "manual" | "zoya" | "musaffa")}
        >
          <option value="manual">manual</option>
          <option value="zoya">zoya</option>
          <option value="musaffa">musaffa</option>
        </select>
        <input
          type="url"
          placeholder="Evidence URL (https://...)"
          value={evidenceUrl}
          onChange={(e) => setEvidenceUrl(e.target.value)}
          style={{ minWidth: 220 }}
        />
        <input
          type="text"
          placeholder="Notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          Expires
          <input type="date" value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} />
        </label>
        <button type="submit" className="btn btn-primary btn-sm" disabled={mutation.isPending}>
          {mutation.isPending ? "Saving..." : "Save"}
        </button>
      </div>
      {mutation.isError && (
        <p className="error-text">{(mutation.error as Error).message}</p>
      )}
    </form>
  );
}
