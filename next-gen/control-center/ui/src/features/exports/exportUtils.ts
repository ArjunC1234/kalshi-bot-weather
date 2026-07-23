import type { ArtifactMetadata, RegistryEntry } from "../../types/index";
import type { JsonObject, JsonValue } from "../../types/index";

export interface ExportProfileTable {
  name: string;
  include?: boolean;
  required?: boolean;
  protected?: boolean;
  required_columns?: string[];
  protected_columns?: string[];
  include_columns?: string[];
  exclude_columns?: string[];
  estimated_rows?: number;
  estimated_size_bytes?: number;
}

export interface ExportCreateDraft {
  profile_id: string;
  start: string;
  end: string;
  output_path: string;
  cities: string[];
  tables: ExportProfileTable[];
}

export function artifactDateRange(artifact?: Pick<ArtifactMetadata, "coverage">) {
  const range = artifact?.coverage?.date_range;
  if (!range?.start && !range?.end) return "-";
  if (range.start === range.end) return range.start ?? range.end ?? "-";
  return `${range.start ?? "?"} to ${range.end ?? "?"}`;
}

export function artifactRows(artifact?: Pick<ArtifactMetadata, "table_counts">) {
  return Object.values(artifact?.table_counts ?? {}).reduce((sum, count) => sum + count, 0);
}

export function bytesLabel(bytes: number | null | undefined) {
  if (!bytes || bytes <= 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatNumber(value: number | null | undefined) {
  return new Intl.NumberFormat("en-US").format(value ?? 0);
}

export function labelize(value: string) {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

export function profileTables(profile?: RegistryEntry): ExportProfileTable[] {
  const rawTables = profile?.tables;
  if (!Array.isArray(rawTables)) return [];
  return rawTables
    .map((table): ExportProfileTable | null => {
      if (typeof table === "string") return { name: table, include: true };
      if (!isObject(table)) return null;
      const name = stringValue(table.name);
      if (!name) return null;
      return {
        name,
        include: booleanValue(table.include, true),
        required: booleanValue(table.required, false),
        protected: booleanValue(table.protected, false),
        required_columns: stringArray(table.required_columns),
        protected_columns: stringArray(table.protected_columns),
        include_columns: stringArray(table.include_columns),
        exclude_columns: stringArray(table.exclude_columns),
        estimated_rows: numberValue(table.estimated_rows),
        estimated_size_bytes: numberValue(table.estimated_size_bytes),
      };
    })
    .filter((table): table is ExportProfileTable => Boolean(table));
}

export function uniqueCities(exports: ArtifactMetadata[]) {
  return Array.from(
    new Set(exports.flatMap((artifact) => artifact.coverage?.cities ?? [])),
  ).sort();
}

export function defaultOutputPath(profileId: string, start: string, end: string) {
  const safeProfile = profileId.replace(/[^a-zA-Z0-9_-]+/g, "_") || "export";
  return `data/${safeProfile}_${start}_${end}`;
}

export function jsonSummary(value: JsonValue | JsonObject | undefined, maxLength = 180) {
  if (value === undefined) return "-";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return "-";
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value : "";
}

function booleanValue(value: unknown, fallback: boolean) {
  return typeof value === "boolean" ? value : fallback;
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function stringArray(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : undefined;
}
