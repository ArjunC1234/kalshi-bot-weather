import {
  AlertTriangle,
  CheckCircle2,
  CircleStop,
  Clock3,
  Loader2,
  MinusCircle,
} from "lucide-react";

export type StatusTone =
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "muted"
  | "running";

export interface StatusBadgeProps {
  status: string | null | undefined;
  label?: string;
  title?: string;
}

export function StatusBadge({ status, label, title }: StatusBadgeProps) {
  const normalized = normalizeStatus(status);
  const tone = statusTone(normalized);
  const Icon = statusIcon(tone);

  return (
    <span
      className={`status-badge status-badge--${tone}`}
      data-status={normalized}
      title={title ?? labelize(normalized)}
    >
      <Icon
        aria-hidden="true"
        className={tone === "running" ? "spin" : undefined}
        size={14}
      />
      <span>{label ?? labelize(normalized)}</span>
    </span>
  );
}

export function statusTone(status: string): StatusTone {
  if (
    ["complete", "completed", "succeeded", "success", "ready", "valid", "healthy"].includes(
      status,
    )
  ) {
    return "success";
  }
  if (["running", "in_progress", "processing", "active"].includes(status)) {
    return "running";
  }
  if (["queued", "pending", "scheduled", "stale"].includes(status)) {
    return "info";
  }
  if (["warning", "partial", "degraded"].includes(status)) {
    return "warning";
  }
  if (["failed", "error", "invalid", "blocked", "critical"].includes(status)) {
    return "danger";
  }
  if (["cancelled", "canceled", "archived", "disabled", "unknown"].includes(status)) {
    return "muted";
  }
  return "muted";
}

function statusIcon(tone: StatusTone) {
  if (tone === "success") return CheckCircle2;
  if (tone === "running") return Loader2;
  if (tone === "info") return Clock3;
  if (tone === "warning") return AlertTriangle;
  if (tone === "danger") return AlertTriangle;
  if (tone === "muted") return CircleStop;
  return MinusCircle;
}

function normalizeStatus(status: string | null | undefined) {
  return (status ?? "unknown").trim().toLowerCase().replace(/\s+/g, "_");
}

function labelize(value: string) {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}
