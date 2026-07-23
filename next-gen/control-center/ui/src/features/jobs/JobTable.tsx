import { Ban, FileText, RotateCcw, SquareArrowOutUpRight } from "lucide-react";
import type { JobRecord } from "../../types/index";
import { DataTable, type DataTableColumn } from "../../components/tables";
import { StatusBadge } from "../../components/status";
import {
  commandLabel,
  isCancellable,
  isRetriable,
  jobDurationLabel,
  jobSubmittedAt,
  normalizeJobStatus,
} from "./jobUtils";

export interface JobTableProps {
  jobs: JobRecord[];
  selectedJobId?: string;
  detailed?: boolean;
  onSelectJob?: (jobId: string) => void;
  onOpenLogs?: (jobId: string) => void;
  onCancelJob?: (jobId: string) => void;
  onRetryJob?: (job: JobRecord) => void;
  onOpenOutput?: (job: JobRecord) => void;
}

export function JobTable({
  jobs,
  selectedJobId,
  detailed = true,
  onSelectJob,
  onOpenLogs,
  onCancelJob,
  onRetryJob,
  onOpenOutput,
}: JobTableProps) {
  const columns: Array<DataTableColumn<JobRecord>> = [
    {
      id: "job",
      header: "Job",
      cell: (job) => (
        <div className="stacked-cell">
          <strong>{job.id}</strong>
          <span>{job.kind} / {job.entrypoint}</span>
        </div>
      ),
      title: (job) => job.id,
    },
    {
      id: "registry",
      header: "Registry",
      cell: (job) => job.registry_id,
    },
    {
      id: "status",
      header: "Status",
      cell: (job) => <StatusBadge status={normalizeJobStatus(job.status)} />,
    },
    {
      id: "submitted",
      header: "Submitted",
      cell: (job) => jobSubmittedAt(job),
    },
  ];

  if (detailed) {
    columns.push(
      {
        id: "duration",
        header: "Duration",
        cell: (job) => jobDurationLabel(job),
        align: "end",
      },
      {
        id: "output",
        header: "Output",
        cell: (job) => job.output_path ?? "-",
        title: (job) => job.output_path ?? undefined,
      },
      {
        id: "command",
        header: "Command",
        cell: (job) => commandLabel(job),
        title: (job) => commandLabel(job),
      },
    );
  }

  columns.push({
    id: "actions",
    header: "Actions",
    align: "end",
    cell: (job) => (
      <div className="table-actions" onClick={(event) => event.stopPropagation()}>
        <button
          aria-label={`Inspect ${job.id}`}
          onClick={() => onSelectJob?.(job.id)}
          type="button"
        >
          <SquareArrowOutUpRight size={15} />
        </button>
        <button
          aria-label={`Open logs for ${job.id}`}
          onClick={() => onOpenLogs?.(job.id)}
          type="button"
        >
          <FileText size={15} />
        </button>
        <button
          aria-label={`Open output for ${job.id}`}
          disabled={!job.output_path}
          onClick={() => onOpenOutput?.(job)}
          type="button"
        >
          <SquareArrowOutUpRight size={15} />
        </button>
        <button
          aria-label={`Retry ${job.id}`}
          disabled={!isRetriable(job)}
          onClick={() => onRetryJob?.(job)}
          type="button"
        >
          <RotateCcw size={15} />
        </button>
        <button
          aria-label={`Cancel ${job.id}`}
          disabled={!isCancellable(job)}
          onClick={() => onCancelJob?.(job.id)}
          type="button"
        >
          <Ban size={15} />
        </button>
      </div>
    ),
  });

  return (
    <section className="panel job-table-panel">
      <header className="panel-title-row">
        <div>
          <h3>Jobs</h3>
          <p>{jobs.length} queued, running, or historical operations</p>
        </div>
      </header>
      <DataTable
        columns={columns}
        emptyLabel="No jobs have been recorded"
        getRowKey={(job) => job.id}
        onRowClick={onSelectJob ? (job) => onSelectJob(job.id) : undefined}
        rows={jobs}
        selectedRowKey={selectedJobId}
      />
    </section>
  );
}
