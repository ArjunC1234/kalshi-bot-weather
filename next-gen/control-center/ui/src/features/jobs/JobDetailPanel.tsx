import { Ban, ExternalLink, FileText, RotateCcw } from "lucide-react";
import type { JobRecord } from "../../types/index";
import { KeyValueGrid } from "../../components/tables";
import { StatusBadge } from "../../components/status";
import {
  commandLabel,
  isCancellable,
  isRetriable,
  jobDurationLabel,
  jobSubmittedAt,
  jobUpdatedAt,
  paramsLabel,
} from "./jobUtils";

export interface JobDetailPanelProps {
  job?: JobRecord | null;
  onOpenLogs?: (jobId: string) => void;
  onCancelJob?: (jobId: string) => void;
  onRetryJob?: (job: JobRecord) => void;
  onOpenOutput?: (job: JobRecord) => void;
}

export function JobDetailPanel({
  job,
  onOpenLogs,
  onCancelJob,
  onRetryJob,
  onOpenOutput,
}: JobDetailPanelProps) {
  if (!job) {
    return (
      <aside className="panel job-detail-panel">
        <h3>Job Detail</h3>
        <p className="muted">Select a job to inspect command, params, output, and errors.</p>
      </aside>
    );
  }

  return (
    <aside className="panel job-detail-panel">
      <header className="panel-title-row">
        <div>
          <h3>{job.id}</h3>
          <p>{job.kind} / {job.registry_id} / {job.entrypoint}</p>
        </div>
        <StatusBadge status={job.status} />
      </header>

      <KeyValueGrid
        items={[
          { key: "submitted", label: "Submitted", value: jobSubmittedAt(job) },
          { key: "updated", label: "Updated", value: jobUpdatedAt(job) },
          { key: "duration", label: "Duration", value: jobDurationLabel(job) },
          { key: "cwd", label: "Working dir", value: job.cwd ?? "-" },
          { key: "output", label: "Output", value: job.output_path ?? "-" },
          { key: "log", label: "Log path", value: job.log_path ?? "-" },
          { key: "returncode", label: "Return code", value: job.returncode ?? "-" },
        ]}
      />

      <section className="subpanel">
        <h4>Command</h4>
        <pre>{commandLabel(job)}</pre>
      </section>

      <section className="subpanel">
        <h4>Params</h4>
        <pre>{paramsLabel(job.params)}</pre>
      </section>

      {job.error ? (
        <section className="validation-box critical">
          <h4>Error</h4>
          <pre>{job.error}</pre>
        </section>
      ) : null}

      <div className="button-grid">
        <button onClick={() => onOpenLogs?.(job.id)} type="button">
          <FileText size={15} />
          Logs
        </button>
        <button disabled={!job.output_path} onClick={() => onOpenOutput?.(job)} type="button">
          <ExternalLink size={15} />
          Output
        </button>
        <button disabled={!isRetriable(job)} onClick={() => onRetryJob?.(job)} type="button">
          <RotateCcw size={15} />
          Retry
        </button>
        <button
          className="danger"
          disabled={!isCancellable(job)}
          onClick={() => onCancelJob?.(job.id)}
          type="button"
        >
          <Ban size={15} />
          Cancel
        </button>
      </div>
    </aside>
  );
}
