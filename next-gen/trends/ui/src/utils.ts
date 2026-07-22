import type { DataRow, MetricInfo, SourceInfo } from "./types";

export const TEMPERATURE_KEYS = [
  "final_high_f",
  "nws_anchor_high_f",
  "observed_high_so_far_f",
  "hrrr_projected_high_f",
  "nbm_projected_high_f",
  "ensemble_raw_median_high_f",
  "expected_high_f",
  "predicted_high_f",
  "actual_high_f",
  "settlement_temperature_f",
];

export const PROBABILITY_KEYS = [
  "model_probability",
  "market_probability",
  "normalized_market_midpoint_probability",
  "yes_midpoint",
  "yes_ask_dollars",
  "winner_probability",
  "top_one_accuracy",
  "hit_rate",
  "roi",
  "positive_clv_rate",
  "coverage_ratio",
];

export function asNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

export function formatNumber(value: unknown, digits = 2): string {
  const number = asNumber(value);
  if (number === null) return "n/a";
  const abs = Math.abs(number);
  if (abs >= 1000) return number.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (abs >= 100) return number.toFixed(0);
  if (abs >= 10) return number.toFixed(1);
  return number.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
}

export function formatPercent(value: unknown): string {
  const number = asNumber(value);
  if (number === null) return "n/a";
  const normalized = Math.abs(number) <= 1 ? number * 100 : number;
  return `${formatNumber(normalized, 1)}%`;
}

export function formatCurrency(value: unknown): string {
  const number = asNumber(value);
  if (number === null) return "n/a";
  const sign = number < 0 ? "-" : "";
  return `${sign}$${Math.abs(number).toLocaleString(undefined, {
    maximumFractionDigits: Math.abs(number) >= 100 ? 0 : 2,
  })}`;
}

export function formatDateTime(value: unknown): string {
  const text = asText(value);
  if (!text) return "n/a";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatDate(value: unknown): string {
  const text = asText(value);
  if (!text) return "n/a";
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text.slice(5);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function formatShortDateTime(value: unknown): string {
  const text = asText(value);
  if (!text) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return formatDate(text);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return cleanLabel(text);
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
  });
}

export function sourceLabel(source?: SourceInfo | null): string {
  if (!source) return "None";
  if (source.date_start || source.date_end) {
    return `${source.date_start ?? "?"} to ${source.date_end ?? "?"}`;
  }
  return titleCase(source.model_name || source.mode || source.name || source.id);
}

export function titleCase(value: unknown): string {
  return restoreAcronyms(cleanLabel(value)
    .toLowerCase()
    .replace(/\b[a-z]/g, (letter) => letter.toUpperCase()));
}

export function cleanLabel(value: unknown): string {
  return asText(value)
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\butc\b/gi, "UTC")
    .replace(/\bnws\b/gi, "NWS")
    .replace(/\bhrrr\b/gi, "HRRR")
    .replace(/\bnbm\b/gi, "NBM")
    .replace(/\bpnl\b/gi, "PnL")
    .replace(/\broi\b/gi, "ROI")
    .replace(/\bclv\b/gi, "CLV")
    .replace(/\bf\b/gi, "F")
    .replace(/\s+/g, " ")
    .trim();
}

function restoreAcronyms(value: string): string {
  return value
    .replace(/\bUtc\b/g, "UTC")
    .replace(/\bNws\b/g, "NWS")
    .replace(/\bHrrr\b/g, "HRRR")
    .replace(/\bNbm\b/g, "NBM")
    .replace(/\bPnl\b/g, "PnL")
    .replace(/\bRoi\b/g, "ROI")
    .replace(/\bClv\b/g, "CLV");
}

export function metricLabel(metrics: MetricInfo[], key: string): string {
  return metrics.find((metric) => metric.key === key)?.label ?? titleCase(key);
}

export function unique(values: unknown[]): string[] {
  return [...new Set(values.map(asText).filter(Boolean))];
}

export function sortedBy<T>(rows: T[], getValue: (row: T) => unknown): T[] {
  return [...rows].sort((left, right) =>
    asText(getValue(left)).localeCompare(asText(getValue(right)), undefined, { numeric: true }),
  );
}

export function latestBy<T extends DataRow>(rows: T[], key: string): T | undefined {
  return sortedBy(rows, (row) => row[key]).at(-1);
}

export function numericExtent(rows: DataRow[], key: string): [number, number] | null {
  const values = rows.map((row) => asNumber(row[key])).filter((value) => value !== null);
  if (!values.length) return null;
  return [Math.min(...values), Math.max(...values)];
}

export function colorForIndex(index: number): string {
  const colors = [
    "#25b8a6",
    "#5c8df6",
    "#f1b94b",
    "#ed6a5a",
    "#9b7cf6",
    "#68c77f",
    "#d78fd7",
    "#8fa0a8",
  ];
  return colors[index % colors.length];
}

export function isProbablyPercent(key: string): boolean {
  return PROBABILITY_KEYS.some((part) => key.includes(part));
}

export function isTemperatureKey(key: string): boolean {
  return TEMPERATURE_KEYS.some((part) => key.includes(part));
}
