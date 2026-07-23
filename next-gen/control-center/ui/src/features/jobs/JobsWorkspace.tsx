import { useMemo, useState } from "react";
import type { CreateJobRequest, JobRecord } from "../../types/index";
import { JobDetailPanel } from "./JobDetailPanel";
import { JobLogPanel } from "./JobLogPanel";
import { JobTable } from "./JobTable";

export interface JobsApi {
  job(jobId: string): Promise<JobRecord>;
  jobLogs(jobId: string, lines?: number): Promise<{ job_id: string; log: string }>;
  createJob(request: CreateJobRequest): Promise<JobRecord>;
  cancelJob(jobId: string): Promise<{ job_id: string; cancelled: boolean }>;
}

export interface JobsWorkspaceProps {
  api?: JobsApi;
  jobs: JobRecord[];
  selectedJobId?: string;
  onSelectJob?: (jobId: string) => void;
  onJobChanged?: (job: JobRecord) => void;
  onOpenOutput?: (job: JobRecord) => void;
}

export function JobsWorkspace({
  api,
  jobs,
  selectedJobId,
  onSelectJob,
  onJobChanged,
  onOpenOutput,
}: JobsWorkspaceProps) {
  const [internalSelectedId, setInternalSelectedId] = useState(jobs[0]?.id ?? "");
  const [activeLogJobId, setActiveLogJobId] = useState("");
  const [log, setLog] = useState("");
  const [loadingLogs, setLoadingLogs] = useState(false);
  const activeJobId = selectedJobId ?? internalSelectedId;
  const selectedJob = useMemo(
    () => jobs.find((job) => job.id === activeJobId) ?? jobs[0],
    [activeJobId, jobs],
  );

  function selectJob(jobId: string) {
    setInternalSelectedId(jobId);
    onSelectJob?.(jobId);
  }

  async function openLogs(jobId: string, lines = 400) {
    selectJob(jobId);
    setActiveLogJobId(jobId);
    setLoadingLogs(true);
    try {
      const result = await api?.jobLogs(jobId, lines);
      setLog(result?.log ?? "Log handler is not connected.");
    } catch (error) {
      setLog(error instanceof Error ? error.message : "Unable to load job logs");
    } finally {
      setLoadingLogs(false);
    }
  }

  async function cancelJob(jobId: string) {
    try {
      await api?.cancelJob(jobId);
      const updated = await api?.job(jobId);
      if (updated) onJobChanged?.(updated);
    } catch {
      // Integration layer can surface global errors; keep this component non-blocking.
    }
  }

  async function retryJob(job: JobRecord) {
    const request: CreateJobRequest = {
      kind: job.kind,
      registry_id: job.registry_id,
      entrypoint: job.entrypoint,
      params: job.params,
    };
    const created = await api?.createJob(request);
    if (created) onJobChanged?.(created);
  }

  return (
    <section className="jobs-workspace">
      <JobTable
        jobs={jobs}
        onCancelJob={cancelJob}
        onOpenLogs={(jobId) => void openLogs(jobId)}
        onOpenOutput={onOpenOutput}
        onRetryJob={(job) => void retryJob(job)}
        onSelectJob={selectJob}
        selectedJobId={selectedJob?.id}
      />
      <JobDetailPanel
        job={selectedJob}
        onCancelJob={cancelJob}
        onOpenLogs={(jobId) => void openLogs(jobId)}
        onOpenOutput={onOpenOutput}
        onRetryJob={(job) => void retryJob(job)}
      />
      <JobLogPanel
        jobId={activeLogJobId || selectedJob?.id}
        loading={loadingLogs}
        log={log}
        onRefresh={(jobId, lines) => openLogs(jobId, lines)}
      />
    </section>
  );
}
