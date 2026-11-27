import type { Session } from "@supabase/supabase-js";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import Chart from "../components/Chart";
import {
  ApiError,
  createReplaySession,
  endReplaySession,
  fetchBarsRange,
  fetchInstruments,
  fetchReplayFills,
  fetchWatchlist,
  postReplayOrder,
} from "../lib/api";
import { formatBarTime, formatIsoDateTime } from "../lib/time";
import type { ReplayState, WatchlistRow } from "../types";

interface ReplayTradingProps {
  session: Session;
}

// Not a bug: Buy/Sell is all this screen needs. CLAUDE.md section 1 rules out
// shorting entirely -- a Simul8or-style "Reverse" button would flip a long
// position into a short, which this app never does. Closing a position is
// simply a Sell; the button below is just labeled "Sell (go flat)" so that's
// clear without a separate action.
const SPEED_OPTIONS = [
  { label: "1x", ms: 1500 },
  { label: "4x", ms: 400 },
  { label: "8x", ms: 150 },
];

// Defaults match CLAUDE.md's assumptions exactly (same as Strategy Lab's),
// shown as editable, labeled as assumptions, never silently fixed. A real
// symbol's price can easily exceed the $200 default cap (e.g. AAPL ~$350+/
// share, which buys zero whole shares at the default) -- editable here for
// the same reason Strategy Lab's form makes it editable, not hardcoded.
const DEFAULTS = {
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

// The session's bars can only fall within this UTC calendar day for every
// exchange this app supports (NYSE/NASDAQ/TSX all open and close within the
// same UTC date) -- a wide-enough net without hardcoding exchange hours here.
function dayBoundsEpoch(dayIso: string): [number, number] {
  const from = Math.floor(new Date(`${dayIso}T00:00:00Z`).getTime() / 1000);
  return [from, from + 24 * 60 * 60];
}

export default function ReplayTrading({ session }: ReplayTradingProps) {
  const queryClient = useQueryClient();
  const [instrumentId, setInstrumentId] = useState<number | "">("");
  const [sessionDay, setSessionDay] = useState(todayIso(-7));
  const [startingCash, setStartingCash] = useState(DEFAULTS.starting_cash);
  const [maxPositionValue, setMaxPositionValue] = useState(DEFAULTS.max_position_value);
  const [commission, setCommission] = useState(DEFAULTS.commission_per_order);
  const [spreadBps, setSpreadBps] = useState(DEFAULTS.spread_bps);
  const [slippageBps, setSlippageBps] = useState(DEFAULTS.slippage_bps);
  const [replayState, setReplayState] = useState<ReplayState | null>(null);
  const [revealedIndex, setRevealedIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speedMs, setSpeedMs] = useState(SPEED_OPTIONS[0].ms);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const watchlist = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => fetchWatchlist(session.access_token),
  });
  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: () => fetchInstruments(session.access_token),
  });
  const instrumentsById = new Map((instruments.data ?? []).map((i) => [i.id, i]));

  const activeSessionId = replayState?.session.id ?? null;
  const [from, to] = dayBoundsEpoch(sessionDay);
  const bars = useQuery({
    queryKey: ["replay-bars", replayState?.session.instrument_id, sessionDay],
    queryFn: () => fetchBarsRange(replayState!.session.instrument_id, from, to, session.access_token),
    enabled: activeSessionId !== null,
  });

  const fills = useQuery({
    queryKey: ["replay-fills", activeSessionId],
    queryFn: () => fetchReplayFills(activeSessionId!, session.access_token),
    enabled: activeSessionId !== null,
  });

  const createSession = useMutation({
    mutationFn: () => {
      if (instrumentId === "") throw new Error("choose an instrument first");
      return createReplaySession(
        {
          instrument_id: instrumentId, session_day: sessionDay, starting_cash: startingCash,
          max_position_value: maxPositionValue, commission_per_order: commission,
          spread_bps: spreadBps, slippage_bps: slippageBps,
        },
        session.access_token,
      );
    },
    onSuccess: (state) => {
      setReplayState(state);
      setRevealedIndex(0);
      setPlaying(false);
    },
  });

  const placeOrder = useMutation({
    mutationFn: (side: "buy" | "sell") => {
      const clickedBar = bars.data?.[revealedIndex];
      if (!clickedBar) throw new Error("no bar to trade at yet");
      const clickedBarTs = new Date(clickedBar.ts * 1000).toISOString();
      return postReplayOrder(activeSessionId!, side, clickedBarTs, session.access_token);
    },
    onSuccess: (state) => {
      setReplayState(state);
      queryClient.invalidateQueries({ queryKey: ["replay-fills", activeSessionId] });
    },
  });

  const endSession = useMutation({
    mutationFn: () => endReplaySession(activeSessionId!, session.access_token),
    onSuccess: (state) => {
      setReplayState(state);
      setPlaying(false);
      queryClient.invalidateQueries({ queryKey: ["replay-fills", activeSessionId] });
    },
  });

  // Playback: steps one bar at a time through already-fetched history. This is
  // just an animation speed, not a look-ahead risk -- Buy/Sell always fills at
  // the NEXT bar's open regardless of how fast playback is revealing bars.
  useEffect(() => {
    if (!playing || !bars.data) return;
    if (revealedIndex >= bars.data.length - 1) {
      setPlaying(false);
      return;
    }
    timerRef.current = setInterval(() => {
      setRevealedIndex((i) => {
        if (!bars.data || i >= bars.data.length - 1) {
          setPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, speedMs);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [playing, speedMs, bars.data]);

  function resetSession() {
    setReplayState(null);
    setRevealedIndex(0);
    setPlaying(false);
  }

  if (watchlist.isLoading || instruments.isLoading) return <p className="subtle">Loading watchlist...</p>;

  const rows: WatchlistRow[] = watchlist.data ?? [];

  if (!replayState) {
    return (
      <div>
        <h2 className="section-title">Replay Trading</h2>
        <p className="subtle" style={{ marginBottom: 16 }}>
          Practice manual Buy/Sell against a real historical day, bar by bar -- simulated,
          with a dummy balance. Not financial advice.
        </p>

        <div className="card">
          <h3 className="card-title">Start a session</h3>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">
                Instrument
                <span className="field-hint" title="Only instruments with a current, non-expired COMPLIANT record can be picked -- the compliance gate blocks everything else (shown in parentheses).">!</span>
              </span>
              <select value={instrumentId} onChange={(e) => setInstrumentId(e.target.value ? Number(e.target.value) : "")}>
                <option value="">Choose...</option>
                {rows.map((r) => (
                  <option key={r.id} value={r.id} disabled={r.blocking_reason !== null}>
                    {r.symbol} {r.blocking_reason ? `(${r.blocking_reason})` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">
                Trading day
                <span className="field-hint" title="The past trading day whose real 5-minute bars this session replays, one bar at a time.">!</span>
              </span>
              <input type="date" value={sessionDay} onChange={(e) => setSessionDay(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">
                Starting cash
                <span className="field-hint" title="Dummy balance the session starts with. Virtual money only -- never a real brokerage balance.">!</span>
              </span>
              <input value={startingCash} onChange={(e) => setStartingCash(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">
                Max position value
                <span className="field-hint" title="Per-symbol cap on a single Buy (CLAUDE.md default: CAD $200). A Buy is sized so it can never spend more than this.">!</span>
              </span>
              <input value={maxPositionValue} onChange={(e) => setMaxPositionValue(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">
                Commission/order
                <span className="field-hint" title="Flat fee charged on every fill (buy or sell), deducted from cash alongside the trade.">!</span>
              </span>
              <input value={commission} onChange={(e) => setCommission(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">
                Spread (bps)
                <span className="field-hint" title="Simulated bid-ask spread, in basis points. Half is added to your Buy price and half is subtracted from your Sell price.">!</span>
              </span>
              <input value={spreadBps} onChange={(e) => setSpreadBps(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">
                Slippage (bps)
                <span className="field-hint" title="Simulated market impact, in basis points. Always moves your fill price against you (higher on Buy, lower on Sell).">!</span>
              </span>
              <input value={slippageBps} onChange={(e) => setSlippageBps(e.target.value)} />
            </label>
          </div>
          <button
            className="btn btn-accent"
            disabled={instrumentId === "" || createSession.isPending}
            onClick={() => createSession.mutate()}
          >
            {createSession.isPending ? "Starting..." : "Start session"}
          </button>
          {createSession.isError && (
            <p className="error-text">{(createSession.error as ApiError).message}</p>
          )}
        </div>
      </div>
    );
  }

  const instrument = instrumentsById.get(replayState.session.instrument_id);
  const timeZone = instrument?.timezone ?? "UTC";
  const dayBars = bars.data ?? [];
  const visibleBars = dayBars.slice(0, revealedIndex + 1);
  const currentBar = dayBars[revealedIndex];
  const isFlat = replayState.position_qty === "0";
  const isActive = replayState.session.status === "active";

  const unrealizedPnl =
    !isFlat && replayState.position_entry_price && currentBar
      ? (Number(currentBar.close) - Number(replayState.position_entry_price)) * Number(replayState.position_qty)
      : null;

  const realizedPnlTotal = (fills.data ?? []).reduce(
    (sum, f) => sum + (f.realized_pnl ? Number(f.realized_pnl) : 0), 0);

  const rejection = placeOrder.isError && placeOrder.error instanceof ApiError
    ? (typeof placeOrder.error.detail === "object" && placeOrder.error.detail !== null
        ? (placeOrder.error.detail as { message?: string }).message ?? placeOrder.error.message
        : placeOrder.error.message)
    : null;

  return (
    <div>
      <h2 className="section-title">Replay Trading -- {instrument?.symbol ?? "?"} ({sessionDay})</h2>
      <p className="subtle" style={{ marginBottom: 16 }}>
        Simulated, paper trading only -- not financial advice. Long-only: Sell closes the
        position to flat, there is no "Reverse".
      </p>

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div className="card" style={{ flex: "2 1 480px" }}>
          {bars.isLoading ? (
            <p className="subtle">Loading bars...</p>
          ) : dayBars.length === 0 ? (
            <p className="error-text">No price data for this instrument on this day.</p>
          ) : (
            <>
              <Chart bars={visibleBars} timeZone={timeZone} />
              <div className="field-row" style={{ marginTop: 12 }}>
                <span className="subtle">
                  Bar {revealedIndex + 1} / {dayBars.length}
                  {currentBar && ` -- ${formatBarTime(currentBar.ts, timeZone)}`}
                </span>
                <button className="btn btn-secondary btn-sm" disabled={!isActive} onClick={() => setPlaying((p) => !p)}>
                  {playing ? "Pause" : "Play"}
                </button>
                <div className="segmented">
                  {SPEED_OPTIONS.map((s) => (
                    <button
                      key={s.label}
                      className={`segmented-option ${s.ms === speedMs ? "active" : ""}`}
                      onClick={() => setSpeedMs(s.ms)}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
                <button
                  className="btn btn-secondary btn-sm"
                  disabled={!isActive || revealedIndex >= dayBars.length - 1}
                  onClick={() => setRevealedIndex((i) => Math.min(i + 1, dayBars.length - 1))}
                >
                  Step
                </button>
              </div>
            </>
          )}
        </div>

        <div className="card" style={{ flex: "1 1 260px" }}>
          <div className="stat-grid" style={{ gridTemplateColumns: "1fr" }}>
            <div className="stat-tile">
              <div className="stat-label">Cash</div>
              <div className="stat-value">${replayState.cash_balance}</div>
            </div>
            <div className="stat-tile">
              <div className="stat-label">Position</div>
              <div className="stat-value">
                {isFlat ? "Flat" : `${replayState.position_qty} @ $${replayState.position_entry_price}`}
              </div>
            </div>
            <div className="stat-tile">
              <div className="stat-label">Unrealized P&amp;L</div>
              <div className={`stat-value ${unrealizedPnl == null ? "" : unrealizedPnl > 0 ? "positive" : unrealizedPnl < 0 ? "negative" : ""}`}>
                {unrealizedPnl === null ? "--" : `$${unrealizedPnl.toFixed(2)}`}
              </div>
            </div>
            <div className="stat-tile">
              <div className="stat-label">Realized P&amp;L (session)</div>
              <div className={`stat-value ${realizedPnlTotal > 0 ? "positive" : realizedPnlTotal < 0 ? "negative" : ""}`}>
                ${realizedPnlTotal.toFixed(2)}
              </div>
            </div>
          </div>

          {isActive && (
            <div className="field-row" style={{ marginTop: 12 }}>
              {isFlat ? (
                <button className="btn btn-primary" disabled={placeOrder.isPending} onClick={() => placeOrder.mutate("buy")}>
                  Buy
                </button>
              ) : (
                <button className="btn btn-primary" disabled={placeOrder.isPending} onClick={() => placeOrder.mutate("sell")}>
                  Sell (go flat)
                </button>
              )}
              <button className="btn btn-secondary" disabled={endSession.isPending} onClick={() => endSession.mutate()}>
                {endSession.isPending ? "Ending..." : "End session"}
              </button>
            </div>
          )}
          {rejection && <p className="error-text">{rejection}</p>}
          {endSession.isError && (
            <p className="error-text">{(endSession.error as ApiError).message}</p>
          )}
          {!isActive && (
            <div className="banner banner-warning" style={{ marginTop: 12 }}>
              Session ended. <button className="btn btn-secondary btn-sm" onClick={resetSession}>Start another</button>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <h3 className="card-title">Fill history</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th><th>Side</th><th>Qty</th><th>Fill price</th><th>Commission</th>
                <th>Cash after</th><th>Realized P&amp;L</th><th>Origin</th>
              </tr>
            </thead>
            <tbody>
              {(fills.data ?? []).map((f) => (
                <tr key={f.id}>
                  <td>{formatIsoDateTime(f.fill_ts, timeZone)}</td>
                  <td>{f.side}</td>
                  <td>{f.qty}</td>
                  <td>${f.fill_price}</td>
                  <td>${f.commission}</td>
                  <td>${f.cash_balance_after}</td>
                  <td>{f.realized_pnl !== null ? `$${f.realized_pnl}` : "--"}</td>
                  <td>{f.origin === "eod_exit" ? "end-of-session exit" : "manual"}</td>
                </tr>
              ))}
              {(fills.data ?? []).length === 0 && (
                <tr><td colSpan={8} className="subtle">No trades yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
