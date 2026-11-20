import type { Session } from "@supabase/supabase-js";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import RunPicker from "../components/RunPicker";
import {
  downloadCsv,
  fetchInstruments,
  fetchPaperOrders,
  fetchStrategyRun,
  postPaperRun,
} from "../lib/api";
import { formatIsoDateTime } from "../lib/time";

interface VirtualTradesProps {
  session: Session;
  runId: number | null;
  onSelectRun: (runId: number | null) => void;
}

const PAGE_SIZE = 25;

export default function VirtualTrades({ session, runId, onSelectRun }: VirtualTradesProps) {
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState("");
  const [instrumentFilter, setInstrumentFilter] = useState<number | "">("");
  const [showUtc, setShowUtc] = useState(false);

  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: () => fetchInstruments(session.access_token),
  });

  const run = useQuery({
    queryKey: ["strategy-run", runId],
    queryFn: () => fetchStrategyRun(runId as number, session.access_token),
    enabled: runId !== null,
  });

  const orders = useQuery({
    queryKey: ["paper-orders", runId, page, statusFilter, instrumentFilter],
    queryFn: () =>
      fetchPaperOrders(runId as number, session.access_token, {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        status: statusFilter || undefined,
        instrumentId: instrumentFilter === "" ? undefined : instrumentFilter,
      }),
    enabled: runId !== null && run.data?.paper_status === "completed",
  });

  const runPaperEngine = useMutation({
    mutationFn: () => postPaperRun(runId as number, session.access_token),
    onSuccess: () => run.refetch(),
  });

  const instrumentsById = new Map((instruments.data ?? []).map((i) => [i.id, i]));
  const timeZone = showUtc ? "UTC" : (instrumentsById.get(instrumentFilter as number)?.timezone ?? "UTC");

  return (
    <div>
      <h2>Virtual Trades</h2>
      <p style={{ fontSize: 12, color: "#666" }}>
        Simulated result -- paper trading only, not financial advice.
      </p>

      <RunPicker token={session.access_token} selectedRunId={runId} onSelect={onSelectRun} />

      {runId === null && <p>Pick a run above to see its trades.</p>}

      {runId !== null && run.isLoading && <p>Loading run...</p>}
      {runId !== null && run.isError && <p>Error loading run: {(run.error as Error).message}</p>}

      {run.data && run.data.status === "failed" && (
        <p style={{ color: "#b02a37" }}>
          This run failed: {run.data.error}. {Object.keys(run.data.blocked_instruments).length} instrument(s) blocked.
        </p>
      )}

      {run.data && run.data.status === "completed" && run.data.paper_status === "not_run" && (
        <div>
          <p>The paper engine hasn't run yet for this strategy run.</p>
          <button disabled={runPaperEngine.isPending} onClick={() => runPaperEngine.mutate()}>
            {runPaperEngine.isPending ? "Running..." : "Run paper engine"}
          </button>
        </div>
      )}

      {run.data && run.data.paper_status === "completed" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
            <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}>
              <option value="">All statuses</option>
              <option value="filled">Filled</option>
              <option value="rejected">Rejected</option>
            </select>
            <select
              value={instrumentFilter}
              onChange={(e) => { setInstrumentFilter(e.target.value ? Number(e.target.value) : ""); setPage(0); }}
            >
              <option value="">All instruments</option>
              {(instruments.data ?? []).map((i) => (
                <option key={i.id} value={i.id}>{i.symbol}</option>
              ))}
            </select>
            <label>
              <input type="checkbox" checked={showUtc} onChange={(e) => setShowUtc(e.target.checked)} /> Show UTC
            </label>
            <button onClick={() => downloadCsv(`/strategy-runs/${runId}/orders.csv`, session.access_token, `paper_orders_${runId}.csv`)}>
              Download orders CSV
            </button>
            <button onClick={() => downloadCsv(`/strategy-runs/${runId}/fills.csv`, session.access_token, `paper_fills_${runId}.csv`)}>
              Download fills CSV
            </button>
          </div>

          {orders.isLoading && <p>Loading orders...</p>}
          {orders.isError && <p>Error loading orders: {(orders.error as Error).message}</p>}
          {orders.data && orders.data.total === 0 && <p>The strategy produced no signals in this range.</p>}

          {orders.data && orders.data.items.length > 0 && (
            <>
              <table style={{ borderCollapse: "collapse", width: "100%" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left" }}>Instrument</th>
                    <th style={{ textAlign: "left" }}>Time ({timeZone})</th>
                    <th style={{ textAlign: "left" }}>Side</th>
                    <th style={{ textAlign: "left" }}>Origin</th>
                    <th style={{ textAlign: "left" }}>Qty</th>
                    <th style={{ textAlign: "left" }}>Status</th>
                    <th style={{ textAlign: "left" }}>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.data.items.map((o) => (
                    <tr key={o.id}>
                      <td>{instrumentsById.get(o.instrument_id)?.symbol ?? o.instrument_id}</td>
                      <td>{formatIsoDateTime(o.execution_ts, timeZone)}</td>
                      <td>{o.side}</td>
                      <td>{o.origin}</td>
                      <td>{o.qty}</td>
                      <td>{o.status}</td>
                      <td>{o.rejection_message ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ marginTop: 8 }}>
                <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</button>{" "}
                <button
                  disabled={(page + 1) * PAGE_SIZE >= orders.data.total}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </button>{" "}
                <span style={{ fontSize: 12, color: "#666" }}>
                  {page * PAGE_SIZE + 1}-{Math.min((page + 1) * PAGE_SIZE, orders.data.total)} of {orders.data.total}
                </span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
