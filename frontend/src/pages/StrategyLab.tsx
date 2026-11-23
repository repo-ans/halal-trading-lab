import type { Session } from "@supabase/supabase-js";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, createStrategyRun, fetchInstruments, fetchWatchlist, postPaperRun } from "../lib/api";
import type { WatchlistRow } from "../types";

interface StrategyLabProps {
  session: Session;
  onRunReady: (runId: number) => void;
  onGoToWatchlist: () => void;
}

// One shared map for every instrument-blocking reason this screen can show --
// both the "all instruments blocked" error case below and the partial-block
// list on a successful run use the exact same wording for the exact same
// codes, so the two can never silently say something different. No backend
// equivalent exists for these specific codes (unlike app/services/
// rejection_messages.py's paper-order reasons), so this is the one source.
const BLOCK_REASON_LABELS: Record<string, string> = {
  no_record: "no halal record yet",
  NON_COMPLIANT: "marked not halal",
  QUESTIONABLE: "marked doubtful",
  UNRATED: "not covered by halal screening",
  expired: "halal record has expired",
  licence_restricted: "price data not yet licensed for display",
};

function blockReasonLabel(reason: string): string {
  return BLOCK_REASON_LABELS[reason] ?? reason;
}

interface InstrumentLookup {
  get(id: number): { symbol: string } | undefined;
}

function instrumentLabel(id: string, instrumentsById: InstrumentLookup): string {
  return instrumentsById.get(Number(id))?.symbol ?? `instrument ${id}`;
}

// Pure, exported for testing: the backend's 422 for an all-instruments-
// -blocked run sends the full StrategyRunOut object as `detail` (never a
// plain string) -- this is the one place that shape is interpreted, so the
// generic api.ts error-message fallback never has to know about it, and a
// test can assert this directly without mounting the whole page.
export function blockedInstrumentsFromError(error: unknown): Record<string, string> | null {
  if (!(error instanceof ApiError)) return null;
  const detail = error.detail;
  if (detail === null || typeof detail !== "object" || Array.isArray(detail)) return null;
  const d = detail as Record<string, unknown>;
  if (d.error !== "all_instruments_blocked") return null;
  if (d.blocked_instruments === null || typeof d.blocked_instruments !== "object") return null;
  return d.blocked_instruments as Record<string, string>;
}

// Defaults match CLAUDE.md's assumptions exactly -- shown as editable, labeled
// as assumptions, never silently fixed.
const DEFAULTS = {
  fast_period: 5,
  slow_period: 20,
  starting_cash: "1000",
  max_position_value: "200",
  commission_per_order: "1.00",
  spread_bps: "2",
  slippage_bps: "1",
};

function todayIso(offsetDays: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

export default function StrategyLab({ session, onRunReady, onGoToWatchlist }: StrategyLabProps) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [rangeStart, setRangeStart] = useState(todayIso(-7));
  const [rangeEnd, setRangeEnd] = useState(todayIso(0));
  const [fastPeriod, setFastPeriod] = useState(DEFAULTS.fast_period);
  const [slowPeriod, setSlowPeriod] = useState(DEFAULTS.slow_period);
  const [startingCash, setStartingCash] = useState(DEFAULTS.starting_cash);
  const [maxPositionValue, setMaxPositionValue] = useState(DEFAULTS.max_position_value);
  const [commission, setCommission] = useState(DEFAULTS.commission_per_order);
  const [spreadBps, setSpreadBps] = useState(DEFAULTS.spread_bps);
  const [slippageBps, setSlippageBps] = useState(DEFAULTS.slippage_bps);
  const [resetMaEachSession, setResetMaEachSession] = useState(false);

  const watchlist = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => fetchWatchlist(session.access_token),
  });
  // /watchlist doesn't carry currency (same gap Watchlist.tsx already works
  // around for currency/timezone) -- join /instruments locally by id.
  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: () => fetchInstruments(session.access_token),
  });

  const createRun = useMutation({
    mutationFn: async () => {
      const instrumentsById = new Map((instruments.data ?? []).map((i) => [i.id, i]));
      const firstSelectedId = Array.from(selected)[0];
      // Single-currency simulation (CLAUDE.md section 7 / must-pass 8): use the
      // first selected instrument's own currency -- the backend refuses
      // outright if the selection turns out to be mixed-currency.
      const currency = instrumentsById.get(firstSelectedId)?.currency ?? "USD";
      const run = await createStrategyRun(
        {
          instrument_ids: Array.from(selected),
          range_start: `${rangeStart}T00:00:00Z`,
          range_end: `${rangeEnd}T23:59:59Z`,
          fast_period: fastPeriod,
          slow_period: slowPeriod,
          currency,
          starting_cash: startingCash,
          max_position_value: maxPositionValue,
          commission_per_order: commission,
          spread_bps: spreadBps,
          slippage_bps: slippageBps,
          reset_ma_each_session: resetMaEachSession,
        },
        session.access_token,
      );
      return run;
    },
  });

  const paperRun = useMutation({
    mutationFn: (runId: number) => postPaperRun(runId, session.access_token),
    onSuccess: (_result, runId) => onRunReady(runId),
  });

  const run = createRun.data;
  const allBlockedReasons = createRun.isError
    ? blockedInstrumentsFromError(createRun.error)
    : null;

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (watchlist.isLoading || instruments.isLoading) return <p className="subtle">Loading watchlist...</p>;
  if (watchlist.isError) return <p className="error-text">Error loading watchlist: {(watchlist.error as Error).message}</p>;
  if (instruments.isError) return <p className="error-text">Error loading instruments: {(instruments.error as Error).message}</p>;

  const rows: WatchlistRow[] = watchlist.data ?? [];
  const instrumentsById = new Map((instruments.data ?? []).map((i) => [i.id, i]));

  return (
    <div>
      <h2 className="section-title">Strategy Lab</h2>
      <p className="subtle" style={{ marginBottom: 16 }}>
        Simulated backtest only -- paper trading, not financial advice.
      </p>

      <div className="card">
        <h3 className="card-title">1. Pick instruments from your watchlist</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th></th>
                <th>Symbol</th>
                <th>Gate status</th>
                <th>Last close</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const lastClose = r.latest_close ? Number(r.latest_close) : null;
                const capExceeded = lastClose !== null && lastClose >= Number(maxPositionValue);
                return (
                  <tr key={r.id}>
                    <td>
                      <input type="checkbox" checked={selected.has(r.id)} onChange={() => toggle(r.id)} />
                    </td>
                    <td>{r.symbol}</td>
                    <td>
                      <span className={`badge ${r.blocking_reason ? "badge-danger" : "badge-success"}`}>
                        {r.blocking_reason ?? "ok"}
                      </span>
                    </td>
                    <td>
                      {r.latest_close_restricted ? "restricted (unlicensed source)" : r.latest_close ?? "--"}
                      {capExceeded && (
                        <span className="error-text" style={{ marginLeft: 8 }}>
                          one share costs more than the position cap: no trades will happen
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h3 className="card-title">2. Run parameters (assumptions -- edit freely)</h3>
        <div className="form-grid">
          <label className="field">Range start <input type="date" value={rangeStart} onChange={(e) => setRangeStart(e.target.value)} /></label>
          <label className="field">Range end <input type="date" value={rangeEnd} onChange={(e) => setRangeEnd(e.target.value)} /></label>
          <label className="field">Fast period <input type="number" value={fastPeriod} onChange={(e) => setFastPeriod(Number(e.target.value))} /></label>
          <label className="field">Slow period <input type="number" value={slowPeriod} onChange={(e) => setSlowPeriod(Number(e.target.value))} /></label>
          <label className="field">Starting cash <input value={startingCash} onChange={(e) => setStartingCash(e.target.value)} /></label>
          <label className="field">Max position value <input value={maxPositionValue} onChange={(e) => setMaxPositionValue(e.target.value)} /></label>
          <label className="field">Commission/order <input value={commission} onChange={(e) => setCommission(e.target.value)} /></label>
          <label className="field">Spread (bps) <input value={spreadBps} onChange={(e) => setSpreadBps(e.target.value)} /></label>
          <label className="field">Slippage (bps) <input value={slippageBps} onChange={(e) => setSlippageBps(e.target.value)} /></label>
          <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={resetMaEachSession} onChange={(e) => setResetMaEachSession(e.target.checked)} />
            Reset MA each session
          </label>
        </div>

        <button className="btn btn-accent" disabled={selected.size === 0 || createRun.isPending} onClick={() => createRun.mutate()}>
          {createRun.isPending ? "Running..." : "Run strategy"}
        </button>

        {createRun.isError && (
          allBlockedReasons ? (
            <div className="banner banner-danger" style={{ marginTop: 12, marginBottom: 0 }}>
              <p style={{ marginTop: 0, marginBottom: 8 }}>
                No trades were run. All selected instruments are blocked:
              </p>
              <ul style={{ margin: "0 0 10px" }}>
                {Object.entries(allBlockedReasons).map(([id, reason]) => (
                  <li key={id}>
                    {instrumentLabel(id, instrumentsById)}: {blockReasonLabel(reason)}
                  </li>
                ))}
              </ul>
              <button className="btn btn-secondary btn-sm" onClick={onGoToWatchlist}>
                Go to Watchlist to add a record
              </button>
            </div>
          ) : (
            <p className="error-text">{(createRun.error as ApiError).message}</p>
          )
        )}
      </div>

      {run && (
        <div className="card">
          <div>
            <p style={{ marginTop: 0 }}>Simulated result: run #{run.id} created.</p>
            {Object.keys(run.blocked_instruments).length > 0 && (
              <div>
                <p>Blocked (no signals generated for these):</p>
                <ul>
                  {Object.entries(run.blocked_instruments).map(([id, reason]) => (
                    <li key={id}>
                      {instrumentLabel(id, instrumentsById)}: {blockReasonLabel(reason)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!paperRun.data && !paperRun.isError && (
              <button className="btn btn-primary" disabled={paperRun.isPending} onClick={() => paperRun.mutate(run.id)}>
                {paperRun.isPending ? "Running paper engine..." : "Run paper engine"}
              </button>
            )}
            {paperRun.isError && (
              <div>
                <p className="error-text">
                  Paper engine failed: {(paperRun.error as Error).message}
                </p>
                <button className="btn btn-secondary" onClick={() => paperRun.mutate(run.id)}>Retry paper engine</button>
              </div>
            )}
            {paperRun.data && <p>Paper engine complete -- {paperRun.data.order_count} order(s).</p>}
          </div>
        </div>
      )}
    </div>
  );
}
