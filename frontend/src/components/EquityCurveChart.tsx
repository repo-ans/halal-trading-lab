import { LineSeries, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { EquityPoint } from "../types";

interface EquityCurveChartProps {
  points: EquityPoint[];
}

// Realized equity curve only (CLAUDE.md section 7 / app/services/metrics.py) --
// one point per CLOSED round trip, never a point at the buy. Reuses
// lightweight-charts, same as the instrument chart (CLAUDE.md section 2: one
// charting library for everything in the app, never a second one for this).
export default function EquityCurveChart({ points }: EquityCurveChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { attributionLogo: true },
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    chartRef.current = chart;

    const series = chart.addSeries(LineSeries, { color: "#2962ff" });
    series.setData(
      points.map((p) => ({
        time: (Math.floor(new Date(p.ts).getTime() / 1000)) as UTCTimestamp,
        value: Number(p.cash),
      })),
    );
    chart.timeScale().fitContent();

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [points]);

  return <div ref={containerRef} style={{ width: "100%", height: 300 }} />;
}
