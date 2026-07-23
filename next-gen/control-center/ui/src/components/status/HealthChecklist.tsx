import { AlertTriangle, CheckCircle2, Info } from "lucide-react";

export type HealthLevel = "ok" | "warning" | "critical" | "info";

export interface HealthItem {
  id: string;
  label: string;
  value?: string | number | null;
  level?: HealthLevel;
  detail?: string;
}

export interface HealthChecklistProps {
  items: HealthItem[];
  emptyLabel?: string;
}

export function HealthChecklist({
  items,
  emptyLabel = "No health checks available",
}: HealthChecklistProps) {
  if (!items.length) {
    return <p className="muted">{emptyLabel}</p>;
  }

  return (
    <div className="health-checklist">
      {items.map((item) => {
        const level = item.level ?? "info";
        const Icon =
          level === "ok" ? CheckCircle2 : level === "info" ? Info : AlertTriangle;
        return (
          <div
            className={`health-checklist__item health-checklist__item--${level}`}
            key={item.id}
            title={item.detail}
          >
            <Icon aria-hidden="true" size={15} />
            <span>{item.label}</span>
            {item.value !== undefined && item.value !== null ? (
              <strong>{String(item.value)}</strong>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
