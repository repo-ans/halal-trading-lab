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
    <div className="card">
      <h3 className="card-title">CSV import</h3>
      <p className="subtle">
        Columns: symbol,exchange,status,source,evidence_url,checked_at,expires_at,notes.
        Max 500 rows, 1&nbsp;MB. Unknown instruments are rejected, never created.
      </p>
      <div className="field-row">
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setDryRunResult(null);
          }}
        />
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          disabled={!file || dryRun.isPending}
          onClick={() => file && dryRun.mutate(file)}
        >
          {dryRun.isPending ? "Checking..." : "Dry run"}
        </button>
      </div>

      {dryRunResult && (
        <div>
          <div className="table-wrap" style={{ marginBottom: 12 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Row</th>
                  <th>Symbol</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {dryRunResult.rows.map((r) => (
                  <tr key={r.row_number}>
                    <td>{r.row_number}</td>
                    <td>
                      {r.symbol}/{r.exchange}
                    </td>
                    <td className={r.accepted ? "success-text" : "error-text"}>
                      {r.accepted ? "accepted" : r.reason}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={!dryRunResult.all_accepted || confirm.isPending || !file}
            onClick={() => file && confirm.mutate(file)}
          >
            {confirm.isPending ? "Applying..." : "Confirm import"}
          </button>
          {!dryRunResult.all_accepted && (
            <p className="error-text" style={{ marginTop: 8 }}>
              Fix the rejected rows above before confirming -- a confirm applies all rows or
              none.
            </p>
          )}
        </div>
      )}
      {(dryRun.isError || confirm.isError) && (
        <p className="error-text">{((dryRun.error ?? confirm.error) as Error).message}</p>
      )}
      {confirm.isSuccess && (
        <p className="success-text">Imported {confirm.data.applied} rows.</p>
      )}
    </div>
  );
}
