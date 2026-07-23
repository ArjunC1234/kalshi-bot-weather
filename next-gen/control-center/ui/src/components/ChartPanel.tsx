import { useEffect, useMemo, useRef } from "react";
import { BarChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import {
  getInstanceByDom,
  init,
  use,
  type ECharts,
  type EChartsCoreOption,
} from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

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

type ChartPanelProps = {
  title: string;
  subtitle?: string;
  option: EChartsCoreOption;
  height?: number;
  className?: string;
};

export function ChartPanel({
  title,
  subtitle,
  option,
  height = 320,
  className = "",
}: ChartPanelProps) {
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
    instance?.setOption(mergedOption, { notMerge: true, lazyUpdate: true });
  }, [mergedOption]);

  return (
    <section className={`chart-card ${className}`}>
      <header className="card-head">
        <div>
          <h3>{title}</h3>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
      </header>
      <div
        ref={ref}
        aria-label={[title, subtitle].filter(Boolean).join(". ")}
        className="chart-canvas"
        role="img"
        style={{ minHeight: height }}
      />
    </section>
  );
}

const tooltipBase = {
  confine: true,
  appendToBody: true,
  backgroundColor: "#eaf3f0",
  borderColor: "rgba(23, 34, 34, 0.2)",
  borderWidth: 1,
  textStyle: { color: "#172222", fontSize: 12 },
  extraCssText: "max-width:280px;white-space:normal;box-shadow:0 16px 40px rgba(0,0,0,.25);",
};

export function lineOption(
  labels: string[],
  series: Array<{ name: string; data: number[]; color: string }>,
): EChartsCoreOption {
  return {
    color: series.map((item) => item.color),
    animationDuration: 260,
    grid: { left: 46, right: 28, top: 44, bottom: 58, containLabel: true },
    tooltip: { ...tooltipBase, trigger: "axis" },
    legend: { top: 8, textStyle: { color: "#8ea39d" }, itemWidth: 12, itemHeight: 8 },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { color: "#8ea39d", hideOverlap: true },
      axisLine: { lineStyle: { color: "#263736" } },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "rgba(142,163,157,.16)" } },
      axisLabel: { color: "#8ea39d" },
    },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 18 }],
    series: series.map((item) => ({
      name: item.name,
      type: "line",
      showSymbol: false,
      smooth: 0.22,
      data: item.data,
      lineStyle: { width: 2 },
    })),
  };
}

export function barOption(labels: string[], values: number[]): EChartsCoreOption {
  return {
    animationDuration: 220,
    color: ["#1dd6b7"],
    grid: { left: 42, right: 24, top: 20, bottom: 48, containLabel: true },
    tooltip: { ...tooltipBase, trigger: "axis" },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { color: "#8ea39d", hideOverlap: true },
      axisLine: { lineStyle: { color: "#263736" } },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "rgba(142,163,157,.16)" } },
      axisLabel: { color: "#8ea39d" },
    },
    series: [{ type: "bar", data: values, barMaxWidth: 34 }],
  };
}

export function heatmapOption(days: string[], metrics: string[], values: number[][]): EChartsCoreOption {
  const data = values.flatMap((row, y) => row.map((value, x) => [x, y, value]));
  return {
    animationDuration: 180,
    grid: { left: 88, right: 24, top: 18, bottom: 64, containLabel: false },
    tooltip: { ...tooltipBase, position: "top" },
    xAxis: {
      type: "category",
      data: days,
      axisLabel: { color: "#8ea39d", hideOverlap: true },
      splitArea: { show: true },
    },
    yAxis: {
      type: "category",
      data: metrics,
      axisLabel: { color: "#8ea39d" },
      splitArea: { show: true },
    },
    visualMap: {
      min: 0,
      max: 100,
      orient: "horizontal",
      left: "center",
      bottom: 12,
      calculable: false,
      textStyle: { color: "#8ea39d" },
      inRange: { color: ["#172222", "#4f7dff", "#1dd6b7", "#ffb547"] },
    },
    series: [{ type: "heatmap", data, label: { show: false } }],
  };
}

export function scatterOption(points: Array<[number, number, string]>): EChartsCoreOption {
  return {
    animationDuration: 180,
    color: ["#1dd6b7"],
    grid: { left: 48, right: 28, top: 24, bottom: 46, containLabel: true },
    tooltip: {
      ...tooltipBase,
      formatter: (value: unknown) => {
        const params = value as { data?: [number, number, string] };
        return `${params.data?.[2] ?? "point"}<br/>x ${params.data?.[0]}<br/>error ${params.data?.[1]}`;
      },
    },
    xAxis: {
      type: "value",
      name: "Feature value",
      nameTextStyle: { color: "#8ea39d" },
      splitLine: { lineStyle: { color: "rgba(142,163,157,.16)" } },
      axisLabel: { color: "#8ea39d" },
    },
    yAxis: {
      type: "value",
      name: "Error",
      nameTextStyle: { color: "#8ea39d" },
      splitLine: { lineStyle: { color: "rgba(142,163,157,.16)" } },
      axisLabel: { color: "#8ea39d" },
    },
    series: [{ type: "scatter", symbolSize: 8, data: points }],
  };
}
