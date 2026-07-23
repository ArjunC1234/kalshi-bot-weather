import type {
  ApiMockMode,
  ApiResult,
  ApiResultSource,
  ArchiveExportRequest,
  ArtifactMetadata,
  ArtifactsResponse,
  BotStatusResponse,
  CloneExportRequest,
  CompareExportsRequest,
  CompareExportsResponse,
  ControlCenterApiConfig,
  ControlCenterApiStatus,
  CreateExportRequest,
  CreateJobRequest,
  DatasetCitiesResponse,
  DatasetCityResponse,
  DatasetsResponse,
  DashboardSummary,
  ExportPreviewRequest,
  ExportPreviewResponse,
  ExportProfilesResponse,
  ExportsResponse,
  ExtendExportRequest,
  JobCancelResponse,
  JobLogsResponse,
  JobRecord,
  JobsResponse,
  ModelRunCompatibilityRequest,
  ModelRunCompatibilityResponse,
  ModelsResponse,
  ReduceExportRequest,
  RegistryResponse,
  ReportsResponse,
  ValidateExportRequest,
  ValidateExportResponse,
  VisualizationQueryRequest,
  VisualizationQueryResponse,
} from "../types/index";
import type { ControlCenterState as UiControlCenterState } from "../types";
import type { JsonObject } from "../types/index";
import {
  mockArtifactsResponse,
  mockDashboardSummary,
  mockExport,
  mockExportPreviewResponse,
  mockExportsResponse,
  mockJobLogsResponse,
  mockJobsResponse,
  mockModelReport,
  mockModelRunCompatibilityResponse,
  mockModelsResponse,
  mockRegistryResponse,
  mockReportsResponse,
  mockValidateExportResponse,
  mockVisualizationQueryResponse,
} from "./mockData";

export type MockMode = ApiMockMode;

export interface ControlCenterApiOptions {
  baseUrl?: string;
  fetcher?: typeof fetch;
  mockMode?: MockMode;
  requestTimeoutMs?: number;
  onStatusChange?: (status: ControlCenterApiStatus) => void;
}

export class ControlCenterApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "ControlCenterApiError";
    this.status = status;
    this.payload = payload;
  }
}

export class ControlCenterApiClient {
  private readonly config: ControlCenterApiConfig;
  private readonly fetcher: typeof fetch;
  private readonly requestListeners = new Set<
    (status: ControlCenterApiStatus) => void
  >();
  private status: ControlCenterApiStatus;

  constructor(options: ControlCenterApiOptions = {}) {
    const envConfig = readControlCenterApiEnv();
    this.config = {
      baseUrl: normalizeBaseUrl(options.baseUrl ?? envConfig.baseUrl),
      mockMode: options.mockMode ?? envConfig.mockMode,
      requestTimeoutMs: options.requestTimeoutMs ?? envConfig.requestTimeoutMs,
    };
    this.fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
    this.status = {
      ...this.config,
      state: "idle",
      source: null,
      lastCheckedUtc: null,
      lastSuccessUtc: null,
      lastError: null,
    };
    if (options.onStatusChange) {
      this.requestListeners.add(options.onStatusChange);
    }
  }

  getConfig(): ControlCenterApiConfig {
    return { ...this.config };
  }

  getStatus(): ControlCenterApiStatus {
    return { ...this.status };
  }

  subscribeStatus(
    listener: (status: ControlCenterApiStatus) => void,
  ): () => void {
    this.requestListeners.add(listener);
    listener(this.getStatus());
    return () => {
      this.requestListeners.delete(listener);
    };
  }

  async checkConnection(): Promise<ControlCenterApiStatus> {
    try {
      await this.requestWithMeta<DashboardSummary>(
        "GET",
        "/dashboard",
        undefined,
        mockDashboardSummary,
      );
    } catch {
      // The request path updates status before rethrowing. Consumers only need
      // the current status object here.
    }
    return this.getStatus();
  }

  dashboard(): Promise<DashboardSummary> {
    return this.get("/dashboard", mockDashboardSummary);
  }

  registry(): Promise<RegistryResponse> {
    return this.get("/registry", mockRegistryResponse);
  }

  exportProfiles(): Promise<ExportProfilesResponse> {
    return this.get("/export-profiles", () => mockExportProfilesFromRegistry());
  }

  models(): Promise<ModelsResponse> {
    return this.get("/models", mockModelsResponse);
  }

  artifacts(): Promise<ArtifactsResponse> {
    return this.get("/artifacts", mockArtifactsResponse);
  }

  exports(): Promise<ExportsResponse> {
    return this.get("/exports", mockExportsResponse);
  }

  export(exportId: string): Promise<ArtifactMetadata> {
    return this.get(`/exports/${encodeURIComponent(exportId)}`, () => ({
      ...mockExport,
      id: exportId,
    }));
  }

  reports(): Promise<ReportsResponse> {
    return this.get("/reports", mockReportsResponse);
  }

  async modelReports(): Promise<ReportsResponse> {
    const reports = await this.reports();
    return {
      reports: reports.reports.filter(
        (report) => report.artifact_type === "model_report",
      ),
    };
  }

  async strategyReports(): Promise<ReportsResponse> {
    const reports = await this.reports();
    return {
      reports: reports.reports.filter(
        (report) => report.artifact_type === "strategy_report",
      ),
    };
  }

  async qualityReports(): Promise<ReportsResponse> {
    const reports = await this.reports();
    return {
      reports: reports.reports.filter(
        (report) => report.artifact_type === "quality_report",
      ),
    };
  }

  datasets(): Promise<DatasetsResponse> {
    return this.get("/datasets", () => ({ datasets: mockExportsResponse().exports }));
  }

  dataset(datasetId: string): Promise<ArtifactMetadata> {
    return this.get(`/datasets/${encodeURIComponent(datasetId)}`, () => ({
      ...mockExport,
      id: datasetId,
    }));
  }

  datasetCoverage(datasetId: string): Promise<ArtifactMetadata["coverage"]> {
    return this.get(
      `/datasets/${encodeURIComponent(datasetId)}/coverage`,
      () => mockExport.coverage,
    );
  }

  datasetCities(datasetId: string): Promise<DatasetCitiesResponse> {
    return this.get(`/datasets/${encodeURIComponent(datasetId)}/cities`, () => ({
      dataset_id: datasetId,
      cities: mockExport.coverage?.cities ?? [],
    }));
  }

  datasetCity(datasetId: string, city: string): Promise<DatasetCityResponse> {
    return this.get(
      `/datasets/${encodeURIComponent(datasetId)}/cities/${encodeURIComponent(city)}`,
      () => ({
        dataset_id: datasetId,
        city,
        available: Boolean(mockExport.coverage?.cities.includes(city)),
        coverage: mockExport.coverage!,
      }),
    );
  }

  jobs(): Promise<JobsResponse> {
    return this.get("/jobs", mockJobsResponse);
  }

  job(jobId: string): Promise<JobRecord> {
    return this.get(`/jobs/${encodeURIComponent(jobId)}`, () => ({
      ...mockJobsResponse().jobs[0],
      id: jobId,
    }));
  }

  jobLogs(jobId: string, lines = 400): Promise<JobLogsResponse> {
    const params = new URLSearchParams({ lines: String(lines) });
    return this.get(`/jobs/${encodeURIComponent(jobId)}/logs?${params}`, () =>
      mockJobLogsResponse(jobId),
    );
  }

  createJob(request: CreateJobRequest): Promise<JobRecord> {
    return this.post("/jobs", request, () => ({
      ...mockJobsResponse().jobs[0],
      id: `job_mock_${Date.now()}`,
      kind: request.kind,
      registry_id: request.registry_id,
      entrypoint: request.entrypoint,
      params: request.params ?? {},
      status: "queued",
      created_utc: new Date().toISOString(),
      updated_utc: new Date().toISOString(),
    }));
  }

  cancelJob(jobId: string): Promise<JobCancelResponse> {
    return this.post(`/jobs/${encodeURIComponent(jobId)}/cancel`, {}, () => ({
      job_id: jobId,
      cancelled: true,
    }));
  }

  previewExport(request: ExportPreviewRequest): Promise<ExportPreviewResponse> {
    return this.post("/exports/preview", request, () =>
      mockExportPreviewResponse(request.profile_id),
    );
  }

  createExport(request: CreateExportRequest): Promise<JobRecord> {
    return this.post("/exports/create", request, () => ({
      ...mockJobsResponse().jobs[0],
      id: `job_export_mock_${Date.now()}`,
      kind: "export_profile",
      registry_id: request.profile_id,
      entrypoint: "create",
      status: "queued",
      created_utc: new Date().toISOString(),
      updated_utc: new Date().toISOString(),
      params: {
        profile_id: request.profile_id,
        start: request.start,
        end: request.end,
        output_path: request.output_path ?? "",
      },
    }));
  }

  validateExport(request: ValidateExportRequest): Promise<ValidateExportResponse> {
    return this.post("/exports/validate", request, mockValidateExportResponse);
  }

  compareExports(request: CompareExportsRequest): Promise<CompareExportsResponse> {
    return this.post("/exports/compare", request, () => ({
      left: { ...mockExport, id: request.left_export_id },
      right: { ...mockExport, id: request.right_export_id },
      table_deltas: {
        raw_payloads: -18823,
        events: 0,
        weather_snapshots: 0,
        market_snapshots: 0,
        settlements: 0,
      },
      city_delta: [],
      warnings: ["right export excludes raw payload storage"],
    }));
  }

  cloneExport(request: CloneExportRequest): Promise<ArtifactMetadata> {
    return this.post("/exports/clone", request, () => ({
      ...mockExport,
      id: pathBasename(request.destination) || request.export_id,
      path: request.destination,
    }));
  }

  reduceExport(request: ReduceExportRequest): Promise<ArtifactMetadata> {
    return this.post("/exports/reduce", request, () => ({
      ...mockExport,
      id: pathBasename(request.destination) || request.export_id,
      path: request.destination,
      files: request.tables ?? mockExport.files,
      coverage: {
        ...mockExport.coverage!,
        cities: request.cities ?? mockExport.coverage!.cities,
        date_range: {
          start: request.start ?? mockExport.coverage!.date_range.start,
          end: request.end ?? mockExport.coverage!.date_range.end,
        },
      },
    }));
  }

  extendExport(request: ExtendExportRequest): Promise<ArtifactMetadata> {
    return this.post("/exports/extend", request, () => ({
      ...mockExport,
      id: pathBasename(request.destination) || request.export_id,
      path: request.destination,
    }));
  }

  archiveExport(request: ArchiveExportRequest): Promise<ArtifactMetadata> {
    return this.post("/exports/archive", request, () => ({
      ...mockExport,
      id: `${request.export_id}_archived`,
      path: `data/.archive/${request.export_id}_mock`,
      status: "complete",
    }));
  }

  modelRunCompatibility(
    request: ModelRunCompatibilityRequest,
  ): Promise<ModelRunCompatibilityResponse> {
    return this.post("/compatibility/model-run", request, () =>
      mockModelRunCompatibilityResponse(request.dataset_path),
    );
  }

  botStatus(resource = "status"): Promise<BotStatusResponse> {
    return this.get(`/bot/${encodeURIComponent(resource)}`, () => ({
      status: "deferred",
      resource,
      message: "Bot runtime monitoring is registered but not configured.",
    }));
  }

  visualizationQuery(
    request: VisualizationQueryRequest,
  ): Promise<VisualizationQueryResponse> {
    return this.post("/visualizations/query", request, () =>
      mockVisualizationQueryResponse(request.query),
    );
  }

  getWithMeta<T>(path: string, fallback: () => T): Promise<ApiResult<T>> {
    return this.requestWithMeta<T>("GET", path, undefined, fallback);
  }

  postWithMeta<T>(
    path: string,
    body: unknown,
    fallback: () => T,
  ): Promise<ApiResult<T>> {
    return this.requestWithMeta<T>("POST", path, body, fallback);
  }

  private async get<T>(path: string, fallback: () => T): Promise<T> {
    const result = await this.requestWithMeta<T>("GET", path, undefined, fallback);
    return result.data;
  }

  private async post<T>(
    path: string,
    body: unknown,
    fallback: () => T,
  ): Promise<T> {
    const result = await this.requestWithMeta<T>("POST", path, body, fallback);
    return result.data;
  }

  private async requestWithMeta<T>(
    method: "GET" | "POST",
    path: string,
    body: unknown,
    fallback: () => T,
  ): Promise<ApiResult<T>> {
    if (this.config.mockMode === "always") {
      const data = fallback();
      const status = this.setStatus("mock", "mock", null);
      return { data, source: "mock", status };
    }

    const controller = new AbortController();
    const timeout = globalThis.setTimeout(
      () => controller.abort(),
      this.config.requestTimeoutMs,
    );

    try {
      const response = await this.fetcher(toUrl(this.config.baseUrl, path), {
        method,
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const payload = await readJson(response);
      if (!response.ok) {
        throw new ControlCenterApiError(
          errorMessage(payload, response.statusText),
          response.status,
          payload,
        );
      }
      const status = this.setStatus("live", "live", null);
      return { data: payload as T, source: "live", status };
    } catch (error) {
      if (this.config.mockMode === "on-error") {
        const data = fallback();
        const status = this.setStatus("mock", "mock", readableError(error));
        return { data, source: "mock", status };
      }
      this.setStatus(errorState(error), null, readableError(error));
      throw error;
    } finally {
      globalThis.clearTimeout(timeout);
    }
  }

  private setStatus(
    state: ControlCenterApiStatus["state"],
    source: ApiResultSource | null,
    error: string | null,
  ): ControlCenterApiStatus {
    const checked = new Date().toISOString();
    this.status = {
      ...this.status,
      state,
      source,
      lastCheckedUtc: checked,
      lastSuccessUtc: state === "live" || state === "mock" ? checked : this.status.lastSuccessUtc,
      lastError: error,
    };
    const snapshot = this.getStatus();
    for (const listener of this.requestListeners) {
      listener(snapshot);
    }
    return snapshot;
  }
}

export function createControlCenterApi(
  options?: ControlCenterApiOptions,
): ControlCenterApiClient {
  return new ControlCenterApiClient(options);
}

export function readControlCenterApiEnv(): ControlCenterApiConfig {
  const env = runtimeEnv();
  return {
    baseUrl: normalizeBaseUrl(envString(env, "VITE_CONTROL_API_BASE_URL", "/control/api")),
    mockMode: parseMockMode(envString(env, "VITE_CONTROL_MOCK_MODE", "never")),
    requestTimeoutMs: parsePositiveInt(
      envString(env, "VITE_CONTROL_REQUEST_TIMEOUT_MS", "15000"),
      15_000,
    ),
  };
}

const defaultApi = createControlCenterApi();

export function getDefaultControlCenterApi(): ControlCenterApiClient {
  return defaultApi;
}

export function getControlCenterApiStatus(): ControlCenterApiStatus {
  return defaultApi.getStatus();
}

export function subscribeControlCenterApiStatus(
  listener: (status: ControlCenterApiStatus) => void,
): () => void {
  return defaultApi.subscribeStatus(listener);
}

export function checkControlCenterApi(): Promise<ControlCenterApiStatus> {
  return defaultApi.checkConnection();
}

export async function loadControlCenter(): Promise<UiControlCenterState> {
  const [
    dashboard,
    exportsPayload,
    modelsPayload,
    profilesPayload,
    jobsPayload,
  ] = await Promise.all([
    defaultApi.dashboard(),
    defaultApi.exports(),
    defaultApi.models(),
    defaultApi.exportProfiles(),
    defaultApi.jobs(),
  ]);

  return {
    dashboard,
    exports: exportsPayload.exports,
    models: modelsPayload.models,
    strategies: modelsPayload.strategies,
    profiles: profilesPayload.export_profiles,
    jobs: jobsPayload.jobs,
  } as unknown as UiControlCenterState;
}

export function createExportJob(request: CreateExportRequest): Promise<JobRecord> {
  return defaultApi.createExport(request);
}

export function runModelJob(request: CreateJobRequest): Promise<JobRecord> {
  return defaultApi.createJob(request);
}

export function cancelJob(jobId: string): Promise<JobCancelResponse> {
  return defaultApi.cancelJob(jobId);
}

export function checkModelRunCompatibility(
  request: ModelRunCompatibilityRequest,
): Promise<ModelRunCompatibilityResponse> {
  return defaultApi.modelRunCompatibility(request);
}

export function previewExport(
  request: ExportPreviewRequest,
): Promise<ExportPreviewResponse> {
  return defaultApi.previewExport(request);
}

export function validateExport(
  request: ValidateExportRequest,
): Promise<ValidateExportResponse> {
  return defaultApi.validateExport(request);
}

export function compareExports(
  request: CompareExportsRequest,
): Promise<CompareExportsResponse> {
  return defaultApi.compareExports(request);
}

export function cloneExport(request: CloneExportRequest): Promise<ArtifactMetadata> {
  return defaultApi.cloneExport(request);
}

export function reduceExport(
  request: ReduceExportRequest,
): Promise<ArtifactMetadata> {
  return defaultApi.reduceExport(request);
}

export function extendExport(
  request: ExtendExportRequest,
): Promise<ArtifactMetadata> {
  return defaultApi.extendExport(request);
}

export function archiveExport(
  request: ArchiveExportRequest,
): Promise<ArtifactMetadata> {
  return defaultApi.archiveExport(request);
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, "") || "/control/api";
}

function toUrl(baseUrl: string, path: string): string {
  return `${baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
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
  return fallback || "Control Center API request failed";
}

function readableError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function errorState(error: unknown): ControlCenterApiStatus["state"] {
  if (error instanceof ControlCenterApiError) {
    return "error";
  }
  if (
    error instanceof DOMException &&
    (error.name === "AbortError" || error.name === "TimeoutError")
  ) {
    return "offline";
  }
  if (error instanceof TypeError) {
    return "offline";
  }
  return "error";
}

function parseMockMode(value: string): MockMode {
  if (value === "never" || value === "on-error" || value === "always") {
    return value;
  }
  return "never";
}

function parsePositiveInt(value: string, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function envString(
  env: Record<string, unknown>,
  key: string,
  fallback: string,
): string {
  const value = env[key];
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function runtimeEnv(): Record<string, unknown> {
  const meta = import.meta as ImportMeta & {
    readonly env?: Record<string, unknown>;
  };
  return meta.env ?? {};
}

function pathBasename(value: string): string {
  return value.split(/[\\/]/).filter(Boolean).pop() ?? "";
}

function mockExportProfilesFromRegistry() {
  return {
    export_profiles:
      mockRegistryResponse().entries.export_profile?.map((entry) => entry) ?? [],
  };
}
