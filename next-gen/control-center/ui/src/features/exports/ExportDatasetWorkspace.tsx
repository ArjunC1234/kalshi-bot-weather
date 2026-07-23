import { useMemo, useState } from "react";
import type {
  ArchiveExportRequest,
  ArtifactMetadata,
  CloneExportRequest,
  CompareExportsRequest,
  CompareExportsResponse,
  CreateExportRequest,
  ExtendExportRequest,
  JobRecord,
  RegistryEntry,
  ReduceExportRequest,
  ValidateExportRequest,
  ValidateExportResponse,
} from "../../types/index";
import { CreateExportPanel } from "./CreateExportPanel";
import { ExportInventory } from "./ExportInventory";
import { ExportOperationsPanel } from "./ExportOperationsPanel";
import type { ExportCreateDraft } from "./exportUtils";

export interface ExportDatasetApi {
  previewExport(request: { profile_id: string }): Promise<Record<string, unknown>>;
  createExport(request: CreateExportRequest): Promise<JobRecord>;
  validateExport(request: ValidateExportRequest): Promise<ValidateExportResponse>;
  compareExports(request: CompareExportsRequest): Promise<CompareExportsResponse>;
  cloneExport(request: CloneExportRequest): Promise<ArtifactMetadata>;
  reduceExport(request: ReduceExportRequest): Promise<ArtifactMetadata>;
  extendExport(request: ExtendExportRequest): Promise<ArtifactMetadata>;
  archiveExport(request: ArchiveExportRequest): Promise<ArtifactMetadata>;
}

export interface ExportDatasetWorkspaceProps {
  api?: ExportDatasetApi;
  exports: ArtifactMetadata[];
  profiles: RegistryEntry[];
  selectedExportId?: string;
  onSelectExport?: (exportId: string) => void;
  onJobCreated?: (job: JobRecord) => void;
  onExportChanged?: (artifact: ArtifactMetadata) => void;
}

export function ExportDatasetWorkspace({
  api,
  exports,
  profiles,
  selectedExportId,
  onSelectExport,
  onJobCreated,
  onExportChanged,
}: ExportDatasetWorkspaceProps) {
  const [internalSelectedId, setInternalSelectedId] = useState(exports[0]?.id ?? "");
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [validation, setValidation] = useState<ValidateExportResponse | null>(null);
  const [compareResult, setCompareResult] = useState<CompareExportsResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const activeExportId = selectedExportId ?? internalSelectedId;
  const selectedExport = useMemo(
    () => exports.find((artifact) => artifact.id === activeExportId) ?? exports[0],
    [activeExportId, exports],
  );

  function selectExport(exportId: string) {
    setInternalSelectedId(exportId);
    onSelectExport?.(exportId);
  }

  return (
    <section className="exports-workspace">
      <ExportInventory
        exports={exports}
        onArchiveExport={selectExport}
        onCloneExport={selectExport}
        onCompareExport={selectExport}
        onInspectExport={selectExport}
        onReduceExport={selectExport}
        onSelectExport={selectExport}
        selectedExportId={selectedExport?.id}
      />
      <CreateExportPanel
        busy={busy}
        exports={exports}
        message={message}
        onCreateExport={(request, draft) => withBusy(async () => {
          const job = await api?.createExport(toCreateRequest(request, draft));
          if (job) onJobCreated?.(job);
          setMessage(job ? `Started ${job.id}` : "Create export handler is not connected");
        })}
        onPreview={(draft: ExportCreateDraft) => withBusy(async () => {
          const result = await api?.previewExport({ profile_id: draft.profile_id });
          setPreview((result ?? null) as Record<string, unknown> | null);
          setMessage(result ? "Preview loaded" : "Preview handler is not connected");
        })}
        preview={preview}
        profiles={profiles}
      />
      <ExportOperationsPanel
        busy={busy}
        compareResult={compareResult}
        exports={exports}
        message={message}
        onArchive={(request) => withBusy(async () => {
          const result = await api?.archiveExport(request);
          if (result) onExportChanged?.(result);
          setMessage(result ? `Archived ${request.export_id}` : "Archive handler is not connected");
        })}
        onClone={(request) => withBusy(async () => {
          const result = await api?.cloneExport(request);
          if (result) onExportChanged?.(result);
          setMessage(result ? `Cloned to ${request.destination}` : "Clone handler is not connected");
        })}
        onCompare={(request) => withBusy(async () => {
          const result = await api?.compareExports(request);
          if (result) setCompareResult(result);
          setMessage(result ? "Comparison loaded" : "Compare handler is not connected");
        })}
        onExtend={(request) => withBusy(async () => {
          const result = await api?.extendExport(request);
          if (result) onExportChanged?.(result);
          setMessage(result ? `Extended to ${request.destination}` : "Extend handler is not connected");
        })}
        onReduce={(request) => withBusy(async () => {
          const result = await api?.reduceExport(request);
          if (result) onExportChanged?.(result);
          setMessage(result ? `Reduced to ${request.destination}` : "Reduce handler is not connected");
        })}
        onValidate={(request) => withBusy(async () => {
          const result = await api?.validateExport(request);
          if (result) setValidation(result);
          setMessage(result ? (result.valid ? "Export is valid" : "Export has issues") : "Validate handler is not connected");
        })}
        selectedExport={selectedExport}
        validation={validation}
      />
    </section>
  );

  async function withBusy(work: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await work();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Export operation failed");
    } finally {
      setBusy(false);
    }
  }
}

function toCreateRequest(request: CreateExportRequest, draft: ExportCreateDraft): CreateExportRequest {
  return {
    ...request,
    output_path: request.output_path || draft.output_path || undefined,
  };
}
