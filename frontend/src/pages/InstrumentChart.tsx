import type { Session } from "@supabase/supabase-js";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import Chart from "../components/Chart";
import DevDataBanner from "../components/DevDataBanner";
import { fetchAllBars, fetchBarsRange } from "../lib/api";
import { formatSessionDate } from "../lib/time";
import type { Instrument } from "../types";

type Range = "1D" | "5D" | "ALL";

interface InstrumentChartProps {
  session: Session;
  instrument: Instrument;
  onBack: () => void;
}

const DAY_SECONDS = 24 * 60 * 60;

export default function InstrumentChart({ session, instrument, onBack }: InstrumentChartProps) {
  const [range, setRange] = useState<Range>("1D");
  const [showUtc, setShowUtc] = useState(false);

  const query = useQuery({
    queryKey: ["bars", instrument.id, range],
    queryFn: async () => {
      if (!instrument.latest_bar_ts) return [];
      const latestEpoch = Math.floor(new Date(instrument.latest_bar_ts).getTime() / 1000);
      if (range === "ALL") {
        // Fetched in sequential windows under the API's cap -- see lib/api.ts.
        return fetchAllBars(
          instrument.id,
          instrument.latest_bar_ts,
          instrument.bar_count,
          session.access_token,
        );
      }
      const days = range === "1D" ? 1 : 5;
      const from = latestEpoch - days * DAY_SECONDS;
      const to = latestEpoch + 1; // +1: API's `to` is exclusive
      return fetchBarsRange(instrument.id, from, to, session.access_token);
    },
    enabled: Boolean(instrument.latest_bar_ts),
  });

  const bars = query.data ?? [];
  const hasDevData = bars.some((b) => b.source === "yfinance");
  const displayTimeZone = showUtc ? "UTC" : instrument.timezone;
  const isOversizedRangeError =
    query.isError && (query.error as Error).message.includes("422");

  // 1D: the single session date. 5D/ALL: the date range actually covered by the
  // loaded bars (not the requested window -- e.g. "ALL" may have less history than
  // the window implies near the start of data collection).
  const dateLabel =
    bars.length === 0
      ? null
      : range === "1D"
        ? formatSessionDate(bars[bars.length - 1].ts, displayTimeZone)
        : `${formatSessionDate(bars[0].ts, displayTimeZone)} – ${formatSessionDate(bars[bars.length - 1].ts, displayTimeZone)}`;

  return (
    <div>
      <button className="btn btn-ghost" style={{ paddingLeft: 0 }} onClick={onBack}>&larr; Back to instruments</button>
      <h2 className="section-title">
        {instrument.symbol} ({instrument.exchange})
        {dateLabel && `, ${dateLabel} (${displayTimeZone})`}
      </h2>

      {hasDevData && <DevDataBanner />}

      <div className="field-row">
        <div className="segmented">
          {(["1D", "5D", "ALL"] as const).map((r) => (
            <button
              key={r}
              className={`segmented-option ${range === r ? "active" : ""}`}
              onClick={() => setRange(r)}
              disabled={range === r}
            >
              {r}
            </button>
          ))}
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--text-secondary)" }}>
          <input
            type="checkbox"
            checked={showUtc}
            onChange={(e) => setShowUtc(e.target.checked)}
          />
          Show UTC
        </label>
        <span className="subtle">({displayTimeZone})</span>
        {range === "ALL" && query.isFetching && (
          <span className="subtle">Loading all history...</span>
        )}
      </div>

      {query.isLoading && <p className="subtle">Loading bars...</p>}
      {query.isError && (
        <p className="error-text">
          {isOversizedRangeError
            ? "This range is too large to request directly. Try a shorter range."
            : `Error loading bars: ${(query.error as Error).message}`}
        </p>
      )}
      {!query.isLoading && !query.isError && bars.length === 0 && <p>No bars in this range.</p>}
      {bars.length > 0 && (
        <div className="chart-card">
          <Chart bars={bars} timeZone={displayTimeZone} />
        </div>
      )}
    </div>
  );
}
