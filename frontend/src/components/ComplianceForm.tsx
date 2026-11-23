import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { fetchZoyaLookup, postCompliance } from "../lib/api";
import type { ComplianceStatus, WatchlistRow } from "../types";

interface ComplianceFormProps {
  token: string;
  rows: WatchlistRow[];
}

export default function ComplianceForm({ token, rows }: ComplianceFormProps) {
  const queryClient = useQueryClient();
  const [instrumentId, setInstrumentId] = useState<number | "">("");
  const [status, setStatus] = useState<ComplianceStatus>("COMPLIANT");
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

  // A SUGGESTION only -- never writes anything. Only pre-fills status/source
  // below; evidence_url is deliberately left for the admin to fill in by hand,
  // since Zoya's API gives no evidence link at all (CLAUDE.md section 5: a
  // human still verifies evidence before Save writes anything).
  const zoyaLookup = useMutation({
    mutationFn: () => {
      if (instrumentId === "") throw new Error("choose an instrument first");
      return fetchZoyaLookup(instrumentId, token);
    },
    onSuccess: (result) => {
      if (result.available && result.status) {
        setStatus(result.status);
        setSource("zoya");
      }
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
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          disabled={instrumentId === "" || zoyaLookup.isPending}
          onClick={() => zoyaLookup.mutate()}
        >
          {zoyaLookup.isPending ? "Looking up..." : "Look up via Zoya"}
        </button>
        <select value={status} onChange={(e) => setStatus(e.target.value as ComplianceStatus)}>
          <option value="COMPLIANT">COMPLIANT</option>
          <option value="NON_COMPLIANT">NON_COMPLIANT</option>
          <option value="QUESTIONABLE">QUESTIONABLE</option>
          <option value="UNRATED">UNRATED</option>
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
          placeholder={
            source === "zoya"
              ? "Zoya gives no evidence link -- add one manually"
              : "Evidence URL (https://...)"
          }
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
      {zoyaLookup.isError && (
        <p className="error-text">{(zoyaLookup.error as Error).message}</p>
      )}
      {zoyaLookup.data && !zoyaLookup.data.available && (
        <p className="subtle">Zoya has no coverage for this symbol.</p>
      )}
      {zoyaLookup.data && zoyaLookup.data.available && (
        <p className={zoyaLookup.data.is_sandbox ? "banner banner-warning" : "subtle"}>
          Zoya suggests <strong>{zoyaLookup.data.status}</strong>
          {zoyaLookup.data.zoya_name && ` for ${zoyaLookup.data.zoya_name}`}
          {zoyaLookup.data.report_date && ` (reported ${zoyaLookup.data.report_date.slice(0, 10)})`}
          {zoyaLookup.data.purification_ratio !== null &&
            `, purification ratio ${zoyaLookup.data.purification_ratio}`}
          . Status and source were pre-filled above -- add real evidence before saving.
          {zoyaLookup.data.is_sandbox && " SANDBOX RESULT: randomized dummy data, not a real ruling."}
        </p>
      )}
      {mutation.isError && (
        <p className="error-text">{(mutation.error as Error).message}</p>
      )}
    </form>
  );
}
