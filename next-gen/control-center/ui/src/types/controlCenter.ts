import type { JsonObject, JsonValue } from "./json";

export type RegistryKind =
  | "data_source"
  | "export_profile"
  | "model"
  | "strategy"
  | "visualization"
  | "bot_runtime";

export type ArtifactType =
  | "local_export"
  | "model_report"
  | "quality_report"
  | "strategy_report"
  | "unknown";

export type JobStatus =
  | "queued"
  | "running"
  | "complete"
  | "succeeded"
  | "failed"
  | "cancelled";

export type AggregationOp = "none" | "count" | "sum" | "avg" | "min" | "max";

export type ApiMockMode = "never" | "on-error" | "always";

export type ApiConnectionState =
  | "idle"
  | "live"
  | "mock"
  | "offline"
  | "error";

export type ApiResultSource = "live" | "mock";

export interface ControlCenterApiConfig {
  baseUrl: string;
  mockMode: ApiMockMode;
  requestTimeoutMs: number;
}

export interface ControlCenterApiStatus extends ControlCenterApiConfig {
  state: ApiConnectionState;
  source: ApiResultSource | null;
  lastCheckedUtc: string | null;
  lastSuccessUtc: string | null;
  lastError: string | null;
}

export interface ApiResult<T> {
  data: T;
  source: ApiResultSource;
  status: ControlCenterApiStatus;
}

export interface JsonSchemaProperty {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: JsonValue;
  enum?: JsonValue[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  format?: string;
  items?: JsonSchemaProperty;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  [key: string]: unknown;
}

export interface JsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  additionalProperties?: boolean | JsonSchemaProperty;
  [key: string]: unknown;
}

export interface RegistryEntrypointSpec {
  label?: string;
  command?: string[];
  params_schema?: JsonSchema;
  produces?: {
    artifact_type?: ArtifactType | string;
    contract?: string;
    [key: string]: JsonValue | undefined;
  };
  output_contract?: string | JsonObject;
  [key: string]: unknown;
}

export interface RegistryDatasetInput {
  type?: ArtifactType | string;
  required_tables?: string[];
  optional_tables?: string[];
  [key: string]: JsonValue | undefined;
}

export interface RegistryInputs {
  dataset?: RegistryDatasetInput;
  model_report?: RegistryDatasetInput;
  [key: string]: JsonValue | RegistryDatasetInput | undefined;
}

export interface RegistryArtifactContract {
  required_files?: string[];
  optional_files?: string[];
  [key: string]: JsonValue | undefined;
}

export interface RegistryEntry {
  id: string;
  kind: RegistryKind | string;
  path?: string;
  label?: string;
  version?: number | string;
  description?: string;
  purpose?: string;
  source?: string;
  code?: JsonObject;
  tags?: JsonValue;
  inputs?: RegistryInputs | JsonValue;
  outputs?: JsonValue;
  entrypoints?: Record<string, RegistryEntrypointSpec>;
  params_schema?: JsonSchema;
  artifact_contract?: RegistryArtifactContract;
  semantic_outputs?: JsonObject;
  tables?:
    | Record<string, ExportProfileTableSpec>
    | ExportProfileTableSpec[]
    | string[];
  [key: string]: unknown;
}

export interface RegistryResponse {
  root: string;
  entries: Partial<Record<RegistryKind, RegistryEntry[]>> &
    Record<string, RegistryEntry[] | undefined>;
  errors?: string[];
}

export interface DashboardSummary {
  brand: string;
  artifact_counts: Record<string, number>;
  exports_available: number;
  latest_export: ArtifactMetadata | null;
  registered_models: number;
  registered_strategies: number;
  registered_export_profiles: number;
  recent_jobs: JobRecord[];
  bot_monitoring: {
    status: string;
    route: string;
    [key: string]: JsonValue;
  };
  registry_health?: {
    root?: string;
    valid?: number;
    errors?: string[];
    [key: string]: JsonValue | undefined;
  };
  warnings?: string[];
  [key: string]: unknown;
}

export interface ArtifactSchemaColumn {
  type?: string;
  nullable?: boolean;
  role?: string;
  examples?: JsonValue;
  [key: string]: unknown;
}

export interface ArtifactTableSchema {
  columns: Record<string, ArtifactSchemaColumn>;
  row_count?: number;
  row_grain?: string[];
  primary_time_column?: string | null;
  [key: string]: unknown;
}

export interface ExportCoverage {
  cities: string[];
  date_range: {
    start: string | null;
    end: string | null;
  };
  target_dates: number;
  snapshot_hours: number[];
  row_counts: Record<string, number>;
  settlement_coverage?: Record<string, number>;
  final_label_coverage?: Record<string, number>;
  missing_city_hours?: Array<{
    city: string;
    date: string;
    hour?: number;
    table?: string;
  }>;
  [key: string]: unknown;
}

export interface ExportMetadataHealth {
  has_manifest: boolean;
  has_run_manifest: boolean;
  has_schemas: boolean;
  status: string;
  warnings?: string[];
  [key: string]: unknown;
}

export interface ArtifactMetadata {
  id: string;
  artifact_type: ArtifactType | string;
  path: string;
  status: string;
  modified_utc: string;
  created_utc?: string | null;
  source_export_id?: string | null;
  contract?: string | null;
  files: string[];
  table_counts: Record<string, number>;
  excluded_columns?: Record<string, string[]>;
  schemas?: Record<string, ArtifactTableSchema>;
  summary?: JsonObject;
  coverage?: ExportCoverage;
  metadata_health?: ExportMetadataHealth;
  [key: string]: unknown;
}

export interface ArtifactRecord extends ArtifactMetadata {
  metadata?: ArtifactMetadata;
}

export interface JobRecord {
  id: string;
  kind: string;
  registry_id: string;
  entrypoint: string;
  status: JobStatus | string;
  created_utc: string;
  updated_utc: string;
  params: JsonObject;
  command: string[];
  cwd: string | null;
  output_path: string | null;
  log_path: string;
  returncode: number | null;
  error: string | null;
  [key: string]: unknown;
}

export interface ExportProfileTableSpec {
  name?: string;
  source_table?: string;
  include?: boolean;
  required?: boolean;
  reason?: string;
  protected_columns?: string[];
  required_columns?: string[];
  include_columns?: string[];
  exclude_columns?: string[];
  [key: string]: JsonValue | undefined;
}

export interface ExportProfilesResponse {
  export_profiles: RegistryEntry[];
}

export interface ModelsResponse {
  models: RegistryEntry[];
  strategies: RegistryEntry[];
}

export interface ArtifactsResponse {
  artifacts: ArtifactRecord[];
}

export interface ExportsResponse {
  exports: ArtifactMetadata[];
}

export interface ReportsResponse {
  reports: ArtifactMetadata[];
}

export interface DatasetsResponse {
  datasets: ArtifactMetadata[];
}

export interface DatasetCitiesResponse {
  dataset_id: string;
  cities: string[];
}

export interface DatasetCityResponse {
  dataset_id: string;
  city: string;
  available: boolean;
  coverage: ExportCoverage;
}

export interface JobsResponse {
  jobs: JobRecord[];
}

export interface ControlCenterState {
  dashboard: DashboardSummary;
  exports: ArtifactMetadata[];
  models: RegistryEntry[];
  strategies: RegistryEntry[];
  profiles: RegistryEntry[];
  jobs: JobRecord[];
}

export interface JobLogsResponse {
  job_id: string;
  log: string;
}

export interface JobCancelResponse {
  job_id: string;
  cancelled: boolean;
}

export interface CreateJobRequest {
  kind: "model" | "strategy" | "export_profile" | string;
  registry_id: string;
  entrypoint: string;
  params?: JsonObject;
}

export interface ExportPreviewRequest {
  profile_id: string;
}

export interface ExportPreviewTable {
  table: string;
  source_table?: string;
  include: boolean;
  required?: boolean;
  protected_columns?: string[];
  required_columns?: string[];
  include_columns?: string[];
  exclude_columns?: string[];
  effective_columns?: string[];
  estimated_rows?: number | null;
  estimated_size_bytes?: number | null;
  reason?: string;
}

export interface ExportPreviewResponse {
  profile_id: string;
  source?: string;
  tables: ExportPreviewTable[];
  available_tables?: string[];
  estimated_rows?: Record<string, number> | number;
  estimated_size_bytes?: number;
  validation?: {
    blocking: string[];
    warnings: string[];
  };
  [key: string]: unknown;
}

export interface CreateExportRequest {
  profile_id: string;
  start: string;
  end: string;
  output_path?: string;
}

export interface ValidateExportRequest {
  export_id: string;
}

export interface ValidateExportResponse {
  valid: boolean;
  blocking: string[];
  warnings: string[];
  export: ArtifactMetadata;
}

export interface CompareExportsRequest {
  left_export_id: string;
  right_export_id: string;
}

export interface CompareExportsResponse {
  left: ArtifactMetadata;
  right: ArtifactMetadata;
  table_deltas: Record<string, number>;
  city_delta: string[];
  warnings?: string[];
}

export interface CloneExportRequest {
  export_id: string;
  destination: string;
}

export interface ReduceExportRequest {
  export_id: string;
  destination: string;
  tables?: string[];
  cities?: string[];
  start?: string;
  end?: string;
}

export interface ExtendExportRequest {
  export_id: string;
  extension_export_id: string;
  destination: string;
}

export interface ArchiveExportRequest {
  export_id: string;
}

export type ExportOperationRequest =
  | CloneExportRequest
  | ReduceExportRequest
  | ExtendExportRequest
  | ArchiveExportRequest;

export interface ModelRunCompatibilityRequest {
  model_id: string;
  dataset_path: string;
}

export interface ModelRunCompatibilityResponse {
  compatible: boolean;
  blocking: string[];
  warnings: string[];
  artifact: ArtifactMetadata;
}

export interface VisualizationAggregation {
  op: AggregationOp;
  field?: string;
  as?: string;
}

export interface VisualizationFilters {
  city?: string | string[];
  cities?: string[];
  date_column?: string;
  date_from?: string;
  date_to?: string;
  hour_blocks?: number;
  [key: string]: JsonValue | undefined;
}

export interface VisualizationQuery {
  table: string;
  x?: string;
  y?: string;
  group?: string[];
  groups?: string[];
  filters?: VisualizationFilters;
  hour_blocks?: number;
  aggregation?: AggregationOp | VisualizationAggregation;
  sample?: number | { limit: number };
  decimate_to?: number;
  density?: boolean | { bins?: number };
  page?: number;
  page_size?: number;
}

export interface VisualizationQueryRequest {
  artifact_path: string;
  query: VisualizationQuery;
}

export interface VisualizationQueryResponse {
  table: string;
  schema: ArtifactTableSchema;
  rows: JsonObject[];
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
    aggregation: JsonObject;
    sampling: JsonObject;
    decimation: JsonObject;
    density: JsonObject;
  };
}

export interface BotStatusResponse {
  status: string;
  resource?: string;
  message?: string;
  [key: string]: JsonValue | undefined;
}

export interface ApiErrorPayload {
  error: string;
}
