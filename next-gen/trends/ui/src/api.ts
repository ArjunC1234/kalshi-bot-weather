import type {
  AnalysisResponse,
  LoadResponse,
  SeriesResponse,
  SourcesResponse,
  TableResponse,
} from "./types";

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const value = (await response.json()) as T & { error?: string };
  if (!response.ok) {
    throw new Error(value.error || response.statusText);
  }
  return value as T;
}

export function getSources(): Promise<SourcesResponse> {
  return jsonRequest<SourcesResponse>("/api/sources");
}

export function loadWorkbench(request: {
  export_id: string;
  report_id?: string;
  quality_id?: string;
  strategy_id?: string;
}): Promise<LoadResponse> {
  return jsonRequest<LoadResponse>("/api/load", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function getSeries(metric: string): Promise<SeriesResponse> {
  return jsonRequest<SeriesResponse>(`/api/series/${encodeURIComponent(metric)}`);
}

export function getAnalysis<T = unknown>(section: string): Promise<AnalysisResponse<T>> {
  return jsonRequest<AnalysisResponse<T>>(`/api/analysis/${encodeURIComponent(section)}`);
}

export function getTable(tableName: string): Promise<TableResponse> {
  return jsonRequest<TableResponse>(`/api/table/${encodeURIComponent(tableName)}`);
}
