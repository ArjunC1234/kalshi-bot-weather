import type { CoverageBucket } from "./types";
import "./visualization.css";

export interface MetricSparklineProps {
  label: string;
  value: string;
  points: Array<number | null>;
  unit?: string;
  delta?: string;
  status?: "good" | "warn" | "bad" | "neutral";
}

export interface CoverageBarsProps {
  buckets: CoverageBucket[];
  compact?: boolean;
  onBucketClick?: (bucket: CoverageBucket) => void;
}

const statusClass = (status?: string) => `is-${status ?? "neutral"}`;

const sparklinePath = (points: Array<number | null>, width: number, height: number) => {
  const values = points.filter((point): point is number => typeof point === "number");
  if (!values.length) {
    return "";
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / Math.max(points.length - 1, 1);
  let started = false;

  return points
    .map((point, index) => {
      if (point === null) {
        return "";
      }
      const x = index * step;
      const y = height - ((point - min) / span) * height;
      const command = started ? "L" : "M";
      started = true;
      return `${command} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .filter(Boolean)
    .join(" ");
};

export function MetricSparkline({
  label,
  value,
  points,
  unit,
  delta,
  status = "neutral",
}: MetricSparklineProps) {
  const path = sparklinePath(points, 132, 38);

  return (
    <article className={["kbc-metric-sparkline", statusClass(status)].join(" ")}>
      <div className="kbc-metric-sparkline__copy">
        <span className="kbc-metric-sparkline__label">{label}</span>
        <strong className="kbc-metric-sparkline__value">
          {value}
          {unit ? <span>{unit}</span> : null}
        </strong>
        {delta ? <span className="kbc-metric-sparkline__delta">{delta}</span> : null}
      </div>
      <svg viewBox="0 0 132 38" className="kbc-metric-sparkline__chart" aria-hidden="true">
        <path d={path} fill="none" pathLength={1} />
      </svg>
    </article>
  );
}

export function CoverageBars({ buckets, compact, onBucketClick }: CoverageBarsProps) {
  return (
    <div className={compact ? "kbc-coverage-bars is-compact" : "kbc-coverage-bars"}>
      {buckets.map((bucket) => {
        const max = bucket.max ?? 100;
        const width = max > 0 ? Math.max(0, Math.min(100, (bucket.value / max) * 100)) : 0;
        const isButton = Boolean(onBucketClick);

        const content = (
          <>
            <span className="kbc-coverage-bars__label">{bucket.label}</span>
            <span className="kbc-coverage-bars__track" aria-hidden="true">
              <span
                className={["kbc-coverage-bars__fill", statusClass(bucket.status)].join(" ")}
                style={{ width: `${width}%` }}
              />
            </span>
            <span className="kbc-coverage-bars__value">{bucket.value.toLocaleString()}</span>
          </>
        );

        return isButton ? (
          <button
            key={bucket.id}
            type="button"
            className="kbc-coverage-bars__row is-clickable"
            onClick={() => onBucketClick?.(bucket)}
          >
            {content}
          </button>
        ) : (
          <div key={bucket.id} className="kbc-coverage-bars__row">
            {content}
          </div>
        );
      })}
    </div>
  );
}
