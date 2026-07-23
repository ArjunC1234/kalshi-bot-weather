import type { ReactNode } from "react";

export type ChartPrimitive = string | number | boolean | null | undefined;

export interface ChartDatum {
  [key: string]: ChartPrimitive | Date | ChartDatum | ChartDatum[] | number[];
}

export interface ChartSeries {
  id: string;
  name: string;
  color?: string;
  data: Array<[string | number, number | null] | ChartDatum>;
  unit?: string;
  visible?: boolean;
}

export interface ChartPoint {
  x: string | number;
  y: number | null;
  group?: string;
  label?: string;
  value?: number | null;
  meta?: Record<string, ChartPrimitive>;
}

export interface HeatmapCell {
  x: string;
  y: string;
  value: number | null;
  label?: string;
  meta?: Record<string, ChartPrimitive>;
}

export interface CoverageBucket {
  id: string;
  label: string;
  value: number;
  max?: number;
  status?: "good" | "warn" | "bad" | "empty" | "neutral";
}

export interface CityCoverage {
  id: string;
  label: string;
  code?: string;
  lat?: number;
  lon?: number;
  value: number;
  max?: number;
  status?: CoverageBucket["status"];
  selected?: boolean;
  disabled?: boolean;
  details?: Record<string, ChartPrimitive>;
}

export interface VisualizationTheme {
  background: string;
  surface: string;
  border: string;
  grid: string;
  text: string;
  mutedText: string;
  primary: string;
  comparison: string;
  warning: string;
  danger: string;
  success: string;
  palette: string[];
}

export interface ChartLegendItem {
  id: string;
  label: string;
  color: string;
  muted?: boolean;
}

export interface ChartPanelProps {
  title: string;
  subtitle?: string;
  eyebrow?: string;
  option?: Record<string, unknown>;
  height?: number | string;
  loading?: boolean;
  error?: string | null;
  empty?: boolean;
  emptyMessage?: string;
  densityLabel?: string;
  warning?: string;
  actions?: ReactNode;
  legend?: ChartLegendItem[];
  children?: ReactNode;
  className?: string;
}

export interface EChartsViewProps {
  option: Record<string, unknown>;
  height?: number | string;
  className?: string;
  loading?: boolean;
  renderer?: "canvas" | "svg";
  notMerge?: boolean;
  lazyUpdate?: boolean;
  ariaLabel?: string;
  onReady?: (chart: unknown) => void;
  onError?: (message: string) => void;
  events?: Record<string, (params: unknown) => void>;
}

export const controlCenterTheme: VisualizationTheme = {
  background: "#071012",
  surface: "#101819",
  border: "#263736",
  grid: "rgba(142, 163, 157, 0.18)",
  text: "#EAF3F0",
  mutedText: "#8EA39D",
  primary: "#1DD6B7",
  comparison: "#4F7DFF",
  warning: "#FFB547",
  danger: "#FF6B5B",
  success: "#69D184",
  palette: [
    "#1DD6B7",
    "#4F7DFF",
    "#FFB547",
    "#FF6B5B",
    "#8B6CFF",
    "#69D184",
    "#58B7FF",
    "#F07DCA",
  ],
};
