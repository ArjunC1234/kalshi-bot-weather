import { useEffect, useMemo, useRef, type CSSProperties, type ReactNode } from "react";
import { BarChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { getInstanceByDom, init, use, type ECharts, type EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { commandPalette } from "./chartOptions";

use([
  BarChart,
  HeatmapChart,
  LineChart,
  ScatterChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export type ChartLegendItem = {
  id: string;
  label: string;
  color: string;
  muted?: boolean;
};

export type CommandChartProps = {
  title: string;
  subtitle?: string;
  eyebrow?: string;
  option?: EChartsCoreOption;
  height?: number;
  legend?: ChartLegendItem[];
  status?: "ready" | "empty" | "warning" | "loading";
  densityLabel?: string;
  inspector?: ReactNode;
  actions?: ReactNode;
  onSelect?: (payload: unknown) => void;
};

export function CommandChart({
  title,
  subtitle,
  eyebrow,
  option,
  height = 320,
  legend,
  status = option ? "ready" : "empty",
  densityLabel,
  inspector,
  actions,
  onSelect,
}: CommandChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ECharts | null>(null);
  const mergedOption = useMemo(() => option, [option]);

  useEffect(() => {
    if (!ref.current) return undefined;
    const chart = init(ref.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => chart.resize());
    });
    observer.observe(ref.current);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = ref.current ? getInstanceByDom(ref.current) : chartRef.current;
    if (!instance || !mergedOption) return;
    instance.setOption(mergedOption, { notMerge: true, lazyUpdate: true });
  }, [mergedOption]);

  useEffect(() => {
    const instance = ref.current ? getInstanceByDom(ref.current) : chartRef.current;
    if (!instance || !onSelect) return undefined;
    const handler = (params: unknown) => onSelect(params);
    instance.on("click", handler);
    return () => {
      instance.off("click", handler);
    };
  }, [onSelect]);

  return (
    <section style={styles.panel}>
      <header style={styles.header}>
        <div>
          {eyebrow ? <div style={styles.eyebrow}>{eyebrow}</div> : null}
          <h3 style={styles.title}>{title}</h3>
          {subtitle ? <p style={styles.subtitle}>{subtitle}</p> : null}
        </div>
        {actions ? <div style={styles.actions}>{actions}</div> : null}
      </header>
      {(legend?.length || densityLabel || status === "warning") ? (
        <div style={styles.meta}>
          {legend?.map((item) => (
            <span key={item.id} style={{ ...styles.legendItem, opacity: item.muted ? 0.48 : 1 }}>
              <span style={{ ...styles.swatch, background: item.color }} />
              {item.label}
            </span>
          ))}
          {densityLabel ? <span style={styles.pill}>{densityLabel}</span> : null}
          {status === "warning" ? <span style={{ ...styles.pill, color: commandPalette.amber }}>Needs attention</span> : null}
        </div>
      ) : null}
      <div style={styles.body}>
        <div
          ref={ref}
          role="img"
          aria-label={[title, subtitle].filter(Boolean).join(". ")}
          style={{ ...styles.canvas, minHeight: height }}
        />
        {status === "empty" ? <div style={styles.empty}>No data for this selection.</div> : null}
        {status === "loading" ? <div style={styles.empty}>Loading analysis...</div> : null}
      </div>
      {inspector ? <aside style={styles.inspector}>{inspector}</aside> : null}
    </section>
  );
}

const styles: Record<string, CSSProperties> = {
  panel: {
    border: `1px solid ${commandPalette.border}`,
    background: "linear-gradient(180deg, rgba(17,24,25,.96), rgba(7,16,18,.96))",
    borderRadius: 10,
    overflow: "hidden",
    boxShadow: "0 18px 60px rgba(0,0,0,.26)",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 16,
    padding: "16px 18px 10px",
    borderBottom: `1px solid ${commandPalette.border}`,
  },
  eyebrow: {
    color: commandPalette.teal,
    fontSize: 11,
    fontWeight: 800,
    letterSpacing: 1.8,
    textTransform: "uppercase",
  },
  title: { margin: 0, color: commandPalette.text, fontSize: 16, fontWeight: 800 },
  subtitle: { margin: "4px 0 0", color: commandPalette.muted, fontSize: 12, lineHeight: 1.45 },
  actions: { display: "flex", gap: 8, alignItems: "center" },
  meta: { display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", padding: "10px 18px 0" },
  legendItem: { display: "inline-flex", gap: 7, alignItems: "center", color: commandPalette.muted, fontSize: 12 },
  swatch: { width: 9, height: 9, borderRadius: 999 },
  pill: {
    border: `1px solid ${commandPalette.border}`,
    borderRadius: 999,
    padding: "3px 8px",
    color: commandPalette.muted,
    fontSize: 11,
    fontWeight: 700,
  },
  body: { position: "relative", padding: "8px 10px 10px" },
  canvas: { width: "100%" },
  empty: {
    position: "absolute",
    inset: 10,
    display: "grid",
    placeItems: "center",
    color: commandPalette.muted,
    background: "rgba(7,16,18,.74)",
    fontSize: 13,
  },
  inspector: {
    margin: "0 18px 16px",
    borderTop: `1px solid ${commandPalette.border}`,
    paddingTop: 12,
    color: commandPalette.muted,
    fontSize: 12,
  },
};

