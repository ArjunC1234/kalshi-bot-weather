import type {
  ArtifactMetadata,
  DashboardSummary,
  ExportCoverage,
  ExportPreviewResponse,
  ExportProfilesResponse,
  ExportsResponse,
  JobLogsResponse,
  JobRecord,
  JobsResponse,
  ModelRunCompatibilityResponse,
  ModelsResponse,
  RegistryEntry,
  RegistryResponse,
  ReportsResponse,
  ValidateExportResponse,
  VisualizationQuery,
  VisualizationQueryResponse,
} from "../types/index";

const now = "2026-07-22T12:00:00.000Z";

export const mockCoverage: ExportCoverage = {
  cities: ["aus", "den", "la", "mia", "nyc", "okc"],
  date_range: { start: "2026-07-02", end: "2026-07-21" },
  target_dates: 20,
  snapshot_hours: Array.from({ length: 24 }, (_, hour) => hour),
  row_counts: {
    collector_runs: 39,
    raw_payloads: 18823,
    events: 96,
    weather_snapshots: 2136,
    market_snapshots: 12816,
    settlements: 90,
    final_temperature_labels: 90,
    provider_errors: 4320,
  },
  settlement_coverage: {
    "2026-07-06": 0,
    "2026-07-07": 6,
    "2026-07-08": 6,
    "2026-07-09": 6,
    "2026-07-10": 6,
    "2026-07-11": 6,
  },
  final_label_coverage: {
    "2026-07-06": 0,
    "2026-07-07": 6,
    "2026-07-08": 6,
    "2026-07-09": 6,
    "2026-07-10": 6,
    "2026-07-11": 6,
  },
  missing_city_hours: [
    { city: "aus", date: "2026-07-06", table: "settlements" },
    { city: "den", date: "2026-07-06", table: "settlements" },
    { city: "la", date: "2026-07-06", table: "settlements" },
    { city: "mia", date: "2026-07-06", table: "settlements" },
    { city: "nyc", date: "2026-07-06", table: "settlements" },
    { city: "okc", date: "2026-07-06", table: "settlements" },
  ],
};

export const mockExport: ArtifactMetadata = {
  id: "export_20260702_20260721_20260721T190238Z",
  artifact_type: "local_export",
  path: "data/export_20260702_20260721_20260721T190238Z",
  status: "complete",
  modified_utc: now,
  created_utc: "2026-07-21T19:02:38.000Z",
  files: [
    "collector_runs",
    "raw_payloads",
    "events",
    "market_snapshots",
    "weather_snapshots",
    "settlements",
    "final_temperature_labels",
    "provider_errors",
  ],
  table_counts: mockCoverage.row_counts,
  excluded_columns: {
    raw_payloads: ["payload"],
    market_snapshots: ["raw_payload_id"],
    weather_snapshots: ["raw_payload_id"],
  },
  coverage: mockCoverage,
  metadata_health: {
    has_manifest: true,
    has_run_manifest: true,
    has_schemas: true,
    status: "complete",
    warnings: ["settlements missing for 2026-07-06 across all selected cities"],
  },
  schemas: {
    events: {
      row_grain: ["city", "event_ticker", "target_date"],
      primary_time_column: "target_date",
      row_count: 96,
      columns: {
        city: { type: "string", role: "city" },
        event_ticker: { type: "string", role: "event" },
        target_date: { type: "date", role: "target_date" },
        station_id: { type: "string" },
      },
    },
    weather_snapshots: {
      row_grain: ["city", "event_ticker", "target_date", "snapshot_time_utc"],
      primary_time_column: "snapshot_time_utc",
      row_count: 2136,
      columns: {
        city: { type: "string", role: "city" },
        event_ticker: { type: "string", role: "event" },
        target_date: { type: "date", role: "target_date" },
        snapshot_time_utc: { type: "datetime", role: "snapshot_time" },
        nws_anchor_high_f: { type: "number", role: "weather_feature" },
        hrrr_projected_high_f: { type: "number", role: "weather_feature" },
        nbm_projected_high_f: { type: "number", role: "weather_feature" },
        ensemble_raw_median_high_f: { type: "number", role: "weather_feature" },
        features: { type: "object", role: "feature_blob" },
      },
    },
    market_snapshots: {
      row_grain: [
        "city",
        "event_ticker",
        "market_ticker",
        "target_date",
        "snapshot_time_utc",
      ],
      primary_time_column: "snapshot_time_utc",
      row_count: 12816,
      columns: {
        city: { type: "string", role: "city" },
        event_ticker: { type: "string", role: "event" },
        market_ticker: { type: "string", role: "market" },
        target_date: { type: "date", role: "target_date" },
        snapshot_time_utc: { type: "datetime", role: "snapshot_time" },
        bracket_index: { type: "integer", role: "market_bracket" },
        bracket_lower_f: { type: "number" },
        bracket_upper_f: { type: "number" },
        yes_bid_dollars: { type: "number", role: "market_price" },
        yes_ask_dollars: { type: "number", role: "market_price" },
        normalized_market_midpoint_probability: {
          type: "number",
          role: "market_probability",
        },
      },
    },
    settlements: {
      row_grain: ["city", "event_ticker", "target_date"],
      primary_time_column: "target_date",
      row_count: 90,
      columns: {
        city: { type: "string", role: "city" },
        event_ticker: { type: "string", role: "event" },
        target_date: { type: "date", role: "target_date" },
        settled_high_f: { type: "number", role: "label" },
      },
    },
  },
};

export const mockLightweightExport: ArtifactMetadata = {
  ...mockExport,
  id: "export_lightweight_model_eval_20260702_20260721",
  path: "data/export_lightweight_model_eval_20260702_20260721",
  created_utc: "2026-07-21T19:30:05.000Z",
  modified_utc: "2026-07-21T19:30:05.000Z",
  files: [
    "events",
    "market_snapshots",
    "weather_snapshots",
    "settlements",
    "final_temperature_labels",
  ],
  table_counts: {
    events: 96,
    market_snapshots: 12816,
    weather_snapshots: 2136,
    settlements: 90,
    final_temperature_labels: 90,
  },
  excluded_columns: {
    market_snapshots: ["raw_payload_id"],
    weather_snapshots: ["raw_payload_id"],
  },
};

export const mockQualityReport: ArtifactMetadata = {
  id: "daily_health_20260720_export_20260702_20260721",
  artifact_type: "quality_report",
  path: "reports/quality/daily_health_20260720_export_20260702_20260721",
  status: "warning",
  source_export_id: mockExport.id,
  contract: "quality_report.v1",
  created_utc: "2026-07-21T19:03:05.000Z",
  modified_utc: "2026-07-21T19:03:05.000Z",
  files: ["summary.json", "table_counts.csv", "missing_city_hours.csv"],
  table_counts: {
    table_counts: 8,
    missing_city_hours: 6,
  },
  summary: {
    completeness: 0.98,
    freshness: 0.96,
    schema_health: 1,
    anomalies: 2,
    warnings: ["settlements missing for 2026-07-06"],
  },
};

export const mockModelReport: ArtifactMetadata = {
  id: "neuralcaster_v2_fixed_train_20260702_20260714_score_20260702_20260716",
  artifact_type: "model_report",
  path: "reports/model/neuralcaster_v2_fixed_train_20260702_20260714_score_20260702_20260716",
  status: "complete",
  source_export_id: mockExport.id,
  contract: "weather_model_report.v1",
  created_utc: "2026-07-21T19:04:12.000Z",
  modified_utc: "2026-07-21T19:04:12.000Z",
  files: [
    "summary.json",
    "predictions.csv",
    "bracket_distributions.csv",
    "errors.csv",
    "by_checkpoint.csv",
    "by_city.csv",
    "temperature_metrics.csv",
    "bracket_metrics.csv",
    "training_diagnostics.csv",
    "calibration_bins.csv",
  ],
  table_counts: {
    predictions: 2136,
    bracket_distributions: 12960,
    errors: 4320,
    by_checkpoint: 24,
    by_city: 6,
    calibration_bins: 20,
  },
  summary: {
    model_id: "neuralcaster_v2",
    mae_f: 1.42,
    rmse_f: 2.13,
    crps: 0.387,
    coverage_90: 0.892,
  },
};

export const mockStrategyReport: ArtifactMetadata = {
  id: "edgecaster_v2_fixed_train_20260702_20260714_test_20260715_20260716",
  artifact_type: "strategy_report",
  path: "reports/strategy/edgecaster_v2_fixed_train_20260702_20260714_test_20260715_20260716",
  status: "complete",
  source_export_id: mockExport.id,
  contract: "strategy_report.v1",
  created_utc: "2026-07-21T19:05:18.000Z",
  modified_utc: "2026-07-21T19:05:18.000Z",
  files: [
    "summary.json",
    "trades.csv",
    "daily_pnl.csv",
    "threshold_sweep.csv",
    "validation_threshold_sweep.csv",
    "policy_calibration.csv",
    "predictions.csv",
    "candidates.csv",
    "signals.csv",
    "orders.csv",
    "positions.csv",
    "fills.csv",
    "city_metrics.csv",
    "checkpoint_metrics.csv",
    "side_metrics.csv",
    "edge_buckets.csv",
    "probability_buckets.csv",
    "ranking_diagnostics.csv",
  ],
  table_counts: {
    trades: 148,
    daily_pnl: 16,
    threshold_sweep: 240,
    candidates: 12960,
    city_metrics: 6,
    edge_buckets: 12,
  },
  summary: {
    strategy_id: "edgecaster_v2",
    pnl: -0.76,
    trades: 148,
    roi: -0.018,
    max_drawdown: 3.4,
    best_threshold: 0.035,
  },
};

export const mockDataSource: RegistryEntry = {
  id: "supabase_weather",
  kind: "data_source",
  version: 1,
  label: "Supabase Weather Facts",
  description: "Supabase-backed normalized weather, market, event, settlement, and provider tables.",
  provider: "supabase",
  tables: {
    events: { source_table: "events" },
    market_snapshots: { source_table: "market_snapshots" },
    weather_snapshots: { source_table: "weather_snapshots" },
    settlements: { source_table: "settlements" },
    final_temperature_labels: { source_table: "final_temperature_labels" },
    provider_errors: { source_table: "provider_errors" },
    raw_payloads: { source_table: "raw_payloads" },
    collector_runs: { source_table: "collector_runs" },
  },
};

export const mockResearchExportProfile: RegistryEntry = {
  id: "weather_research_default",
  kind: "export_profile",
  label: "Weather Research Default",
  version: 1,
  source: "supabase.weather",
  description:
    "Full local research export for model evaluation, quality checks, settlement analysis, and visualization.",
  tables: {
    collector_runs: { source_table: "collector_runs", required: false },
    raw_payloads: {
      source_table: "raw_payloads",
      required: false,
      exclude_columns: ["payload"],
    },
    events: { source_table: "events", required: true },
    market_snapshots: { source_table: "market_snapshots", required: true },
    weather_snapshots: { source_table: "weather_snapshots", required: true },
    settlements: { source_table: "settlements", required: false },
    final_temperature_labels: {
      source_table: "final_temperature_labels",
      required: false,
    },
    provider_errors: { source_table: "provider_errors", required: false },
  },
};

export const mockLightweightExportProfile: RegistryEntry = {
  id: "lightweight_model_eval",
  kind: "export_profile",
  label: "Lightweight Model Evaluation",
  version: 1,
  source: "supabase.weather",
  description:
    "Compact export for model development that excludes raw payloads and collector internals.",
  tables: {
    collector_runs: {
      include: false,
      reason: "Collector metadata is not needed for model evaluation.",
    },
    raw_payloads: {
      include: false,
      reason: "Raw payloads are large and not needed for most model evaluation.",
    },
    events: {
      source_table: "events",
      required: true,
      protected_columns: ["city", "event_ticker", "target_date", "snapshot_time_utc"],
      required_columns: [
        "climate_day_start_utc",
        "climate_day_end_utc",
        "station_id",
      ],
    },
    market_snapshots: {
      source_table: "market_snapshots",
      required: true,
      protected_columns: [
        "city",
        "event_ticker",
        "market_ticker",
        "target_date",
        "snapshot_time_utc",
      ],
      required_columns: [
        "bracket_index",
        "bracket_lower_f",
        "bracket_upper_f",
        "yes_bid_dollars",
        "yes_ask_dollars",
        "no_bid_dollars",
        "no_ask_dollars",
        "normalized_market_midpoint_probability",
      ],
      exclude_columns: ["raw_payload_id"],
    },
    weather_snapshots: {
      source_table: "weather_snapshots",
      required: true,
      protected_columns: ["city", "event_ticker", "target_date", "snapshot_time_utc"],
      required_columns: [
        "nws_anchor_high_f",
        "observed_high_so_far_f",
        "hrrr_projected_high_f",
        "nbm_projected_high_f",
        "ensemble_raw_median_high_f",
        "features",
      ],
      exclude_columns: ["raw_payload_id"],
    },
    settlements: { source_table: "settlements", required: false },
    final_temperature_labels: {
      source_table: "final_temperature_labels",
      required: false,
    },
    provider_errors: {
      include: false,
      reason: "Provider failures are quality diagnostics, not model features.",
    },
  },
};

export const mockNeuralcasterModel: RegistryEntry = {
  id: "neuralcaster_v2",
  kind: "model",
  version: 2,
  label: "Neuralcaster v2",
  description:
    "Temporal neural weather model with rolling and fixed-window evaluation outputs.",
  code: { working_directory: "." },
  entrypoints: {
    rolling_eval: {
      label: "Rolling Evaluation",
      command: [
        "python",
        "maxtemp-engine/neuralcaster/v2/cli.py",
        "rolling-eval",
        "--data",
        "{{ inputs.dataset.path }}",
        "--output",
        "{{ outputs.report_dir }}",
      ],
      params_schema: {
        type: "object",
        properties: {
          mode: {
            type: "string",
            enum: ["weather", "market"],
            default: "weather",
          },
          epochs: {
            type: "integer",
            minimum: 1,
            maximum: 500,
            default: 350,
          },
          train_days: { type: "integer", default: 5 },
          test_days: { type: "integer", default: 1 },
        },
      },
      produces: {
        artifact_type: "model_report",
        contract: "weather_model_report.v1",
      },
    },
  },
  inputs: {
    dataset: {
      type: "local_export",
      required_tables: ["events", "weather_snapshots", "market_snapshots"],
      optional_tables: ["settlements", "final_temperature_labels"],
    },
  },
  artifact_contract: {
    required_files: ["summary.json", "predictions.csv", "bracket_distributions.csv"],
    optional_files: [
      "errors.csv",
      "by_checkpoint.csv",
      "by_city.csv",
      "temperature_metrics.csv",
      "bracket_metrics.csv",
      "training_diagnostics.csv",
    ],
  },
  semantic_outputs: {
    prediction_time: "snapshot_time_utc",
    target_date: "target_date",
    city: "city",
    event: "event_ticker",
    point_prediction: "expected_high_f",
    probability_distribution: "probabilities",
  },
};

export const mockRaycasterModel: RegistryEntry = {
  id: "raycaster_v1",
  kind: "model",
  version: 1,
  label: "Raycaster v1",
  description: "Weather-only final high model with bracket probability outputs.",
  code: { working_directory: "." },
  entrypoints: {
    evaluate: {
      label: "Evaluate",
      command: [
        "python",
        "maxtemp-engine/raycaster/v1/cli.py",
        "evaluate",
        "--data",
        "{{ inputs.dataset.path }}",
        "--output",
        "{{ outputs.report_dir }}",
      ],
      params_schema: {
        type: "object",
        properties: {
          mode: {
            type: "string",
            enum: ["expanding", "rolling", "fixed"],
            default: "expanding",
          },
        },
      },
      produces: {
        artifact_type: "model_report",
        contract: "weather_model_report.v1",
      },
    },
    rolling_eval: {
      label: "Rolling Evaluation",
      command: [
        "python",
        "maxtemp-engine/raycaster/v1/cli.py",
        "rolling-eval",
        "--data",
        "{{ inputs.dataset.path }}",
        "--output",
        "{{ outputs.report_dir }}",
      ],
      params_schema: {
        type: "object",
        properties: {
          train_days: { type: "integer", default: 14 },
          test_days: { type: "integer", default: 1 },
          feature_profile: { type: "string", default: "weather_only" },
        },
      },
      produces: {
        artifact_type: "model_report",
        contract: "weather_model_report.v1",
      },
    },
  },
  inputs: mockNeuralcasterModel.inputs,
  artifact_contract: mockNeuralcasterModel.artifact_contract,
  semantic_outputs: mockNeuralcasterModel.semantic_outputs,
};

export const mockEdgecasterStrategy: RegistryEntry = {
  id: "edgecaster_v2",
  kind: "strategy",
  version: 2,
  label: "Edgecaster v2",
  description: "Candidate-set edge and reward strategy evaluator.",
  code: { working_directory: "." },
  entrypoints: {
    fixed_window: {
      label: "Fixed Window",
      command: [
        "python",
        "-m",
        "edgecaster.v2.cli",
        "fixed-window",
        "--data",
        "{{ inputs.dataset.path }}",
        "--model-report",
        "{{ inputs.model_report.path }}",
        "--output",
        "{{ outputs.report_dir }}",
      ],
      params_schema: {
        type: "object",
        properties: {
          min_predicted_reward: { type: "number", default: 0.03 },
          daily_budget: { type: "number", default: 40 },
          train_start: { type: "string", format: "date" },
          train_end: { type: "string", format: "date" },
          test_start: { type: "string", format: "date" },
          test_end: { type: "string", format: "date" },
        },
      },
      produces: {
        artifact_type: "strategy_report",
        contract: "strategy_report.v1",
      },
    },
    rolling_eval: {
      label: "Rolling Evaluation",
      command: [
        "python",
        "-m",
        "edgecaster.v2.cli",
        "rolling-eval",
        "--data",
        "{{ inputs.dataset.path }}",
        "--model-report",
        "{{ inputs.model_report.path }}",
        "--output",
        "{{ outputs.report_dir }}",
      ],
      params_schema: {
        type: "object",
        properties: {
          train_days: { type: "integer", default: 7 },
          min_predicted_reward: { type: "number", default: 0.03 },
        },
      },
      produces: {
        artifact_type: "strategy_report",
        contract: "strategy_report.v1",
      },
    },
  },
  inputs: {
    dataset: {
      type: "local_export",
      required_tables: ["events", "market_snapshots", "weather_snapshots"],
    },
    model_report: {
      type: "model_report",
      required_tables: ["predictions", "bracket_distributions"],
    },
  },
  artifact_contract: {
    required_files: ["summary.json"],
    optional_files: [
      "trades.csv",
      "daily_pnl.csv",
      "threshold_sweep.csv",
      "validation_threshold_sweep.csv",
      "policy_calibration.csv",
      "predictions.csv",
      "candidates.csv",
    ],
  },
};

export const mockWeatherModelVisualization: RegistryEntry = {
  id: "weather_model_report_v1",
  kind: "visualization",
  version: 1,
  label: "Weather Model Report",
  description: "Renderer contract for weather model diagnostics and calibration.",
  contract: "weather_model_report.v1",
};

export const mockStrategyVisualization: RegistryEntry = {
  id: "strategy_report_v1",
  kind: "visualization",
  version: 1,
  label: "Strategy Report",
  description: "Renderer contract for edge, gate, PnL, and trade diagnostics.",
  contract: "strategy_report.v1",
};

export const mockBotRuntime: RegistryEntry = {
  id: "deployed_weather_bot",
  kind: "bot_runtime",
  version: 1,
  label: "Deployed Weather Bot",
  description: "Future runtime monitor contract for live positions, orders, decisions, and telemetry.",
  status: "deferred",
};

export const mockRunningJob: JobRecord = {
  id: "job_export_20260722T120000Z",
  kind: "export_profile",
  registry_id: "weather_research_default",
  entrypoint: "create",
  status: "running",
  created_utc: "2026-07-22T11:58:20.000Z",
  updated_utc: now,
  params: {
    profile_id: "weather_research_default",
    start: "2026-07-02",
    end: "2026-07-21",
    output_path: "data/export_20260702_20260721_control",
  },
  command: [
    "python",
    "-m",
    "control.cli",
    "export",
    "--profile",
    "weather_research_default",
  ],
  cwd: ".",
  output_path: "data/export_20260702_20260721_control",
  log_path: ".control/jobs/logs/job_export_20260722T120000Z.log",
  returncode: null,
  error: null,
  progress: 0.64,
};

export const mockCompletedModelJob: JobRecord = {
  id: "job_neuralcaster_20260721T190412Z",
  kind: "model",
  registry_id: "neuralcaster_v2",
  entrypoint: "rolling_eval",
  status: "complete",
  created_utc: "2026-07-21T19:03:48.000Z",
  updated_utc: "2026-07-21T19:04:12.000Z",
  params: {
    dataset_path: mockExport.path,
    mode: "weather",
    epochs: 350,
    train_days: 5,
    test_days: 1,
  },
  command: [
    "python",
    "maxtemp-engine/neuralcaster/v2/cli.py",
    "rolling-eval",
  ],
  cwd: ".",
  output_path: mockModelReport.path,
  log_path: ".control/jobs/logs/job_neuralcaster_20260721T190412Z.log",
  returncode: 0,
  error: null,
};

export const mockFailedStrategyJob: JobRecord = {
  id: "job_edgecaster_20260721T191004Z",
  kind: "strategy",
  registry_id: "edgecaster_v2",
  entrypoint: "fixed_window",
  status: "failed",
  created_utc: "2026-07-21T19:09:34.000Z",
  updated_utc: "2026-07-21T19:10:04.000Z",
  params: {
    dataset_path: mockExport.path,
    model_report_path: mockModelReport.path,
    min_predicted_reward: 0.03,
    daily_budget: 40,
  },
  command: ["python", "-m", "edgecaster.v2.cli", "fixed-window"],
  cwd: ".",
  output_path: "reports/strategy/failed_edgecaster_mock",
  log_path: ".control/jobs/logs/job_edgecaster_20260721T191004Z.log",
  returncode: 1,
  error: "model_report_path missing required table: predictions",
};

export function mockDashboardSummary(): DashboardSummary {
  return {
    brand: "Kalshi Bot Control Center",
    artifact_counts: {
      local_export: 2,
      model_report: 1,
      quality_report: 1,
      strategy_report: 1,
    },
    exports_available: 2,
    latest_export: mockExport,
    registered_models: 2,
    registered_strategies: 1,
    registered_export_profiles: 2,
    recent_jobs: [mockRunningJob, mockCompletedModelJob, mockFailedStrategyJob],
    bot_monitoring: { status: "deferred", route: "/control/api/bot/status" },
    registry_health: {
      root: "next-gen/control/registry/builtin",
      valid: 9,
      errors: [],
    },
    warnings: ["settlements missing for target date 2026-07-06"],
  };
}

export function mockRegistryResponse(): RegistryResponse {
  return {
    root: "next-gen/control/registry/builtin",
    entries: {
      data_source: [mockDataSource],
      export_profile: [mockResearchExportProfile, mockLightweightExportProfile],
      model: [mockNeuralcasterModel, mockRaycasterModel],
      strategy: [mockEdgecasterStrategy],
      visualization: [mockWeatherModelVisualization, mockStrategyVisualization],
      bot_runtime: [mockBotRuntime],
    },
    errors: [],
  };
}

export function mockExportProfilesResponse(): ExportProfilesResponse {
  return {
    export_profiles: [mockResearchExportProfile, mockLightweightExportProfile],
  };
}

export function mockModelsResponse(): ModelsResponse {
  return {
    models: [mockNeuralcasterModel, mockRaycasterModel],
    strategies: [mockEdgecasterStrategy],
  };
}

export function mockArtifactsResponse() {
  return {
    artifacts: [
      mockExport,
      mockLightweightExport,
      mockModelReport,
      mockStrategyReport,
      mockQualityReport,
    ],
  };
}

export function mockExportsResponse(): ExportsResponse {
  return { exports: [mockExport, mockLightweightExport] };
}

export function mockReportsResponse(): ReportsResponse {
  return { reports: [mockModelReport, mockStrategyReport, mockQualityReport] };
}

export function mockJobsResponse(): JobsResponse {
  return {
    jobs: [mockRunningJob, mockCompletedModelJob, mockFailedStrategyJob],
  };
}

export function mockJobLogsResponse(jobId = mockRunningJob.id): JobLogsResponse {
  return {
    job_id: jobId,
    log: [
      `[mock] ${jobId} queued`,
      "[mock] resolved registry entry",
      "[mock] checked dataset compatibility",
      "[mock] materialized safe command",
      jobId.includes("edgecaster")
        ? "[mock] failed: model_report_path missing required table: predictions"
        : "[mock] completed successfully",
    ].join("\n"),
  };
}

export function mockExportPreviewResponse(profileId = "weather_research_default"): ExportPreviewResponse {
  const profile =
    profileId === "lightweight_model_eval"
      ? mockLightweightExportProfile
      : mockResearchExportProfile;
  const tableMap =
    profile.tables && !Array.isArray(profile.tables) ? profile.tables : {};
  const tables = Object.entries(tableMap).map(([table, spec]) => ({
    table,
    source_table: spec.source_table ?? table,
    include: spec.include !== false,
    required: Boolean(spec.required),
    protected_columns: spec.protected_columns,
    required_columns: spec.required_columns,
    include_columns: spec.include_columns,
    exclude_columns: spec.exclude_columns,
    effective_columns: spec.include_columns,
    estimated_rows: mockCoverage.row_counts[table] ?? null,
    estimated_size_bytes:
      table === "raw_payloads" ? 2_900_000_000 : (mockCoverage.row_counts[table] ?? 0) * 240,
    reason: spec.reason,
  }));
  return {
    profile_id: profileId,
    source: profile.source,
    tables,
    available_tables: tables.filter((table) => table.include).map((table) => table.table),
    estimated_rows: Object.fromEntries(
      tables.map((table) => [table.table, table.estimated_rows ?? 0]),
    ),
    estimated_size_bytes: tables.reduce(
      (sum, table) => sum + (table.estimated_size_bytes ?? 0),
      0,
    ),
    validation: {
      blocking: [],
      warnings:
        profileId === "weather_research_default"
          ? ["raw_payloads.payload is excluded to keep export size manageable"]
          : [],
    },
  };
}

export function mockValidateExportResponse(): ValidateExportResponse {
  return {
    valid: true,
    blocking: [],
    warnings: ["settlements missing for 2026-07-06"],
    export: mockExport,
  };
}

export function mockModelRunCompatibilityResponse(
  datasetPath = mockExport.path,
): ModelRunCompatibilityResponse {
  return {
    compatible: true,
    blocking: [],
    warnings: [
      "market_snapshots excludes columns: raw_payload_id",
      "weather_snapshots excludes columns: raw_payload_id",
    ],
    artifact: {
      ...mockExport,
      path: datasetPath,
    },
  };
}

export function mockVisualizationQueryResponse(
  query: Partial<VisualizationQuery> = {},
): VisualizationQueryResponse {
  const table = query.table ?? "weather_snapshots";
  const x = query.x ?? "snapshot_time_utc";
  const y = query.y ?? "nws_anchor_high_f";
  const groups = query.group ?? query.groups ?? ["city"];
  const rows = mockCoverage.cities.flatMap((city, cityIndex) =>
    Array.from({ length: 6 }, (_, dayIndex) => ({
      [x]: `2026-07-${String(dayIndex + 2).padStart(2, "0")}T12:00:00+00:00`,
      [y]: 78 + cityIndex * 2 + dayIndex,
      city,
      target_date: `2026-07-${String(dayIndex + 2).padStart(2, "0")}`,
      hour_block: query.hour_blocks ? "12:00-18:00" : null,
    })),
  );

  return {
    table,
    schema: {
      columns: {
        [x]: { type: "datetime", role: "snapshot_time" },
        [y]: { type: "number", role: "metric" },
        city: { type: "string", role: "city" },
        target_date: { type: "date", role: "target_date" },
        hour_block: { type: "string", role: "hour_block" },
      },
    },
    rows,
    metadata: {
      total_rows: mockCoverage.row_counts[table] ?? rows.length,
      filtered_rows: rows.length,
      result_rows: rows.length,
      rows_after_sampling: rows.length,
      rows_after_decimation: Math.min(rows.length, query.decimate_to ?? rows.length),
      returned_rows: rows.length,
      pagination: { page: query.page ?? 1, page_size: query.page_size ?? 500, offset: 0, has_next_page: false },
      fields: { x, y, groups, hour_blocks: query.hour_blocks ?? null },
      aggregation:
        typeof query.aggregation === "string"
          ? { op: query.aggregation }
          : {
              op: query.aggregation?.op ?? "none",
              field: query.aggregation?.field ?? null,
              as: query.aggregation?.as ?? null,
            },
      sampling: { applied: Boolean(query.sample), request: query.sample ?? null },
      decimation: {
        applied: Boolean(query.decimate_to),
        target: query.decimate_to ?? null,
      },
      density: {
        available: table !== "events",
        requested: Boolean(query.density),
      },
    },
  };
}
