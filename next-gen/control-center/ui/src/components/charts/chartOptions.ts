import type { EChartsCoreOption } from "echarts/core";

export const commandPalette = {
  bg: "#071012",
  panel: "#111819",
  panel2: "#0b1719",
  border: "rgba(234, 243, 240, 0.14)",
  borderStrong: "rgba(29, 214, 183, 0.42)",
  text: "#EAF3F0",
  muted: "#8EA39D",
  teal: "#1DD6B7",
  blue: "#4F7DFF",
  amber: "#FFB547",
  coral: "#FF6B5B",
  green: "#63D88D",
  grid: "rgba(142, 163, 157, 0.14)",
};

export type CommandSeries = {
  id?: string;
  name: string;
  data: Array<number | string | null | [string | number, number | null]>;
  color?: string;
};

export type CommandPoint = {
  x: number;
  y: number;
  value?: number;
  label?: string;
  group?: string;
};

export type CommandHeatCell = {
  x: string;
  y: string;
  value: number | null;
};

export type ChartKind = "line" | "bar" | "scatter" | "heatmap";

const tooltipBase = {
  confine: true,
  appendTo: "body",
  backgroundColor: "rgba(234, 243, 240, 0.97)",
  borderColor: "rgba(23, 34, 34, 0.24)",
  borderWidth: 1,
  textStyle: { color: "#172222", fontSize: 12, fontWeight: 600 },
  extraCssText:
    "z-index:999999;max-width:320px;white-space:normal;border-radius:8px;box-shadow:0 16px 48px rgba(0,0,0,.32);",
};

const axisLabel = {
  color: commandPalette.muted,
  hideOverlap: true,
  overflow: "truncate",
  width: 92,
  ellipsis: "...",
};

const baseGrid = {
  left: 54,
  right: 28,
  top: 28,
  bottom: 48,
  containLabel: true,
};

const formatValue = (value: unknown, suffix = "") => {
  if (value === null || value === undefined || value === "") return "n/a";
  if (typeof value !== "number") return String(value);
  const formatted = value.toLocaleString(undefined, {
    maximumFractionDigits: Math.abs(value) >= 100 ? 0 : 3,
  });
  return suffix ? `${formatted}${suffix}` : formatted;
};

export function createCommandLineOption(
  labels: string[],
  series: CommandSeries[],
  options: {
    xName?: string;
    yName?: string;
    suffix?: string;
    referenceLines?: Array<{ y: number; label: string; color?: string }>;
    showZoom?: boolean;
  } = {},
): EChartsCoreOption {
  return {
    color: series.map((item) => item.color ?? commandPalette.teal),
    animationDuration: 220,
    animationDurationUpdate: 160,
    grid: baseGrid,
    legend: { show: false },
    tooltip: {
      ...tooltipBase,
      trigger: "axis",
      valueFormatter: (value: unknown) => formatValue(value, options.suffix),
    },
    xAxis: {
      type: "category",
      data: labels,
      name: options.xName,
      nameLocation: "middle",
      nameGap: 34,
      axisLine: { lineStyle: { color: commandPalette.border } },
      axisTick: { show: false },
      axisLabel,
    },
    yAxis: {
      type: "value",
      name: options.yName,
      nameTextStyle: { color: commandPalette.muted },
      splitLine: { lineStyle: { color: commandPalette.grid } },
      axisLabel: {
        color: commandPalette.muted,
        formatter: (value: number) => formatValue(value, options.suffix),
      },
    },
    dataZoom: options.showZoom
      ? [{ type: "inside" }, { type: "slider", height: 18, bottom: 12 }]
      : undefined,
    series: series.map((item) => ({
      id: item.id ?? item.name,
      name: item.name,
      type: "line",
      data: item.data,
      showSymbol: false,
      smooth: 0.18,
      sampling: "lttb",
      lineStyle: { width: 2 },
      markLine: options.referenceLines?.length
        ? {
            symbol: "none",
            label: { color: commandPalette.text, formatter: "{b}" },
            lineStyle: { type: "dashed", width: 1.5 },
            data: options.referenceLines.map((line) => ({
              name: line.label,
              yAxis: line.y,
              lineStyle: { color: line.color ?? commandPalette.amber },
            })),
          }
        : undefined,
    })),
  };
}

export function createCommandBarOption(
  labels: string[],
  values: number[],
  options: { yName?: string; suffix?: string; colors?: string[] } = {},
): EChartsCoreOption {
  return {
    color: options.colors ?? [commandPalette.teal],
    animationDuration: 200,
    grid: baseGrid,
    tooltip: {
      ...tooltipBase,
      trigger: "axis",
      valueFormatter: (value: unknown) => formatValue(value, options.suffix),
    },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: commandPalette.border } },
      axisTick: { show: false },
      axisLabel,
    },
    yAxis: {
      type: "value",
      name: options.yName,
      splitLine: { lineStyle: { color: commandPalette.grid } },
      axisLabel: { color: commandPalette.muted },
    },
    series: [
      {
        type: "bar",
        data: values,
        barMaxWidth: 34,
        itemStyle: {
          borderRadius: [4, 4, 0, 0],
          color: (params: { dataIndex: number }) =>
            (options.colors ?? [commandPalette.teal])[params.dataIndex % (options.colors?.length ?? 1)],
        },
      },
    ],
  };
}

export function createCommandScatterOption(
  points: CommandPoint[],
  options: { xName?: string; yName?: string; valueName?: string; large?: boolean } = {},
): EChartsCoreOption {
  const groups = Array.from(new Set(points.map((point) => point.group ?? "series")));
  const colors = [commandPalette.teal, commandPalette.blue, commandPalette.amber, commandPalette.coral, commandPalette.green];
  return {
    color: colors,
    animationDuration: 160,
    grid: baseGrid,
    legend: { show: false },
    tooltip: {
      ...tooltipBase,
      trigger: "item",
      formatter: (params: { data?: unknown[]; seriesName?: string }) => {
        const data = params.data ?? [];
        return [
          `<strong>${params.seriesName ?? "Point"}</strong>`,
          `${options.xName ?? "x"}: ${formatValue(data[0])}`,
          `${options.yName ?? "y"}: ${formatValue(data[1])}`,
          options.valueName ? `${options.valueName}: ${formatValue(data[2])}` : "",
          data[3] ? String(data[3]) : "",
        ]
          .filter(Boolean)
          .join("<br />");
      },
    },
    xAxis: {
      type: "value",
      name: options.xName,
      nameLocation: "middle",
      nameGap: 34,
      splitLine: { lineStyle: { color: commandPalette.grid } },
      axisLabel,
    },
    yAxis: {
      type: "value",
      name: options.yName,
      splitLine: { lineStyle: { color: commandPalette.grid } },
      axisLabel,
    },
    series: groups.map((group, index) => ({
      name: group,
      type: "scatter",
      large: options.large,
      progressive: options.large ? 1200 : 0,
      symbolSize: 7,
      itemStyle: { color: colors[index % colors.length], opacity: 0.8 },
      emphasis: { scale: 1.35, itemStyle: { opacity: 1, borderColor: commandPalette.text, borderWidth: 1 } },
      data: points
        .filter((point) => (point.group ?? "series") === group)
        .map((point) => [point.x, point.y, point.value, point.label]),
    })),
  };
}

export function createCommandHeatmapOption(
  cells: CommandHeatCell[],
  options: { xName?: string; yName?: string; valueName?: string; min?: number; max?: number } = {},
): EChartsCoreOption {
  const xs = Array.from(new Set(cells.map((cell) => cell.x)));
  const ys = Array.from(new Set(cells.map((cell) => cell.y)));
  const values = cells.map((cell) => cell.value).filter((value): value is number => typeof value === "number");
  return {
    animationDuration: 160,
    grid: { left: 86, right: 32, top: 24, bottom: 74, containLabel: true },
    tooltip: {
      ...tooltipBase,
      trigger: "item",
      formatter: (params: { data?: [number, number, number | null] }) => {
        const data = params.data;
        if (!data) return "No data";
        return `<strong>${ys[data[1]]}</strong><br />${xs[data[0]]}: ${formatValue(data[2])}`;
      },
    },
    xAxis: { type: "category", data: xs, name: options.xName, axisLabel, axisTick: { show: false } },
    yAxis: { type: "category", data: ys, name: options.yName, axisLabel: { ...axisLabel, width: 124 }, axisTick: { show: false } },
    visualMap: {
      min: options.min ?? Math.min(...values, 0),
      max: options.max ?? Math.max(...values, 1),
      orient: "horizontal",
      left: "center",
      bottom: 10,
      calculable: true,
      textStyle: { color: commandPalette.muted },
      inRange: { color: ["#1b2a2d", commandPalette.blue, commandPalette.teal, commandPalette.amber, commandPalette.coral] },
    },
    series: [
      {
        type: "heatmap",
        name: options.valueName ?? "Value",
        progressive: 800,
        data: cells.map((cell) => [xs.indexOf(cell.x), ys.indexOf(cell.y), cell.value]),
        emphasis: { itemStyle: { borderColor: commandPalette.text, borderWidth: 1 } },
      },
    ],
  };
}

