import type { DataRow } from "./types";

export type RegistryEntry = {
  id: string;
  kind: string;
  path?: string;
  label?: string;
  version?: string | number;
  description?: string;
  purpose?: string;
  provider?: string;
  source?: string;
  entrypoints?: Record<string, EntrypointSpec>;
  params_schema?: JsonSchema;
  artifact_contract?: {
    required_files?: string[];
    optional_files?: string[];
    [key: string]: unknown;
  };
  semantic_outputs?: Record<string, unknown>;
  inputs?: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  tables?: Record<string, ExportTableSpec> | Array<string | ExportTableSpec>;
  [key: string]: unknown;
};

export type JsonSchema = {
  type?: string | string[];
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  [key: string]: unknown;
};

export type JsonSchemaProperty = {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  format?: string;
  pattern?: string;
  examples?: unknown[];
  example?: unknown;
  placeholder?: string;
  [key: string]: unknown;
};

export type EntrypointSpec = {
  label?: string;
  command?: string[];
  params_schema?: JsonSchema;
  produces?: {
    artifact_type?: string;
    contract?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

export type ExportTableSpec = {
  name?: string;
  source_table?: string;
  include?: boolean;
  required?: boolean;
  reason?: string;
  protected_columns?: string[];
  required_columns?: string[];
  include_columns?: string[];
  exclude_columns?: string[];
  [key: string]: unknown;
};

export type ArtifactSchemaColumn = {
  type?: string;
  nullable?: boolean;
  role?: string;
  [key: string]: unknown;
};

export type ArtifactTableSchema = {
  table?: string;
  columns?: Record<string, ArtifactSchemaColumn>;
  row_count?: number;
  row_grain?: string[];
  primary_time_column?: string | null;
  [key: string]: unknown;
};

export type ArtifactMetadata = {
  id: string;
  artifact_type?: string;
  path?: string;
  status?: string;
  modified_utc?: string;
  created_utc?: string | null;
  source_export_id?: string | null;
  contract?: string | null;
  files?: string[];
  table_counts?: Record<string, number>;
  excluded_columns?: Record<string, string[]>;
  schemas?: Record<string, ArtifactTableSchema>;
  summary?: DataRow;
  coverage?: {
    cities?: string[];
    date_range?: { start?: string | null; end?: string | null };
    target_dates?: number;
    snapshot_hours?: number[];
    row_counts?: Record<string, number>;
    [key: string]: unknown;
  };
  metadata_health?: {
    has_manifest?: boolean;
    has_run_manifest?: boolean;
    has_schemas?: boolean;
    status?: string;
    warnings?: string[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

export type JobRecord = {
  id: string;
  kind?: string;
  registry_id?: string;
  entrypoint?: string;
  status: string;
  created_utc?: string;
  updated_utc?: string;
  params?: DataRow;
  command?: string[];
  cwd?: string | null;
  output_path?: string | null;
  log_path?: string;
  returncode?: number | null;
  error?: string | null;
  [key: string]: unknown;
};

export type DashboardSummary = {
  brand?: string;
  artifact_counts?: Record<string, number>;
  exports_available?: number;
  latest_export?: ArtifactMetadata | null;
  registered_models?: number;
  registered_strategies?: number;
  registered_export_profiles?: number;
  recent_jobs?: JobRecord[];
  bot_monitoring?: Record<string, unknown>;
  [key: string]: unknown;
};

export type ControlSnapshot = {
  dashboard: DashboardSummary;
  registryRoot: string;
  registry: Record<string, RegistryEntry[]>;
  exportProfiles: RegistryEntry[];
  models: RegistryEntry[];
  strategies: RegistryEntry[];
  artifacts: ArtifactMetadata[];
  exports: ArtifactMetadata[];
  reports: ArtifactMetadata[];
  jobs: JobRecord[];
  errors: string[];
  loadedAt: string;
};

export type ExportPreviewResponse = {
  profile_id: string;
  id?: string;
  label?: string;
  source?: string;
  tables?: Array<{
    table?: string;
    name?: string;
    include?: boolean;
    required?: boolean;
    protected_columns?: string[];
    required_columns?: string[];
    include_columns?: string[];
    exclude_columns?: string[];
    effective_columns?: string[];
    estimated_rows?: number | null;
    estimated_size_bytes?: number | null;
    reason?: string;
  }> | Record<string, {
    table?: string;
    name?: string;
    source_table?: string;
    include?: boolean;
    required?: boolean;
    protected_columns?: string[];
    required_columns?: string[];
    include_columns?: string[];
    exclude_columns?: string[];
    effective_columns?: string[];
    estimated_rows?: number | null;
    reason?: string;
  }>;
  estimated_rows?: Record<string, number> | number;
  estimated_size_bytes?: number;
  validation?: {
    blocking?: string[];
    warnings?: string[];
  };
  [key: string]: unknown;
};

export type ValidateExportResponse = {
  valid: boolean;
  blocking: string[];
  warnings: string[];
  export: ArtifactMetadata;
};

export type CompareExportsResponse = {
  left: ArtifactMetadata;
  right: ArtifactMetadata;
  table_deltas: Record<string, number>;
  city_delta: string[];
  warnings?: string[];
};

export type VisualizationQueryRequest = {
  artifact_path: string;
  query: {
    table: string;
    tables?: string[];
    x?: string;
    y?: string;
    group?: string[];
    groups?: string[];
    group_by_table?: boolean;
    join?: {
      table: string;
      keys: string[];
      fields: string[];
    };
    filters?: Record<string, unknown>;
    hour_blocks?: number;
    aggregation?: string | { op: string; field?: string; as?: string };
    sample?: number | { limit: number };
    decimate_to?: number;
    density?: boolean | { bins?: number };
    page?: number;
    page_size?: number;
  };
};

export type VisualizationQueryResponse = {
  table: string;
  tables?: string[];
  schema: ArtifactTableSchema;
  rows: DataRow[];
  metadata: {
    total_rows: number;
    filtered_rows: number;
    result_rows: number;
    rows_after_sampling: number;
    rows_after_decimation: number;
    returned_rows: number;
    pagination: {
      page: number;
      page_size: number;
      offset: number;
      has_next_page: boolean;
    };
    fields: {
      x: string | null;
      y: string | null;
      groups: string[];
      hour_blocks: number | null;
    };
    aggregation: DataRow;
    sampling: DataRow;
    decimation: DataRow;
    density: DataRow;
  };
};

const env = (import.meta as ImportMeta & { env?: Record<string, string> }).env ?? {};
export const workbenchBackendCommand = "cd next-gen; python -m control.cli serve-workbench --port 8775";

const defaultControlApiBaseUrl =
  typeof window !== "undefined" && ["5173", "8765"].includes(window.location.port)
    ? "http://127.0.0.1:8775/control/api"
    : "/control/api";
export const controlApiBaseUrl = (env.VITE_CONTROL_API_BASE_URL || defaultControlApiBaseUrl).replace(/\/+$/, "");

export async function loadControlSnapshot(): Promise<ControlSnapshot> {
  const errors: string[] = [];
  const [
    dashboard,
    registry,
    exportProfiles,
    models,
    artifacts,
    exportsPayload,
    reports,
    jobs,
  ] = await Promise.all([
    safeGet<DashboardSummary>("/dashboard", {}, errors),
    safeGet<{ root?: string; entries?: Record<string, RegistryEntry[]> }>("/registry", {}, errors),
    safeGet<{ export_profiles?: RegistryEntry[] }>("/export-profiles", {}, errors),
    safeGet<{ models?: RegistryEntry[]; strategies?: RegistryEntry[] }>("/models", {}, errors),
    safeGet<{ artifacts?: ArtifactMetadata[] }>("/artifacts", {}, errors),
    safeGet<{ exports?: ArtifactMetadata[] }>("/exports", {}, errors),
    safeGet<{ reports?: ArtifactMetadata[] }>("/reports", {}, errors),
    safeGet<{ jobs?: JobRecord[] }>("/jobs", {}, errors),
  ]);

  const registryEntries = registry.entries ?? {};
  const mergedArtifacts = mergeArtifacts(artifacts.artifacts ?? []);
  const mergedReports = mergeArtifacts([
    ...mergedArtifacts.filter((item) =>
      ["model_report", "strategy_report", "quality_report"].includes(item.artifact_type ?? ""),
    ),
    ...(reports.reports ?? []),
  ]);
  return {
    dashboard,
    registryRoot: registry.root ?? "",
    registry: registryEntries,
    exportProfiles: exportProfiles.export_profiles ?? registryEntries.export_profile ?? [],
    models: models.models ?? registryEntries.model ?? [],
    strategies: models.strategies ?? registryEntries.strategy ?? [],
    artifacts: mergedArtifacts,
    exports: exportsPayload.exports ?? [],
    reports: mergedReports,
    jobs: jobs.jobs ?? [],
    errors: Array.from(new Set(errors)),
    loadedAt: new Date().toISOString(),
  };
}

function mergeArtifacts(items: ArtifactMetadata[]): ArtifactMetadata[] {
  const byId = new Map<string, ArtifactMetadata>();
  for (const item of items) {
    const normalized = normalizeArtifact(item);
    const existing = byId.get(normalized.id);
    byId.set(normalized.id, mergeArtifact(existing, normalized));
  }
  return [...byId.values()];
}

function normalizeArtifact(item: ArtifactMetadata): ArtifactMetadata {
  const metadata = item.metadata as DataRow | undefined;
  return {
    ...item,
    table_counts: richerObject(item.table_counts, metadata?.table_counts as Record<string, number> | undefined),
    schemas: richerObject(item.schemas, metadata?.schemas as ArtifactMetadata["schemas"]),
    summary: richerObject(item.summary, metadata?.summary as DataRow | undefined),
    coverage: richerObject(item.coverage, metadata?.coverage as ArtifactMetadata["coverage"]),
    metadata_health: richerObject(item.metadata_health, metadata?.metadata_health as ArtifactMetadata["metadata_health"]),
  };
}

function mergeArtifact(left: ArtifactMetadata | undefined, right: ArtifactMetadata): ArtifactMetadata {
  if (!left) return right;
  return {
    ...left,
    ...right,
    files: longerList(left.files, right.files),
    table_counts: richerObject(left.table_counts, right.table_counts),
    schemas: richerObject(left.schemas, right.schemas),
    summary: richerObject(left.summary, right.summary),
    metadata: richerObject(left.metadata as DataRow | undefined, right.metadata as DataRow | undefined),
    coverage: richerObject(left.coverage, right.coverage),
  };
}

function longerList<T>(left: T[] | undefined, right: T[] | undefined): T[] | undefined {
  if (!left?.length) return right;
  if (!right?.length) return left;
  return right.length >= left.length ? right : left;
}

function richerObject<T extends Record<string, unknown> | undefined>(left: T, right: T): T {
  const leftSize = objectSize(left);
  const rightSize = objectSize(right);
  if (!leftSize) return right;
  if (!rightSize) return left;
  return rightSize >= leftSize ? right : left;
}

function objectSize(value: Record<string, unknown> | undefined): number {
  return value && typeof value === "object" ? Object.keys(value).length : 0;
}

export function getExport(exportId: string): Promise<ArtifactMetadata> {
  return controlGet(`/exports/${encodeURIComponent(exportId)}`);
}

export function getDataset(datasetId: string): Promise<ArtifactMetadata> {
  return controlGet(`/datasets/${encodeURIComponent(datasetId)}`);
}

export function getJob(jobId: string): Promise<JobRecord> {
  return controlGet(`/jobs/${encodeURIComponent(jobId)}`);
}

export function getJobLogs(jobId: string, lines = 400): Promise<{ job_id: string; log: string }> {
  const params = new URLSearchParams({ lines: String(lines) });
  return controlGet(`/jobs/${encodeURIComponent(jobId)}/logs?${params}`);
}

export function previewExport(profileId: string): Promise<ExportPreviewResponse> {
  return controlPost("/exports/preview", { profile_id: profileId });
}

export function createExport(request: {
  profile_id: string;
  start: string;
  end: string;
  output_path?: string;
}): Promise<JobRecord> {
  return controlPost("/exports/create", request);
}

export function validateExport(exportId: string): Promise<ValidateExportResponse> {
  return controlPost("/exports/validate", { export_id: exportId });
}

export function compareExports(leftExportId: string, rightExportId: string): Promise<CompareExportsResponse> {
  return controlPost("/exports/compare", {
    left_export_id: leftExportId,
    right_export_id: rightExportId,
  });
}

export function cloneExport(exportId: string, destination: string): Promise<ArtifactMetadata> {
  return controlPost("/exports/clone", { export_id: exportId, destination });
}

export function reduceExport(request: {
  export_id: string;
  destination: string;
  tables?: string[];
  cities?: string[];
  start?: string;
  end?: string;
}): Promise<ArtifactMetadata> {
  return controlPost("/exports/reduce", request);
}

export function extendExport(
  exportId: string,
  extensionExportId: string,
  destination: string,
): Promise<ArtifactMetadata> {
  return controlPost("/exports/extend", {
    export_id: exportId,
    extension_export_id: extensionExportId,
    destination,
  });
}

export function archiveExport(exportId: string): Promise<ArtifactMetadata> {
  return controlPost("/exports/archive", { export_id: exportId });
}

export function createJob(request: {
  kind: string;
  registry_id: string;
  entrypoint: string;
  params?: DataRow;
}): Promise<JobRecord> {
  return controlPost("/jobs", request);
}

export function cancelJob(jobId: string): Promise<{ job_id: string; cancelled: boolean }> {
  return controlPost(`/jobs/${encodeURIComponent(jobId)}/cancel`, {});
}

export function checkModelCompatibility(request: {
  model_id: string;
  dataset_path: string;
}): Promise<{ compatible: boolean; blocking: string[]; warnings: string[]; artifact: ArtifactMetadata }> {
  return controlPost("/compatibility/model-run", request);
}

export function getBotStatus(resource = "status"): Promise<DataRow> {
  return controlGet(`/bot/${encodeURIComponent(resource)}`);
}

export function visualizationQuery(request: VisualizationQueryRequest): Promise<VisualizationQueryResponse> {
  return controlPost("/visualizations/query", request);
}

async function safeGet<T>(path: string, fallback: T, errors: string[]): Promise<T> {
  try {
    return await controlGet<T>(path);
  } catch (error) {
    const message = readableError(error);
    errors.push(message.startsWith("Backend offline") ? message : `${path}: ${message}`);
    return fallback;
  }
}

async function controlGet<T>(path: string): Promise<T> {
  return requestJson<T>(path, { method: "GET" });
}

async function controlPost<T>(path: string, body: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${controlApiBaseUrl}${path.startsWith("/") ? path : `/${path}`}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
      },
    });
  } catch (error) {
    throw new Error(backendOfflineMessage(error));
  }
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      if (!response.ok) {
        throw new Error(text.trim() || response.statusText || `HTTP ${response.status}`);
      }
      throw new Error("Control API returned invalid JSON");
    }
  }
  if (!response.ok) {
    throw new Error(errorMessage(payload, response.statusText || `HTTP ${response.status}`));
  }
  return payload as T;
}

function backendOfflineMessage(error: unknown): string {
  const detail = readableError(error);
  return `Backend offline at ${controlApiBaseUrl}${detail ? ` (${detail})` : ""}`;
}

function errorMessage(payload: unknown, fallback: string): string {
  if (
    payload &&
    typeof payload === "object" &&
    "error" in payload &&
    typeof payload.error === "string"
  ) {
    return payload.error;
  }
  return fallback || "Control API request failed";
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
