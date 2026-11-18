import {
  CandlestickSeries,
  HistogramSeries,
  createChart,
  type IChartApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { formatBarDate, formatBarTime } from "../lib/time";
import type { Bar } from "../types";

interface ChartProps {
  bars: Bar[];
  // IANA name for DISPLAY ONLY -- the data passed to the chart is always UTC epoch
  // seconds (Bar.ts), untouched. Only the axis/tooltip formatters use this.
  timeZone: string;
}

// Matches lightweight-charts' own candlestick defaults -- set explicitly (not left
// implicit) so the volume bars below can use the exact same colors, keyed off the
// same up/down test as the candles.
const UP_COLOR = "#26a69a";
const DOWN_COLOR = "#ef5350";

export default function Chart({ bars, timeZone }: ChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      // The lightweight-charts licence requires the "attribution notice"; the
      // built-in TradingView logo satisfies that requirement by itself (see the
      // library's own typings.d.ts on this option). Set explicitly, even though
      // true is already the default, so it's never accidentally turned off.
      layout: { attributionLogo: true },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: Time) => formatBarTime(time as number, timeZone),
      },
      localization: {
        timeFormatter: (time: Time) =>
          `${formatBarDate(time as number, timeZone)} ${formatBarTime(time as number, timeZone)}`,
      },
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP_COLOR,
      downColor: DOWN_COLOR,
      borderVisible: false,
      wickUpColor: UP_COLOR,
      wickDownColor: DOWN_COLOR,
    });
    // Its own pane (index 1), below the candlesticks, not overlaid on price.
    const volumeSeries = chart.addSeries(
      HistogramSeries,
      { priceFormat: { type: "volume" } },
      1,
    );

    // Number() here, and nowhere upstream -- the API keeps prices as Decimal
    // strings; this is the one place that contract is deliberately left, for the
    // chart library's own numeric requirements.
    candleSeries.setData(
      bars.map((b) => ({
        time: b.ts as UTCTimestamp,
        open: Number(b.open),
        high: Number(b.high),
        low: Number(b.low),
        close: Number(b.close),
      })),
    );
    volumeSeries.setData(
      bars.map((b) => ({
        time: b.ts as UTCTimestamp,
        value: b.volume,
        color: Number(b.close) >= Number(b.open) ? UP_COLOR : DOWN_COLOR,
      })),
    );

    chart.timeScale().fitContent();

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [bars, timeZone]);

  return <div ref={containerRef} style={{ width: "100%", height: 500 }} />;
}
