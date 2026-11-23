import { LineSeries, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import { useThemeAttr } from "../lib/theme";
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
  const theme = useThemeAttr();

  useEffect(() => {
    if (!containerRef.current) return;
    const isLight = theme === "light";

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        attributionLogo: true,
        background: { color: "transparent" },
        textColor: isLight ? "#5c5478" : "#a79fc4",
      },
      grid: {
        vertLines: { color: isLight ? "rgba(27,16,48,0.06)" : "rgba(255,255,255,0.06)" },
        horzLines: { color: isLight ? "rgba(27,16,48,0.06)" : "rgba(255,255,255,0.06)" },
      },
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    chartRef.current = chart;

    const series = chart.addSeries(LineSeries, {
      color: "#2bd9a8",
      lineWidth: 2,
    });
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
  }, [points, theme]);

  return <div ref={containerRef} style={{ width: "100%", height: 300 }} />;
}
