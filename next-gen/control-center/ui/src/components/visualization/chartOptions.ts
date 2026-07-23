import type {
  ChartPoint,
  ChartSeries,
  HeatmapCell,
  VisualizationTheme,
} from "./types";
import { controlCenterTheme } from "./types";

export type EChartsOption = Record<string, unknown>;

export interface AxisChartOptions {
  title?: string;
  xName?: string;
  yName?: string;
  yUnit?: string;
  smooth?: boolean;
  showSymbols?: boolean;
  stack?: boolean;
  palette?: string[];
  theme?: VisualizationTheme;
  large?: boolean;
}

export interface HeatmapOptions {
  xName?: string;
  yName?: string;
  valueName?: string;
  min?: number;
  max?: number;
  theme?: VisualizationTheme;
}

export interface ScatterOptions {
  xName?: string;
  yName?: string;
  valueName?: string;
  theme?: VisualizationTheme;
  large?: boolean;
  symbolSize?: number | ((point: ChartPoint) => number);
}

const tooltipStyle = (theme: VisualizationTheme): EChartsOption => ({
  trigger: "axis",
  appendTo: "body",
  confine: true,
  borderWidth: 1,
  borderColor: theme.border,
  backgroundColor: "rgba(234, 243, 240, 0.96)",
  textStyle: {
    color: "#172222",
    fontSize: 12,
    fontWeight: 600,
  },
  extraCssText:
    "box-shadow:0 16px 50px rgba(0,0,0,.28);border-radius:8px;max-width:360px;white-space:normal;",
});

const baseGrid = {
  left: 56,
  right: 28,
  top: 28,
  bottom: 48,
  containLabel: true,
};

const axisLabel = (theme: VisualizationTheme) => ({
  color: theme.mutedText,
  hideOverlap: true,
  margin: 12,
  overflow: "truncate",
  width: 92,
  ellipsis: "...",
});

const splitLine = (theme: VisualizationTheme) => ({
  show: true,
  lineStyle: {
    color: theme.grid,
    width: 1,
  },
});

const formatValue = (value: unknown, unit?: string) => {
  if (value === null || value === undefined || value === "") {
    return "n/a";
  }
  if (typeof value === "number") {
    const formatted =
      Math.abs(value) >= 100
        ? value.toLocaleString(undefined, { maximumFractionDigits: 0 })
        : value.toLocaleString(undefined, { maximumFractionDigits: 3 });
    return unit ? `${formatted} ${unit}` : formatted;
  }
  return String(value);
};

export const createLineOption = (
  series: ChartSeries[],
  options: AxisChartOptions = {},
): EChartsOption => {
  const theme = options.theme ?? controlCenterTheme;
  const palette = options.palette ?? theme.palette;

  return {
    color: palette,
    animationDuration: 220,
    animationDurationUpdate: 160,
    grid: baseGrid,
    legend: {
      show: false,
    },
    tooltip: {
      ...tooltipStyle(theme),
      valueFormatter: (value: unknown) => formatValue(value, options.yUnit),
    },
    xAxis: {
      type: "category",
      name: options.xName,
      nameLocation: "middle",
      nameGap: 34,
      axisLine: { lineStyle: { color: theme.border } },
      axisTick: { show: false },
      axisLabel: axisLabel(theme),
    },
    yAxis: {
      type: "value",
      name: options.yName,
      nameTextStyle: { color: theme.mutedText, align: "left" },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: theme.mutedText,
        formatter: (value: number) => formatValue(value, options.yUnit),
      },
      splitLine: splitLine(theme),
    },
    series: series.map((item, index) => ({
      id: item.id,
      name: item.name,
      type: "line",
      data: item.data,
      smooth: options.smooth ?? false,
      showSymbol: options.showSymbols ?? false,
      symbolSize: 7,
      connectNulls: false,
      sampling: options.large ? "lttb" : undefined,
      lineStyle: {
        width: item.visible === false ? 0 : 2,
        color: item.color ?? palette[index % palette.length],
      },
      itemStyle: {
        color: item.color ?? palette[index % palette.length],
      },
      emphasis: {
        focus: "series",
        lineStyle: { width: 3 },
      },
    })),
  };
};

export const createBarOption = (
  series: ChartSeries[],
  options: AxisChartOptions = {},
): EChartsOption => {
  const theme = options.theme ?? controlCenterTheme;
  const palette = options.palette ?? theme.palette;

  return {
    color: palette,
    animationDuration: 220,
    animationDurationUpdate: 160,
    grid: baseGrid,
    legend: { show: false },
    tooltip: {
      ...tooltipStyle(theme),
      valueFormatter: (value: unknown) => formatValue(value, options.yUnit),
    },
    xAxis: {
      type: "category",
      name: options.xName,
      nameLocation: "middle",
      nameGap: 34,
      axisLine: { lineStyle: { color: theme.border } },
      axisTick: { show: false },
      axisLabel: axisLabel(theme),
    },
    yAxis: {
      type: "value",
      name: options.yName,
      axisLabel: {
        color: theme.mutedText,
        formatter: (value: number) => formatValue(value, options.yUnit),
      },
      splitLine: splitLine(theme),
    },
    series: series.map((item, index) => ({
      id: item.id,
      name: item.name,
      type: "bar",
      data: item.data,
      stack: options.stack ? "total" : undefined,
      barMaxWidth: 34,
      large: options.large,
      itemStyle: {
        color: item.color ?? palette[index % palette.length],
        borderRadius: [4, 4, 0, 0],
      },
      emphasis: {
        focus: "series",
      },
    })),
  };
};

export const createHeatmapOption = (
  cells: HeatmapCell[],
  options: HeatmapOptions = {},
): EChartsOption => {
  const theme = options.theme ?? controlCenterTheme;
  const xCategories = Array.from(new Set(cells.map((cell) => cell.x)));
  const yCategories = Array.from(new Set(cells.map((cell) => cell.y)));
  const values = cells
    .map((cell) => cell.value)
    .filter((value): value is number => typeof value === "number");
  const min = options.min ?? Math.min(...values, 0);
  const max = options.max ?? Math.max(...values, 1);

  return {
    animationDuration: 180,
    animationDurationUpdate: 120,
    grid: {
      left: 72,
      right: 32,
      top: 24,
      bottom: 74,
      containLabel: true,
    },
    tooltip: {
      ...tooltipStyle(theme),
      trigger: "item",
      formatter: (params: { data?: [number, number, number | null] }) => {
        const data = params.data;
        if (!data) {
          return "No data";
        }
        const [xIndex, yIndex, value] = data;
        return [
          `<strong>${yCategories[yIndex]}</strong>`,
          `${xCategories[xIndex]}: ${formatValue(value)}`,
        ].join("<br />");
      },
    },
    xAxis: {
      type: "category",
      data: xCategories,
      name: options.xName,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: theme.border } },
      axisLabel: axisLabel(theme),
    },
    yAxis: {
      type: "category",
      data: yCategories,
      name: options.yName,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: theme.border } },
      axisLabel: {
        ...axisLabel(theme),
        width: 120,
      },
    },
    visualMap: {
      min,
      max,
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: 10,
      textStyle: { color: theme.mutedText },
      inRange: {
        color: ["#213237", theme.primary, "#D6C16D", theme.warning, theme.danger],
      },
      itemWidth: 120,
      itemHeight: 10,
    },
    series: [
      {
        type: "heatmap",
        name: options.valueName ?? "Value",
        data: cells.map((cell) => [
          xCategories.indexOf(cell.x),
          yCategories.indexOf(cell.y),
          cell.value,
        ]),
        progressive: 800,
        emphasis: {
          itemStyle: {
            borderColor: theme.text,
            borderWidth: 1,
          },
        },
      },
    ],
  };
};

export const createScatterOption = (
  points: ChartPoint[],
  options: ScatterOptions = {},
): EChartsOption => {
  const theme = options.theme ?? controlCenterTheme;
  const groups = Array.from(new Set(points.map((point) => point.group ?? "series")));
  const symbolSize = options.symbolSize;

  return {
    color: theme.palette,
    animationDuration: 180,
    animationDurationUpdate: 120,
    grid: baseGrid,
    legend: { show: false },
    tooltip: {
      ...tooltipStyle(theme),
      trigger: "item",
      formatter: (params: { data?: unknown[]; seriesName?: string }) => {
        const data = params.data ?? [];
        return [
          `<strong>${params.seriesName ?? "Point"}</strong>`,
          `${options.xName ?? "x"}: ${formatValue(data[0])}`,
          `${options.yName ?? "y"}: ${formatValue(data[1])}`,
          options.valueName ? `${options.valueName}: ${formatValue(data[2])}` : "",
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
      axisLine: { lineStyle: { color: theme.border } },
      axisLabel: axisLabel(theme),
      splitLine: splitLine(theme),
    },
    yAxis: {
      type: "value",
      name: options.yName,
      axisLine: { show: false },
      axisLabel: axisLabel(theme),
      splitLine: splitLine(theme),
    },
    series: groups.map((group, index) => {
      const groupPoints = points.filter((point) => (point.group ?? "series") === group);
      return {
        name: group,
        type: "scatter",
        large: options.large,
        progressive: options.large ? 1200 : 0,
        symbolSize:
          typeof symbolSize === "function"
            ? (value: unknown[]) => {
                const point = groupPoints.find(
                  (candidate) => candidate.x === value[0] && candidate.y === value[1],
                );
                return point ? symbolSize(point) : 7;
              }
            : (symbolSize ?? 7),
        itemStyle: {
          color: theme.palette[index % theme.palette.length],
          opacity: 0.82,
        },
        emphasis: {
          scale: 1.35,
          itemStyle: {
            opacity: 1,
            borderColor: theme.text,
            borderWidth: 1,
          },
        },
        data: groupPoints.map((point) => [point.x, point.y, point.value, point.label]),
      };
    }),
  };
};
