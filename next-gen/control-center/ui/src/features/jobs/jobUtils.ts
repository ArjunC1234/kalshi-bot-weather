import type { JobRecord } from "../../types/index";
import type { JsonValue } from "../../types/index";

export function jobSubmittedAt(job: JobRecord) {
  return formatDate(job.created_utc);
}

export function jobUpdatedAt(job: JobRecord) {
  return formatDate(job.updated_utc);
}

export function jobDurationLabel(job: JobRecord) {
  if (!job.created_utc || !job.updated_utc) return "-";
  const start = new Date(job.created_utc).valueOf();
  const end = new Date(job.updated_utc).valueOf();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return "-";
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remaining}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function commandLabel(job: JobRecord) {
  return job.command?.length ? job.command.join(" ") : "-";
}

export function paramsLabel(value: JsonValue | Record<string, unknown> | undefined) {
  if (!value) return "{}";
  return JSON.stringify(value, null, 2);
}

export function isCancellable(job: JobRecord) {
  return ["queued", "running"].includes(job.status.toLowerCase());
}

export function isRetriable(job: JobRecord) {
  return ["failed", "cancelled", "canceled", "complete", "completed", "succeeded"].includes(
    job.status.toLowerCase(),
  );
}

export function normalizeJobStatus(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "complete") return "completed";
  if (normalized === "succeeded") return "completed";
  if (normalized === "canceled") return "cancelled";
  return normalized;
}

export function formatDate(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
