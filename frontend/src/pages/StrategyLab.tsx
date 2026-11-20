import type { Session } from "@supabase/supabase-js";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { createStrategyRun, fetchInstruments, fetchWatchlist, postPaperRun } from "../lib/api";
import type { WatchlistRow } from "../types";

interface StrategyLabProps {
  session: Session;
  onRunReady: (runId: number) => void;
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

export default function StrategyLab({ session, onRunReady }: StrategyLabProps) {
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
  const allBlocked = run?.status === "failed" && run?.error === "all_instruments_blocked";

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (watchlist.isLoading || instruments.isLoading) return <p>Loading watchlist...</p>;
  if (watchlist.isError) return <p>Error loading watchlist: {(watchlist.error as Error).message}</p>;
  if (instruments.isError) return <p>Error loading instruments: {(instruments.error as Error).message}</p>;

  const rows: WatchlistRow[] = watchlist.data ?? [];

  return (
    <div>
      <h2>Strategy Lab</h2>
      <p style={{ fontSize: 12, color: "#666" }}>
        Simulated backtest only -- paper trading, not financial advice.
      </p>

      <h3>1. Pick instruments from your watchlist</h3>
      <table style={{ borderCollapse: "collapse", width: "100%", marginBottom: 16 }}>
        <thead>
          <tr>
            <th></th>
            <th style={{ textAlign: "left" }}>Symbol</th>
            <th style={{ textAlign: "left" }}>Gate status</th>
            <th style={{ textAlign: "left" }}>Last close</th>
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
                <td>{r.blocking_reason ?? "ok"}</td>
                <td>
                  {r.latest_close_restricted ? "restricted (unlicensed source)" : r.latest_close ?? "--"}
                  {capExceeded && (
                    <span style={{ color: "#b02a37", marginLeft: 8 }}>
                      one share costs more than the position cap: no trades will happen
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <h3>2. Run parameters (assumptions -- edit freely)</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, auto)", gap: 8, marginBottom: 16 }}>
        <label>Range start <input type="date" value={rangeStart} onChange={(e) => setRangeStart(e.target.value)} /></label>
        <label>Range end <input type="date" value={rangeEnd} onChange={(e) => setRangeEnd(e.target.value)} /></label>
        <label>Fast period <input type="number" value={fastPeriod} onChange={(e) => setFastPeriod(Number(e.target.value))} /></label>
        <label>Slow period <input type="number" value={slowPeriod} onChange={(e) => setSlowPeriod(Number(e.target.value))} /></label>
        <label>Starting cash <input value={startingCash} onChange={(e) => setStartingCash(e.target.value)} /></label>
        <label>Max position value <input value={maxPositionValue} onChange={(e) => setMaxPositionValue(e.target.value)} /></label>
        <label>Commission/order <input value={commission} onChange={(e) => setCommission(e.target.value)} /></label>
        <label>Spread (bps) <input value={spreadBps} onChange={(e) => setSpreadBps(e.target.value)} /></label>
        <label>Slippage (bps) <input value={slippageBps} onChange={(e) => setSlippageBps(e.target.value)} /></label>
        <label>
          <input type="checkbox" checked={resetMaEachSession} onChange={(e) => setResetMaEachSession(e.target.checked)} />{" "}
          Reset MA each session
        </label>
      </div>

      <button disabled={selected.size === 0 || createRun.isPending} onClick={() => createRun.mutate()}>
        {createRun.isPending ? "Running..." : "Run strategy"}
      </button>

      {createRun.isError && <p style={{ color: "#b02a37" }}>{(createRun.error as Error).message}</p>}

      {run && (
        <div style={{ marginTop: 16 }}>
          {allBlocked ? (
            <div>
              <p style={{ color: "#b02a37" }}>
                Simulated result: every selected instrument was blocked. Nothing was run.
              </p>
              <ul>
                {Object.entries(run.blocked_instruments).map(([id, reason]) => (
                  <li key={id}>instrument {id}: {reason}</li>
                ))}
              </ul>
            </div>
          ) : (
            <div>
              <p>Simulated result: run #{run.id} created.</p>
              {Object.keys(run.blocked_instruments).length > 0 && (
                <div>
                  <p>Blocked (no signals generated for these):</p>
                  <ul>
                    {Object.entries(run.blocked_instruments).map(([id, reason]) => (
                      <li key={id}>instrument {id}: {reason}</li>
                    ))}
                  </ul>
                </div>
              )}
              {!paperRun.data && !paperRun.isError && (
                <button disabled={paperRun.isPending} onClick={() => paperRun.mutate(run.id)}>
                  {paperRun.isPending ? "Running paper engine..." : "Run paper engine"}
                </button>
              )}
              {paperRun.isError && (
                <div>
                  <p style={{ color: "#b02a37" }}>
                    Paper engine failed: {(paperRun.error as Error).message}
                  </p>
                  <button onClick={() => paperRun.mutate(run.id)}>Retry paper engine</button>
                </div>
              )}
              {paperRun.data && <p>Paper engine complete -- {paperRun.data.order_count} order(s).</p>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
