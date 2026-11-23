import type { Session } from "@supabase/supabase-js";
import { useMutation, useQuery } from "@tanstack/react-query";
import AiSummaryPanel from "../components/AiSummaryPanel";
import EquityCurveChart from "../components/EquityCurveChart";
import RunPicker from "../components/RunPicker";
import { fetchInstruments, fetchMetrics, fetchStrategyRun, postPaperRun } from "../lib/api";
import { formatMoney as money, formatPercent as pct } from "../lib/metrics";

interface DashboardProps {
  session: Session;
  runId: number | null;
  onSelectRun: (runId: number | null) => void;
}

export default function Dashboard({ session, runId, onSelectRun }: DashboardProps) {
  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: () => fetchInstruments(session.access_token),
  });

  const run = useQuery({
    queryKey: ["strategy-run", runId],
    queryFn: () => fetchStrategyRun(runId as number, session.access_token),
    enabled: runId !== null,
  });

  const metrics = useQuery({
    queryKey: ["metrics", runId],
    queryFn: () => fetchMetrics(runId as number, session.access_token),
    enabled: runId !== null && run.data?.paper_status === "completed",
  });

  const runPaperEngine = useMutation({
    mutationFn: () => postPaperRun(runId as number, session.access_token),
    onSuccess: () => run.refetch(),
  });

  const instrumentsById = new Map((instruments.data ?? []).map((i) => [i.id, i]));

  return (
    <div>
      <h2 className="section-title">Dashboard</h2>
      <p className="subtle" style={{ marginBottom: 16 }}>
        Simulated result -- paper trading only, not financial advice.
      </p>

      <div className="card">
        <RunPicker token={session.access_token} selectedRunId={runId} onSelect={onSelectRun} />
      </div>

      {runId === null && <p>Pick a run above to see its metrics.</p>}
      {runId !== null && run.isLoading && <p>Loading run...</p>}
      {runId !== null && run.isError && <p className="error-text">Error loading run: {(run.error as Error).message}</p>}

      {run.data && run.data.status === "failed" && (
        <div className="banner banner-danger">This run failed: {run.data.error}.</div>
      )}

      {run.data && run.data.status === "completed" && run.data.paper_status === "not_run" && (
        <div className="card">
          <p style={{ marginTop: 0 }}>The paper engine hasn't run yet for this strategy run.</p>
          <button className="btn btn-primary" disabled={runPaperEngine.isPending} onClick={() => runPaperEngine.mutate()}>
            {runPaperEngine.isPending ? "Running..." : "Run paper engine"}
          </button>
        </div>
      )}

      {metrics.isLoading && run.data?.paper_status === "completed" && <p>Loading metrics...</p>}
      {metrics.isError && <p className="error-text">Error loading metrics: {(metrics.error as Error).message}</p>}

      {metrics.data && (
        <div>
          {metrics.data.trades_count === 0 && metrics.data.unresolved_positions === 0 && (
            <p>No trades were made in this run.</p>
          )}

          {metrics.data.unresolved_positions > 0 && (
            <div className="banner banner-danger">
              {metrics.data.unresolved_positions} position(s) could not be closed due to missing
              data: {metrics.data.unresolved_instrument_ids
                .map((id) => instrumentsById.get(id)?.symbol ?? id)
                .join(", ")}
            </div>
          )}

          <div className="stat-grid">
            <Metric label="Total return" value={pct(metrics.data.total_return)} sign={metrics.data.total_return} />
            <Metric label="Realized P&L" value={money(metrics.data.realized_pnl)} sign={metrics.data.realized_pnl} />
            <Metric label="Trades" value={String(metrics.data.trades_count)} />
            <Metric label="Win rate" value={pct(metrics.data.win_rate)} />
            <Metric label="Avg win" value={money(metrics.data.avg_win)} />
            <Metric label="Avg non-win" value={money(metrics.data.avg_non_win)} />
            <Metric label="Total fees" value={`$${metrics.data.total_fees}`}
                   caption="already included in realized P&L" />
            <Metric label="Max drawdown" value={`${(Number(metrics.data.max_drawdown) * 100).toFixed(2)}%`} />
          </div>

          <div className="card">
            <h3 className="card-title">Equity curve (realized)</h3>
            <p className="subtle" style={{ marginTop: -6, marginBottom: 12 }}>
              This curve only reflects closed (realized) trades -- it does not show gains or
              losses while a position is still open, and excludes any unresolved position
              entirely.
            </p>
            <EquityCurveChart points={metrics.data.equity_curve} />
          </div>

          <div className="card">
            <AiSummaryPanel
              token={session.access_token}
              runId={runId as number}
              paperEngineHasRun={run.data?.paper_status === "completed"}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, caption, sign }: {
  label: string; value: string; caption?: string; sign?: string | null;
}) {
  const signClass = sign == null ? "" : Number(sign) > 0 ? "positive" : Number(sign) < 0 ? "negative" : "";
  return (
    <div className="stat-tile">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${signClass}`}>{value}</div>
      {caption && <div className="stat-caption">{caption}</div>}
    </div>
  );
}
