export type DataRow = Record<string, unknown>;

export type SourceInfo = {
  id: string;
  name: string;
  kind: string;
  modified_utc: string;
  files: string[];
  file_count: number;
  date_start?: string | null;
  date_end?: string | null;
  created_utc?: string | null;
  source_export_id?: string;
  source_export_inferred?: boolean;
  model_name?: string;
  mode?: string;
  temperature_metrics?: Array<{ metric: string; value: number }>;
  bracket_metrics?: Array<{ metric: string; value: number }>;
  trades?: number;
  total_pnl?: number;
  roi?: number;
  hit_rate?: number;
};

export type SourcesResponse = {
  roots: Record<string, string>;
  exports: SourceInfo[];
  reports: SourceInfo[];
  quality_reports: SourceInfo[];
  strategy_reports: SourceInfo[];
};

export type MetricInfo = {
  key: string;
  label: string;
  description: string;
};

export type ModeInfo = {
  key: ViewMode;
  label: string;
  requires_report?: boolean;
  requires_strategy?: boolean;
};

export type EventInfo = DataRow & {
  event_key: string;
  label: string;
  city?: string;
  event_ticker?: string;
  target_date?: string;
};

export type Catalog = {
  modes: ModeInfo[];
  events: EventInfo[];
  cities: string[];
  models: string[];
  metrics: MetricInfo[];
  tables: string[];
  checkpoints: string[];
  feature_columns: string[];
};

export type Metadata = {
  data_dir: string;
  report_dirs: string[];
  quality_report: string | null;
  strategy_report_dirs: string[];
  cities: string[];
  models: string[];
  date_range: { start: string | null; end: string | null };
  has_model_reports: boolean;
  has_strategy_reports: boolean;
  table_counts: Record<string, number>;
  metrics: MetricInfo[];
  artifact_date_range?: { start: string | null; end: string | null };
};

export type Selection = {
  export_id: string;
  report_id: string;
  quality_id: string;
  strategy_id: string;
  data_path: string;
  report_path: string | null;
  quality_path: string | null;
  strategy_path: string | null;
};

export type LoadResponse = {
  metadata: Metadata;
  catalog: Catalog;
  overview: DataRow;
  selection: Selection;
};

export type SeriesResponse = {
  metric: string;
  rows: DataRow[];
};

export type AnalysisResponse<T = unknown> = {
  section: string;
  data: T;
};

export type TableResponse = {
  name: string;
  rows: DataRow[];
};

export type EventReplay = EventInfo & {
  final_high_f?: number | null;
  winner_ticker?: string | null;
  winner_label?: string | null;
  settlement_temperature_f?: number | null;
  timeline: DataRow[];
  bracket_probabilities: DataRow[];
  market_model_points: DataRow[];
};

export type StrategyAnalysis = {
  available: boolean;
  summaries: DataRow[];
  overview: DataRow;
  daily_pnl: DataRow[];
  trades: DataRow[];
  threshold_sweep: DataRow[];
  candidate_points: DataRow[];
  bucket_rows: DataRow[];
  policy_calibration: DataRow[];
  ranking_diagnostics: DataRow[];
  city_metrics: DataRow[];
  side_metrics: DataRow[];
};

export type ViewMode =
  | "overview"
  | "trends"
  | "replay"
  | "performance"
  | "features"
  | "disagreement"
  | "market-model"
  | "calibration"
  | "strategy"
  | "settlements"
  | "quality"
  | "tables";

export type ThemeMode = "dark" | "light";
