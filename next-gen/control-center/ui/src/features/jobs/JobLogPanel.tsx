import { RefreshCcw } from "lucide-react";
import { useMemo, useState } from "react";

export interface JobLogPanelProps {
  jobId?: string | null;
  log?: string;
  loading?: boolean;
  maxLines?: number;
  onRefresh?: (jobId: string, lines: number) => void | Promise<void>;
}

export function JobLogPanel({
  jobId,
  log = "",
  loading = false,
  maxLines = 5000,
  onRefresh,
}: JobLogPanelProps) {
  const [lineCount, setLineCount] = useState(400);
  const visibleLog = useMemo(() => tailLines(log, lineCount), [lineCount, log]);

  return (
    <section className="panel job-log-panel">
      <header className="panel-title-row">
        <div>
          <h3>Logs</h3>
          <p>{jobId ?? "Select a job"}</p>
        </div>
        <div className="inline-controls">
          <label className="form-field compact">
            <span>Lines</span>
            <select
              value={lineCount}
              onChange={(event) => setLineCount(Number(event.target.value))}
            >
              {[100, 400, 1000, 2500, maxLines].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <button
            className="secondary"
            disabled={!jobId || loading}
            onClick={() => jobId && void onRefresh?.(jobId, lineCount)}
            type="button"
          >
            <RefreshCcw className={loading ? "spin" : undefined} size={15} />
            Refresh
          </button>
        </div>
      </header>
      <pre className="log-viewer">
        {visibleLog || (jobId ? "No log lines returned." : "Select a job to load logs.")}
      </pre>
    </section>
  );
}

function tailLines(log: string, lines: number) {
  const parts = log.split(/\r?\n/);
  return parts.slice(Math.max(0, parts.length - lines)).join("\n");
}
