import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { postCsvConfirm, postCsvDryRun } from "../lib/api";
import type { CsvImportResult } from "../types";

export default function CsvImport({ token }: { token: string }) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [dryRunResult, setDryRunResult] = useState<CsvImportResult | null>(null);

  const dryRun = useMutation({
    mutationFn: (f: File) => postCsvDryRun(f, token),
    onSuccess: (result) => setDryRunResult(result),
  });

  const confirm = useMutation({
    mutationFn: (f: File) => postCsvConfirm(f, token),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchlist"] });
      setDryRunResult(null);
      setFile(null);
    },
  });

  return (
    <div style={{ border: "1px solid #ddd", borderRadius: 6, padding: 12, marginBottom: 16 }}>
      <h3 style={{ marginTop: 0 }}>CSV import</h3>
      <p style={{ fontSize: 12, color: "#666" }}>
        Columns: symbol,exchange,status,source,evidence_url,checked_at,expires_at,notes.
        Max 500 rows, 1&nbsp;MB. Unknown instruments are rejected, never created.
      </p>
      <input
        type="file"
        accept=".csv,text/csv"
        onChange={(e) => {
          setFile(e.target.files?.[0] ?? null);
          setDryRunResult(null);
        }}
      />{" "}
      <button
        type="button"
        disabled={!file || dryRun.isPending}
        onClick={() => file && dryRun.mutate(file)}
      >
        {dryRun.isPending ? "Checking..." : "Dry run"}
      </button>

      {dryRunResult && (
        <div style={{ marginTop: 8 }}>
          <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Row</th>
                <th style={{ textAlign: "left" }}>Symbol</th>
                <th style={{ textAlign: "left" }}>Result</th>
              </tr>
            </thead>
            <tbody>
              {dryRunResult.rows.map((r) => (
                <tr key={r.row_number}>
                  <td>{r.row_number}</td>
                  <td>
                    {r.symbol}/{r.exchange}
                  </td>
                  <td style={{ color: r.accepted ? "#0a3622" : "#58151c" }}>
                    {r.accepted ? "accepted" : r.reason}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            type="button"
            disabled={!dryRunResult.all_accepted || confirm.isPending || !file}
            onClick={() => file && confirm.mutate(file)}
          >
            {confirm.isPending ? "Applying..." : "Confirm import"}
          </button>
          {!dryRunResult.all_accepted && (
            <p style={{ color: "#b02a37", fontSize: 12 }}>
              Fix the rejected rows above before confirming -- a confirm applies all rows or
              none.
            </p>
          )}
        </div>
      )}
      {(dryRun.isError || confirm.isError) && (
        <p style={{ color: "#b02a37" }}>{((dryRun.error ?? confirm.error) as Error).message}</p>
      )}
      {confirm.isSuccess && (
        <p style={{ color: "#0a3622" }}>Imported {confirm.data.applied} rows.</p>
      )}
    </div>
  );
}
