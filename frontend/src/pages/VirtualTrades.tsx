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
      <h2 className="section-title">Virtual Trades</h2>
      <p className="subtle" style={{ marginBottom: 16 }}>
        Simulated result -- paper trading only, not financial advice.
      </p>

      <div className="card">
        <RunPicker token={session.access_token} selectedRunId={runId} onSelect={onSelectRun} />
      </div>

      {runId === null && <p>Pick a run above to see its trades.</p>}

      {runId !== null && run.isLoading && <p className="subtle">Loading run...</p>}
      {runId !== null && run.isError && <p className="error-text">Error loading run: {(run.error as Error).message}</p>}

      {run.data && run.data.status === "failed" && (
        <div className="banner banner-danger">
          This run failed: {run.data.error}. {Object.keys(run.data.blocked_instruments).length} instrument(s) blocked.
        </div>
      )}

      {run.data && run.data.status === "completed" && run.data.paper_status === "not_run" && (
        <div className="card">
          <p style={{ marginTop: 0 }}>The paper engine hasn't run yet for this strategy run.</p>
          <button className="btn btn-primary" disabled={runPaperEngine.isPending} onClick={() => runPaperEngine.mutate()}>
            {runPaperEngine.isPending ? "Running..." : "Run paper engine"}
          </button>
        </div>
      )}

      {run.data && run.data.paper_status === "completed" && (
        <div className="card">
          <div className="field-row">
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
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={showUtc} onChange={(e) => setShowUtc(e.target.checked)} /> Show UTC
            </label>
            <button className="btn btn-secondary btn-sm" onClick={() => downloadCsv(`/strategy-runs/${runId}/orders.csv`, session.access_token, `paper_orders_${runId}.csv`)}>
              Download orders CSV
            </button>
            <button className="btn btn-secondary btn-sm" onClick={() => downloadCsv(`/strategy-runs/${runId}/fills.csv`, session.access_token, `paper_fills_${runId}.csv`)}>
              Download fills CSV
            </button>
          </div>

          {orders.isLoading && <p className="subtle">Loading orders...</p>}
          {orders.isError && <p className="error-text">Error loading orders: {(orders.error as Error).message}</p>}
          {orders.data && orders.data.total === 0 && <p>The strategy produced no signals in this range.</p>}

          {orders.data && orders.data.items.length > 0 && (
            <>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Instrument</th>
                      <th>Time ({timeZone})</th>
                      <th>Side</th>
                      <th>Origin</th>
                      <th>Qty</th>
                      <th>Status</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.data.items.map((o) => (
                      <tr key={o.id}>
                        <td>{instrumentsById.get(o.instrument_id)?.symbol ?? o.instrument_id}</td>
                        <td>{formatIsoDateTime(o.execution_ts, timeZone)}</td>
                        <td>
                          <span className={`badge ${o.side === "buy" ? "badge-success" : "badge-warning"}`}>{o.side}</span>
                        </td>
                        <td>{o.origin}</td>
                        <td>{o.qty}</td>
                        <td>
                          <span className={`badge ${o.status === "filled" ? "badge-success" : "badge-danger"}`}>{o.status}</span>
                        </td>
                        <td>{o.rejection_message ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 10 }}>
                <button className="btn btn-secondary btn-sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</button>
                <button
                  className="btn btn-secondary btn-sm"
                  disabled={(page + 1) * PAGE_SIZE >= orders.data.total}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </button>
                <span className="subtle">
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
