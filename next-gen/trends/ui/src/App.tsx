import * as Dialog from "@radix-ui/react-dialog";
import type { EChartsCoreOption as EChartsOption } from "echarts/core";
import {
  AlertTriangle,
  BarChart3,
  Database,
  GitCompareArrows,
  Layers,
  LineChart,
  Moon,
  Play,
  RefreshCcw,
  Search,
  Settings2,
  ShieldCheck,
  Sun,
  Table2,
  Target,
  TrendingUp,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { ComponentType, ReactNode } from "react";
import { getAnalysis, getSeries, getSources, getTable, loadWorkbench } from "./api";
import { ChartPanel } from "./components/ChartPanel";
import { DataTable } from "./components/DataTable";
import { SourcePicker } from "./components/SourcePicker";
import type {
  Catalog,
  DataRow,
  EventReplay,
  LoadResponse,
  MetricInfo,
  SourceInfo,
  SourcesResponse,
  StrategyAnalysis,
  ThemeMode,
  ViewMode,
} from "./types";
import {
  asNumber,
  asText,
  cleanLabel,
  colorForIndex,
  formatCurrency,
  formatDate,
  formatDateTime,
  formatNumber,
  formatPercent,
  formatShortDateTime,
  isProbablyPercent,
  metricLabel,
  sourceLabel,
  titleCase,
  unique,
} from "./utils";

type PickerState = {
  exportId: string;
  reportId: string;
  qualityId: string;
  strategyId: string;
};

type DateMode = "range" | "single";
type MetricFamily = "all" | "temperature" | "temperature_delta" | "spread_error" | "probability_price" | "score_count";
type SnapshotInterval = "all" | "3h" | "6h" | "12h" | "24h";

type ViewState = {
  dateMode: DateMode;
  start: string;
  end: string;
  date: string;
  metricFamily: MetricFamily;
  snapshotInterval: SnapshotInterval;
  cities: string[];
  model: string;
  checkpoint: string;
};

type ViewFilterOptions = {
  groupKey?: string;
  timeKeys?: string[];
};

const METRIC_FAMILIES: Array<{ value: MetricFamily; label: string }> = [
  { value: "all", label: "All metric families" },
  { value: "temperature", label: "Temperature F" },
  { value: "temperature_delta", label: "Delta / signed error" },
  { value: "spread_error", label: "Spread / absolute error" },
  { value: "probability_price", label: "Probability / price" },
  { value: "score_count", label: "Score / count" },
];

const SNAPSHOT_INTERVALS: Array<{ value: SnapshotInterval; label: string }> = [
  { value: "all", label: "All snapshots" },
  { value: "3h", label: "Every 3 hours" },
  { value: "6h", label: "Every 6 hours" },
  { value: "12h", label: "Every 12 hours" },
  { value: "24h", label: "Daily 00Z" },
];

const DEFAULT_VIEW_STATE: ViewState = {
  dateMode: "range",
  start: "",
  end: "",
  date: "",
  metricFamily: "all",
  snapshotInterval: "all",
  cities: [],
  model: "all",
  checkpoint: "all",
};

function readInitialViewState(): ViewState {
  const params = new URLSearchParams(window.location.search);
  return {
    ...DEFAULT_VIEW_STATE,
    dateMode: params.get("dateMode") === "single" ? "single" : "range",
    start: params.get("start") ?? "",
    end: params.get("end") ?? "",
    date: params.get("date") ?? "",
    metricFamily: coerceMetricFamily(params.get("family") ?? params.get("metricFamily")),
    snapshotInterval: coerceSnapshotInterval(params.get("interval") ?? params.get("snapshotInterval")),
    cities: (params.get("cities") ?? "")
      .split(",")
      .map((city) => city.trim())
      .filter(Boolean),
    model: params.get("model") ?? "all",
    checkpoint: params.get("checkpoint") ?? "all",
  };
}

function defaultViewStateForWorkbench(workbench: LoadResponse): ViewState {
  return normalizeViewState(
    {
      ...DEFAULT_VIEW_STATE,
      start: workbench.metadata.date_range.start ?? workbench.metadata.artifact_date_range?.start ?? "",
      end: workbench.metadata.date_range.end ?? workbench.metadata.artifact_date_range?.end ?? "",
      date:
        workbench.metadata.date_range.start ??
        workbench.metadata.artifact_date_range?.start ??
        workbench.metadata.date_range.end ??
        "",
    },
    workbench,
  );
}

function normalizeViewState(state: ViewState, workbench?: LoadResponse): ViewState {
  const defaulted = {
    ...DEFAULT_VIEW_STATE,
    ...state,
    metricFamily: coerceMetricFamily(state.metricFamily),
    snapshotInterval: coerceSnapshotInterval(state.snapshotInterval),
    model: state.model || "all",
    checkpoint: state.checkpoint || "all",
    cities: Array.isArray(state.cities) ? state.cities.filter(Boolean) : [],
  };
  if (!workbench) return defaulted;
  const validCities = new Set(workbench.catalog.cities);
  const validModels = new Set(workbench.catalog.models);
  const validCheckpoints = new Set(workbench.catalog.checkpoints);
  return {
    ...defaulted,
    start: defaulted.start || workbench.metadata.date_range.start || workbench.metadata.artifact_date_range?.start || "",
    end: defaulted.end || workbench.metadata.date_range.end || workbench.metadata.artifact_date_range?.end || "",
    date:
      defaulted.date ||
      workbench.metadata.date_range.start ||
      workbench.metadata.artifact_date_range?.start ||
      workbench.metadata.date_range.end ||
      "",
    cities: defaulted.cities.filter((city) => validCities.has(city)),
    model: defaulted.model === "all" || validModels.has(defaulted.model) ? defaulted.model : "all",
    checkpoint: defaulted.checkpoint === "all" || validCheckpoints.has(defaulted.checkpoint) ? defaulted.checkpoint : "all",
  };
}

function writeViewStateToParams(params: URLSearchParams, state: ViewState) {
  const normalized = normalizeViewState(state);
  const entries: Array<[string, string, keyof ViewState]> = [
    ["dateMode", normalized.dateMode, "dateMode"],
    ["start", normalized.start, "start"],
    ["end", normalized.end, "end"],
    ["date", normalized.date, "date"],
    ["family", normalized.metricFamily, "metricFamily"],
    ["interval", normalized.snapshotInterval, "snapshotInterval"],
    ["cities", normalized.cities.join(","), "cities"],
    ["model", normalized.model, "model"],
    ["checkpoint", normalized.checkpoint, "checkpoint"],
  ];
  entries.forEach(([key, value, stateKey]) => {
    if (value && value !== "all" && value !== DEFAULT_VIEW_STATE[stateKey]) params.set(key, value);
    else params.delete(key);
  });
}

function coerceMetricFamily(value: unknown): MetricFamily {
  return METRIC_FAMILIES.some((item) => item.value === value) ? (value as MetricFamily) : "all";
}

function coerceSnapshotInterval(value: unknown): SnapshotInterval {
  return SNAPSHOT_INTERVALS.some((item) => item.value === value) ? (value as SnapshotInterval) : "all";
}

const NAV_GROUPS: Array<{ label: string; modes: ViewMode[] }> = [
  { label: "Dataset", modes: ["overview", "quality", "tables"] },
  { label: "Weather", modes: ["trends", "replay", "disagreement", "settlements"] },
  { label: "Model", modes: ["performance", "features", "calibration"] },
  { label: "Market", modes: ["market-model"] },
  { label: "Strategy", modes: ["strategy"] },
];

const MODE_META: Record<ViewMode, { label: string; icon: ComponentType<{ size?: number }> }> =
  {
    overview: { label: "Overview", icon: BarChart3 },
    trends: { label: "Trend Explorer", icon: LineChart },
    replay: { label: "Event Replay", icon: Play },
    performance: { label: "Checkpoint Performance", icon: Target },
    features: { label: "Feature vs Error", icon: GitCompareArrows },
    disagreement: { label: "Source Disagreement", icon: AlertTriangle },
    "market-model": { label: "Market vs Model", icon: GitCompareArrows },
    calibration: { label: "Calibration", icon: Target },
    strategy: { label: "Strategy Lab", icon: TrendingUp },
    settlements: { label: "Settlement Grid", icon: Layers },
    quality: { label: "Data Quality", icon: ShieldCheck },
    tables: { label: "Raw Tables", icon: Table2 },
  };

const DEFAULT_TREND_METRICS = [
  "nws_anchor_by_snapshot",
  "hrrr_projected_high_by_snapshot",
  "nbm_projected_high_by_snapshot",
  "ensemble_median_by_snapshot",
  "model_expected_high_by_snapshot",
  "market_top_probability_by_snapshot",
];

const REPLAY_FIELDS = [
  ["nws_anchor_high_f", "NWS"],
  ["observed_high_so_far_f", "Observed"],
  ["hrrr_projected_high_f", "HRRR"],
  ["nbm_projected_high_f", "NBM"],
  ["ensemble_raw_median_high_f", "Ensemble"],
] as const;

export function App() {
  const [theme, setTheme] = useState<ThemeMode>(() =>
    window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark",
  );
  const [sources, setSources] = useState<SourcesResponse | null>(null);
  const [picker, setPicker] = useState<PickerState>({
    exportId: "",
    reportId: "",
    qualityId: "",
    strategyId: "",
  });
  const [workbench, setWorkbench] = useState<LoadResponse | null>(null);
  const [mode, setMode] = useState<ViewMode>(readInitialMode);
  const [viewState, setViewState] = useState<ViewState>(readInitialViewState);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sourceOpen, setSourceOpen] = useState(false);

  const refreshSources = useCallback(async () => {
    setSourceLoading(true);
    try {
      const result = await getSources();
      setSources(result);
      setPicker((current) => {
        const exportId = current.exportId || result.exports[0]?.id || "";
        const reports = linkedToExport(result.reports, exportId);
        const strategies = linkedToExport(result.strategy_reports, exportId);
        const strategyId = current.strategyId || strategies[0]?.id || "";
        const strategy = strategies.find((item) => item.id === strategyId);
        const qualityId =
          current.qualityId ||
          result.quality_reports.find((item) => item.source_export_id === exportId)?.id ||
          "";
        const reportId = current.reportId || matchingModelReportId(strategy, reports) || reports[0]?.id || "";
        return { ...current, exportId, reportId, qualityId, strategyId };
      });
    } finally {
      setSourceLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshSources().catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [refreshSources]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    if (workbench) window.scrollTo(0, 0);
  }, [workbench]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    params.set("view", mode);
    writeViewStateToParams(params, viewState);
    window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
  }, [mode, viewState]);

  async function handleLoad() {
    setLoading(true);
    setError("");
    try {
      const result = await loadWorkbench({
        export_id: picker.exportId,
        report_id: picker.reportId,
        quality_id: picker.qualityId,
        strategy_id: picker.strategyId,
      });
      setWorkbench(result);
      setViewState((current) => normalizeViewState(current, result));
      setSourceOpen(false);
      const modeInfo = result.catalog.modes.find((item) => item.key === mode);
      if (modeInfo && !canUseMode(modeInfo, result.metadata)) {
        setMode("overview");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  function handleThemeToggle() {
    const nextTheme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    setTheme(nextTheme);
  }

  const activeExport = sources?.exports.find((item) => item.id === picker.exportId);
  const activeMode = MODE_META[mode];

  if (!workbench) {
    return (
      <main className="start-layout">
        <div className="brand-strip">
          <span className="brand-mark">
            <LineChart aria-hidden="true" size={24} />
          </span>
          <div>
            <p className="eyebrow">Next-Gen Research</p>
            <h1>Weather Trends</h1>
          </div>
        </div>
        {error ? <div className="notice error">{error}</div> : null}
        <SourcePicker
          sources={sources}
          value={picker}
          loadingSources={sourceLoading}
          loading={loading}
          onChange={setPicker}
          onLoad={handleLoad}
          onRefresh={() => refreshSources().catch((err) => setError(String(err)))}
        />
      </main>
    );
  }

  return (
    <main className="app-shell">
      <aside className="left-rail">
        <div className="rail-brand">
          <span className="brand-mark">
            <LineChart aria-hidden="true" size={20} />
          </span>
          <div>
            <h1>Weather Trends</h1>
            <p>{activeExport ? sourceLabel(activeExport) : "Local workbench"}</p>
          </div>
        </div>
        <nav className="mode-nav" aria-label="Workbench views">
          {NAV_GROUPS.map((group) => (
            <section key={group.label}>
              <p>{group.label}</p>
              {group.modes.map((viewMode) => {
                const info = workbench.catalog.modes.find((item) => item.key === viewMode);
                const available = !info || canUseMode(info, workbench.metadata);
                const Icon = MODE_META[viewMode].icon;
                const disabledReason = info?.requires_strategy
                  ? "Attach a strategy report to enable this view."
                  : "Attach a model report to enable this view.";
                return (
                  <button
                    key={viewMode}
                    className={mode === viewMode ? "active" : ""}
                    disabled={!available}
                    title={available ? MODE_META[viewMode].label : disabledReason}
                    type="button"
                    onClick={() => setMode(viewMode)}
                  >
                    <Icon aria-hidden="true" size={16} />
                    <span>{MODE_META[viewMode].label}</span>
                  </button>
                );
              })}
            </section>
          ))}
        </nav>
      </aside>

      <section className="workbench-pane">
        <header className="topbar">
          <div className="topbar-title">
            <p className="eyebrow">{activeMode.label}</p>
            <h2>{viewSubtitle(mode)}</h2>
          </div>
          <div className="status-pills">
            <span>Dataset {workbench.overview.event_count ? "Ready" : "Sparse"}</span>
            <span>Quality {workbench.overview.quality_available ? "Attached" : "None"}</span>
            <span>Model {workbench.metadata.has_model_reports ? "Attached" : "None"}</span>
            <span>Strategy {workbench.metadata.has_strategy_reports ? "Attached" : "None"}</span>
          </div>
          <div className="topbar-actions">
            <button className="ghost" type="button" onClick={() => setSourceOpen(true)}>
              <Database aria-hidden="true" size={16} />
              Source
            </button>
            <button
              className="icon-button ghost"
              type="button"
              onClick={handleThemeToggle}
              title="Toggle theme"
            >
              {theme === "dark" ? <Sun aria-hidden="true" size={17} /> : <Moon aria-hidden="true" size={17} />}
            </button>
          </div>
        </header>

        <section className="mobile-mode-scroll">
          {workbench.catalog.modes.map((item) => {
            const available = canUseMode(item, workbench.metadata);
            const disabledReason = item.requires_strategy
              ? "Attach a strategy report to enable this view."
              : "Attach a model report to enable this view.";
            return (
              <button
                key={item.key}
                className={mode === item.key ? "active" : ""}
                disabled={!available}
                title={available ? (MODE_META[item.key]?.label ?? item.label) : disabledReason}
                type="button"
                onClick={() => setMode(item.key)}
              >
                {MODE_META[item.key]?.label ?? item.label}
              </button>
            );
          })}
        </section>

        <section className="content-stage">
          <ViewRouter mode={mode} workbench={workbench} viewState={viewState} />
        </section>
      </section>

      <Inspector
        workbench={workbench}
        mode={mode}
        viewState={viewState}
        onChangeSource={() => setSourceOpen(true)}
        onResetView={() => setViewState(defaultViewStateForWorkbench(workbench))}
        onViewStateChange={(patch) => setViewState((current) => normalizeViewState({ ...current, ...patch }, workbench))}
      />

      <Dialog.Root open={sourceOpen} onOpenChange={setSourceOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content">
            <Dialog.Title className="sr-only">Change workbench source</Dialog.Title>
            {error ? <div className="notice error">{error}</div> : null}
            <SourcePicker
              sources={sources}
              value={picker}
              loadingSources={sourceLoading}
              loading={loading}
              onChange={setPicker}
              onLoad={handleLoad}
              onRefresh={() => refreshSources().catch((err) => setError(String(err)))}
            />
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </main>
  );
}

function ViewRouter({ mode, workbench, viewState }: { mode: ViewMode; workbench: LoadResponse; viewState: ViewState }) {
  switch (mode) {
    case "overview":
      return <OverviewView workbench={workbench} />;
    case "trends":
      return <TrendsView metrics={workbench.metadata.metrics} viewState={viewState} />;
    case "replay":
      return <ReplayView catalog={workbench.catalog} viewState={viewState} />;
    case "disagreement":
      return <DisagreementView viewState={viewState} />;
    case "performance":
      return <PerformanceView viewState={viewState} />;
    case "features":
      return <FeaturesView catalog={workbench.catalog} viewState={viewState} />;
    case "market-model":
      return <MarketModelView viewState={viewState} />;
    case "calibration":
      return <CalibrationView viewState={viewState} />;
    case "strategy":
      return <StrategyView viewState={viewState} />;
    case "settlements":
      return <SettlementsView viewState={viewState} />;
    case "quality":
      return <QualityView viewState={viewState} />;
    case "tables":
      return <TablesView catalog={workbench.catalog} tableCounts={workbench.metadata.table_counts} viewState={viewState} />;
    default:
      return <OverviewView workbench={workbench} />;
  }
}

function OverviewView({ workbench }: { workbench: LoadResponse }) {
  const counts = Object.entries(workbench.metadata.table_counts)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 12);
  const option = barOption(
    counts.map(([table, rows]) => ({ table, rows })),
    "table",
    "rows",
    "Rows by table",
  );
  return (
    <div className="view-stack">
      <div className="kpi-grid">
        <Kpi label="Events" value={workbench.overview.event_count} />
        <Kpi label="Weather snapshots" value={workbench.overview.snapshot_count} />
        <Kpi label="Market rows" value={workbench.overview.market_row_count} />
        <Kpi label="Replayable events" value={workbench.overview.events_with_replay} />
        <Kpi label="Pending settlements" value={workbench.overview.pending_settlements} tone="warn" />
        <Kpi label="Strategy PnL" value={formatCurrency(workbench.overview.strategy_total_pnl)} />
      </div>
      <div className="view-grid wide-first">
        <ChartPanel
          title="Artifact Coverage"
          subtitle="Largest loaded tables"
          option={option}
          explanation="Shows how many rows were loaded for the largest tables in the selected export. Use it to confirm whether expected raw, weather, market, model, and report artifacts are present."
        />
        <section className="summary-panel">
          <header className="panel-head">
            <div>
              <h3>Loaded Source</h3>
              <p>Selection and lineage</p>
            </div>
          </header>
          <div className="detail-list">
            <Detail label="Dataset" value={workbench.selection.export_id} />
            <Detail label="Model report" value={workbench.selection.report_id || "None"} />
            <Detail label="Quality report" value={workbench.selection.quality_id || "None"} />
            <Detail label="Strategy report" value={workbench.selection.strategy_id || "None"} />
            <Detail label="Latest snapshot" value={formatDateTime(workbench.overview.latest_snapshot_utc)} />
          </div>
        </section>
      </div>
    </div>
  );
}

function TrendsView({ metrics, viewState }: { metrics: MetricInfo[]; viewState: ViewState }) {
  const availableMetrics = metrics.filter((item) => {
    const visible = DEFAULT_TREND_METRICS.includes(item.key) || item.key.startsWith("strategy_");
    return visible && metricMatchesFamily(item.key, viewState.metricFamily);
  });
  const [metric, setMetric] = useState(availableMetrics[0]?.key ?? metrics[0]?.key ?? "");
  const { data, loading, error } = useSeries(metric);
  const rows = filterRowsForView(data?.rows ?? [], viewState, { groupKey: "group", timeKeys: ["x"] });
  useEffect(() => {
    if (availableMetrics.length && !availableMetrics.some((item) => item.key === metric)) {
      setMetric(availableMetrics[0].key);
    }
  }, [availableMetrics, metric]);
  return (
    <div className="view-stack">
      <ControlStrip>
        <label>
          Metric
          <select value={metric} onChange={(event) => setMetric(event.target.value)}>
            {availableMetrics.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
      </ControlStrip>
      <ChartPanel
        title={metricLabel(metrics, metric)}
        subtitle={loading ? "Loading series..." : `${rows.length.toLocaleString()} observations`}
        option={lineOption(rows, "x", "value", "group", metric)}
        height={460}
        explanation="Plots the selected metric over its natural x-axis, grouped by city, source, model, or strategy where available. Compare line level, slope, and gaps to spot shifts, stale data, or divergent behavior."
      />
      {error ? <div className="notice error">{error}</div> : null}
    </div>
  );
}

function ReplayView({ catalog, viewState }: { catalog: Catalog; viewState: ViewState }) {
  const catalogEvents = filterRowsForView(catalog.events, viewState) as EventReplay[];
  const [eventKey, setEventKey] = useState(catalogEvents[0]?.event_key ?? "");
  const { data, loading } = useAnalysis<EventReplay[]>("event_replays");
  const events = filterRowsForView(data ?? [], viewState) as EventReplay[];
  const eventBase = events.find((item) => item.event_key === eventKey) ?? events[0];
  const event = eventBase
    ? {
        ...eventBase,
        timeline: filterRowsForView(eventBase.timeline, viewState),
        bracket_probabilities: filterRowsForView(eventBase.bracket_probabilities, viewState),
        market_model_points: filterRowsForView(eventBase.market_model_points, viewState),
      }
    : undefined;
  useEffect(() => {
    if (!eventKey && events[0]?.event_key) setEventKey(events[0].event_key);
    if (eventKey && events.length && !events.some((item) => item.event_key === eventKey)) setEventKey(events[0].event_key);
  }, [eventKey, events]);
  if (!event) return <EmptyState label={loading ? "Loading replays..." : "No replayable events found."} />;
  return (
    <div className="view-stack">
      <ControlStrip>
        <label>
          Event
          <select value={event.event_key} onChange={(change) => setEventKey(change.target.value)}>
            {events.map((item) => (
              <option key={item.event_key} value={item.event_key}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <div className="outcome-pills">
          <span>Final high {formatNumber(event.final_high_f)}F</span>
          <span>Winner {asText(event.winner_label || event.winner_ticker) || "pending"}</span>
        </div>
      </ControlStrip>
      <ChartPanel
        title="Event Replay"
        subtitle="Weather source path, model overlays, and settlement reference"
        option={eventReplayOption(event)}
        height={480}
        explanation="Reconstructs one market event through time. Weather observations, provider projections, model expected highs, and settlement context show what was known at each snapshot before the outcome."
      />
      <div className="view-grid">
        <ChartPanel
          title="Bracket Probabilities"
          subtitle="Model distribution across market tickers"
          option={lineOption(event.bracket_probabilities, "snapshot_time_utc", "model_probability", "market_ticker", "model_probability")}
          explanation="Shows how the model assigned probability across each temperature bracket over time. Rising or falling lines indicate confidence shifting between possible settlement buckets."
        />
        <ChartPanel
          title="Market vs Model"
          subtitle="Probability and ask spread at archived snapshots"
          option={marketTimelineOption(event.market_model_points)}
          explanation="Compares market-implied probability with model probability for the selected event over time. Separation between the two indicates possible edge, stale pricing, or model disagreement."
        />
      </div>
      <DataTable
        title="Replay Timeline"
        rows={event.timeline}
        preferredColumns={[
          "snapshot_time_utc",
          "checkpoint",
          "nws_anchor_high_f",
          "observed_high_so_far_f",
          "hrrr_projected_high_f",
          "nbm_projected_high_f",
          "ensemble_raw_median_high_f",
          "market_top_probability",
          "market_top_ask",
        ]}
      />
    </div>
  );
}

function DisagreementView({ viewState }: { viewState: ViewState }) {
  const { data, loading } = useAnalysis<DataRow[]>("source_disagreement");
  const rows = filterRowsForView(data ?? [], viewState);
  const chartRows = rows.filter((row) =>
    ["source_range_f", "hrrr_minus_nws", "nbm_minus_nws", "ensemble_minus_nws", "observed_minus_nws"].some(
      (key) => metricMatchesFamily(key, viewState.metricFamily) && asNumber(row[key]) !== null,
    ),
  );
  return (
    <div className="view-stack">
      <ChartPanel
        title="Source Disagreement"
        subtitle={loading ? "Loading..." : "Mean source divergence by target date"}
        option={disagreementHeatmapOption(chartRows)}
        height={460}
        explanation="Aggregates weather-provider differences by target date. Cooler cells are negative differences, warmer cells are positive or wider source ranges; stronger color means more disagreement to investigate."
      />
      <DataTable
        title="Largest Source Ranges"
        rows={[...rows]
          .sort((left, right) => (asNumber(right.source_range_f) ?? 0) - (asNumber(left.source_range_f) ?? 0))}
        maxRows={80}
        preferredColumns={[
          "target_date",
          "city",
          "checkpoint",
          "source_range_f",
          "hrrr_minus_nws",
          "nbm_minus_nws",
          "ensemble_minus_nws",
          "observed_minus_nws",
        ]}
      />
    </div>
  );
}

function PerformanceView({ viewState }: { viewState: ViewState }) {
  const { data, loading } = useAnalysis<DataRow[]>("checkpoint_metrics");
  const rows = filterMetricRows(filterRowsForView(data ?? [], viewState), "metric", viewState.metricFamily);
  return (
    <div className="view-stack">
      <ChartPanel
        title="Checkpoint Performance"
        subtitle={loading ? "Loading..." : "Metrics by forecast checkpoint"}
        option={metricHeatmapOption(rows, "checkpoint", "metric", "value")}
        height={480}
        explanation="Summarizes model quality metrics by forecast checkpoint. Each cell is one checkpoint-metric value, making it easier to see which forecast horizons improve accuracy, calibration, or probability quality."
      />
      <DataTable title="Checkpoint Metrics" rows={rows} preferredColumns={["checkpoint", "metric_type", "metric", "value", "count", "report_name"]} />
    </div>
  );
}

function FeaturesView({ catalog, viewState }: { catalog: Catalog; viewState: ViewState }) {
  const { data, loading } = useAnalysis<DataRow[]>("feature_error_points");
  const rows = filterRowsForView(data ?? [], viewState);
  const defaultX = catalog.feature_columns.includes("source_range_f")
    ? "source_range_f"
    : catalog.feature_columns[0] ?? "absolute_error_f";
  const [xKey, setXKey] = useState(defaultX);
  return (
    <div className="view-stack">
      <ControlStrip>
        <label>
          Feature
          <select value={xKey} onChange={(event) => setXKey(event.target.value)}>
            {catalog.feature_columns.map((key) => (
              <option key={key} value={key}>
                {titleCase(key)}
              </option>
            ))}
          </select>
        </label>
      </ControlStrip>
      <ChartPanel
        title="Feature vs Error"
        subtitle={loading ? "Loading..." : "Find weather features correlated with misses"}
        option={scatterOption(rows, xKey, "absolute_error_f", "city")}
        height={470}
        explanation="Plots each prediction by the selected feature on the x-axis and absolute forecast error on the y-axis. Upward patterns or city clusters suggest features associated with larger misses."
      />
      <DataTable title="Feature Error Points" rows={rows} preferredColumns={["target_date", "city", "checkpoint", xKey, "absolute_error_f", "error_f", "model_name"]} />
    </div>
  );
}

function MarketModelView({ viewState }: { viewState: ViewState }) {
  const { data, loading } = useAnalysis<DataRow[]>("market_model_points");
  const rows = filterRowsForView(data ?? [], viewState);
  return (
    <div className="view-stack">
      <ChartPanel
        title="Market vs Model"
        subtitle={loading ? "Loading..." : "Archived market probabilities against model probabilities"}
        option={scatterOption(rows, "model_probability", "market_probability", "city", true)}
        height={480}
        explanation="Plots model probability against market probability for archived snapshots. Points above the diagonal mean the market priced the outcome higher than the model; points below mean the model was higher."
      />
      <DataTable
        title="Largest Model-Market Edges"
        rows={[...rows]
          .sort((left, right) => Math.abs(asNumber(right.model_minus_market) ?? 0) - Math.abs(asNumber(left.model_minus_market) ?? 0))}
        maxRows={120}
        preferredColumns={[
          "snapshot_time_utc",
          "city",
          "market_ticker",
          "model_probability",
          "market_probability",
          "yes_ask_dollars",
          "model_minus_market",
          "is_winner",
        ]}
      />
    </div>
  );
}

function CalibrationView({ viewState }: { viewState: ViewState }) {
  const { data, loading } = useAnalysis<DataRow[]>("calibration_bins");
  const rows = filterRowsForView(data ?? [], viewState);
  return (
    <div className="view-stack">
      <ChartPanel
        title="Reliability"
        subtitle={loading ? "Loading..." : "Predicted probability vs observed frequency"}
        option={calibrationOption(rows)}
        height={480}
        explanation="Compares predicted probabilities with observed win frequency. The dashed diagonal is perfect calibration; points above it are underconfident predictions, and points below it are overconfident predictions."
      />
      <DataTable title="Calibration Bins" rows={rows} preferredColumns={["model_name", "probability_bin", "count", "mean_probability", "observed_frequency"]} />
    </div>
  );
}

function StrategyView({ viewState }: { viewState: ViewState }) {
  const { data, loading } = useAnalysis<StrategyAnalysis>("strategy");
  const strategy = data;
  if (!strategy?.available) return <EmptyState label={loading ? "Loading..." : "No strategy report selected."} />;
  const dailyPnl = filterRowsForView(strategy.daily_pnl, viewState);
  const trades = filterRowsForView(strategy.trades, viewState, { timeKeys: ["entry_time_utc"] });
  const thresholdSweep = filterRowsForView(strategy.threshold_sweep, viewState);
  const bucketRows = filterRowsForView(strategy.bucket_rows, viewState);
  const policyCalibration = filterRowsForView(strategy.policy_calibration, viewState);
  const rankedGates = rankStrategyGates(thresholdSweep);
  const bestRoiGate = bestRowBy(thresholdSweep, "roi");
  const bestPnlGate = bestRowBy(thresholdSweep, "pnl");
  const mostTradesGate = bestRowBy(thresholdSweep, "trades");
  return (
    <div className="view-stack">
      <div className="kpi-grid">
        <Kpi label="Trades" value={strategy.overview.trades} />
        <Kpi label="Total PnL" value={formatCurrency(strategy.overview.total_pnl)} tone={asNumber(strategy.overview.total_pnl) && asNumber(strategy.overview.total_pnl)! < 0 ? "bad" : "good"} />
        <Kpi label="ROI" value={formatPercent(strategy.overview.roi)} />
        <Kpi label="Hit rate" value={formatPercent(strategy.overview.hit_rate)} />
        <Kpi label="Max drawdown" value={formatCurrency(strategy.overview.max_drawdown)} tone="warn" />
        <Kpi label="Positive CLV" value={formatPercent(strategy.overview.positive_clv_rate)} />
      </div>
      <div className="view-grid strategy-grid">
        <ChartPanel
          title="Strategy Profit Over Time"
          subtitle="Bars show daily net PnL. The line shows cumulative PnL after each target date."
          option={strategyEquityOption(dailyPnl)}
          height={420}
          explanation="Shows strategy profit and loss by target date plus cumulative PnL. Use daily bars for isolated spikes or drawdowns and the cumulative line for overall strategy trajectory."
        />
        <ChartPanel
          title="Gate Sensitivity"
          subtitle={gateChartSubtitle(thresholdSweep)}
          option={thresholdSweepOption(thresholdSweep)}
          height={420}
          explanation="Tests strategy gates across different minimum edge or reward thresholds. The best region balances return with enough trades to avoid overreading a tiny sample."
        />
      </div>
      {thresholdSweep.length ? (
        <section className="summary-panel">
          <header className="panel-head">
            <div>
              <h3>Gate Readout</h3>
              <p>Best policy gates from the current filtered sweep</p>
            </div>
          </header>
          <div className="detail-list detail-list-three">
            <Detail label="Best ROI" value={describeStrategyGate(bestRoiGate)} />
            <Detail label="Best PnL" value={describeStrategyGate(bestPnlGate)} />
            <Detail label="Most trades" value={describeStrategyGate(mostTradesGate)} />
          </div>
        </section>
      ) : null}
      <div className="view-grid">
        <ChartPanel
          title="Policy Calibration By Reward Bucket"
          subtitle="Shows whether buckets with higher predicted reward actually produced higher realized reward."
          option={barOption(policyCalibration, "group", "avg_realized_reward", "Average realized reward")}
          explanation="Groups trades by predicted reward band and shows realized average reward. Higher bars in higher predicted buckets indicate the policy ranking is aligned with actual outcomes."
        />
        <ChartPanel
          title="Bucket Results"
          subtitle="Compares the selected strategy bucket table by net PnL when available, otherwise by ROI."
          option={barOption(bucketRows, "group", bucketRows.some((row) => row.pnl !== undefined) ? "pnl" : "roi", bucketRows.some((row) => row.pnl !== undefined) ? "Net PnL" : "ROI")}
          explanation="Breaks strategy performance into report-defined buckets such as city, checkpoint, side, or edge band. Use it to identify which slices are driving gains, losses, or unstable behavior."
        />
      </div>
      {thresholdSweep.length ? (
        <DataTable
          title="Policy Gate Sweep"
          rows={rankedGates}
          maxRows={80}
          preferredColumns={[
            "min_predicted_reward",
            "min_trade_probability",
            "min_raw_edge",
            "roi",
            "pnl",
            "trades",
            "contracts",
            "risk",
            "hit_rate",
          ]}
        />
      ) : null}
      <DataTable
        title="Trades"
        rows={trades}
        preferredColumns={[
          "entry_time_utc",
          "target_date",
          "city",
          "side",
          "market_ticker",
          "entry_price",
          "model_probability",
          "edge",
          "contracts",
          "pnl",
          "roi",
          "clv",
          "hit",
        ]}
      />
    </div>
  );
}

function SettlementsView({ viewState }: { viewState: ViewState }) {
  const { data, loading } = useAnalysis<DataRow[]>("settlement_grid");
  const rows = filterRowsForView(data ?? [], viewState);
  if (!rows.length && loading) return <EmptyState label="Loading settlements..." />;
  return (
    <DataTable
      title="Settlement Grid"
      rows={rows}
      preferredColumns={[
        "target_date",
        "city",
        "settlement_status",
        "label_status",
        "final_high_f",
        "winner_label",
        "model_top_probability",
        "top_one_accuracy",
        "winner_probability",
        "absolute_error_f",
        "bracket_miss_distance",
      ]}
    />
  );
}

function QualityView({ viewState }: { viewState: ViewState }) {
  const { data, loading } = useAnalysis<Record<string, unknown>>("quality");
  const quality = data;
  const summary = (quality?.summary ?? {}) as DataRow;
  const tableCounts = rowsFromUnknown(quality?.table_counts);
  const errors = filterRowsForView(rowsFromUnknown(quality?.provider_errors), viewState);
  const missing = filterRowsForView(rowsFromUnknown(quality?.missing_city_hours), viewState, { timeKeys: ["snapshot_hour_utc"] });
  const cityCoverage = filterRowsForView(rowsFromUnknown(quality?.city_coverage), viewState);
  const providerErrorCount = totalFromRows(errors, "errors") ?? totalFromMap(summary.provider_errors) ?? 0;
  const missingCount = asNumber(summary.missing_city_hours) ?? missing.length;
  if (!quality?.available) {
    return <EmptyState label={loading ? "Loading..." : "No quality report selected."} />;
  }
  return (
    <div className="view-stack">
      <div className="kpi-grid">
        <Kpi label="Expected city-hours" value={summary.expected_city_hours} />
        <Kpi label="Actual city-hours" value={summary.actual_city_hours} />
        <Kpi label="Missing city-hours" value={missingCount} tone={missingCount ? "warn" : "good"} />
        <Kpi label="Provider errors" value={providerErrorCount} tone={providerErrorCount ? "bad" : "good"} />
        <Kpi label="Pending labels" value={summary.pending_final_highs} tone="warn" />
        <Kpi label="Pending settlements" value={summary.pending_settlements} tone="warn" />
      </div>
      <div className="view-grid">
        {tableCounts.length ? (
          <ChartPanel
            title="Quality Table Counts"
            option={barOption(tableCounts, "table", "rows", "Rows")}
            explanation="Counts rows in each quality-report table. Unexpectedly low counts point to missing coverage, failed provider pulls, or incomplete report generation."
          />
        ) : (
          <ChartPanel
            title="Daily City Coverage"
            option={barOption(cityCoverage, "city", "coverage_ratio", "Coverage")}
            explanation="Shows the share of expected weather city-hours present for each city. Lower coverage means missing observations or provider gaps that can weaken model and settlement analysis."
          />
        )}
        <DataTable title="Provider Errors" rows={errors} preferredColumns={["provider", "errors", "error_type", "error_message", "snapshot_time_utc"]} />
      </div>
      <DataTable title="Missing City-Hours" rows={missing} preferredColumns={["target_date", "snapshot_hour_utc", "city"]} />
    </div>
  );
}

function TablesView({ catalog, tableCounts, viewState }: { catalog: Catalog; tableCounts: Record<string, number>; viewState: ViewState }) {
  const defaultTable = firstNonEmptyTable(catalog.tables, tableCounts);
  const [tableName, setTableName] = useState(defaultTable);
  const { data, loading } = useTable(tableName);
  useEffect(() => {
    if (!tableName && defaultTable) setTableName(defaultTable);
  }, [defaultTable, tableName]);
  return (
    <div className="view-stack">
      <ControlStrip>
        <label>
          Table
          <select value={tableName} onChange={(event) => setTableName(event.target.value)}>
            {catalog.tables.map((name) => (
              <option key={name} value={name}>
                {titleCase(name)} ({formatNumber(tableCounts[name] ?? 0)})
              </option>
            ))}
          </select>
        </label>
      </ControlStrip>
      <DataTable title={loading ? "Loading table..." : titleCase(tableName)} rows={filterRowsForView(data?.rows ?? [], viewState)} />
    </div>
  );
}

function firstNonEmptyTable(tables: string[], tableCounts: Record<string, number>): string {
  return tables.find((name) => (tableCounts[name] ?? 0) > 0) ?? tables[0] ?? "";
}

function Inspector({
  workbench,
  mode,
  viewState,
  onChangeSource,
  onResetView,
  onViewStateChange,
}: {
  workbench: LoadResponse;
  mode: ViewMode;
  viewState: ViewState;
  onChangeSource: () => void;
  onResetView: () => void;
  onViewStateChange: (patch: Partial<ViewState>) => void;
}) {
  const counts = Object.entries(workbench.metadata.table_counts).sort((left, right) => right[1] - left[1]);
  const defaults = defaultViewStateForWorkbench(workbench);
  const activeDateStart = viewState.dateMode === "single" ? viewState.date : viewState.start;
  const activeDateEnd = viewState.dateMode === "single" ? viewState.date : viewState.end;
  const selectedCitySet = new Set(viewState.cities);
  return (
    <aside className="inspector">
      <div className="inspector-head">
        <p className="eyebrow">Inspector</p>
        <h2>{MODE_META[mode].label}</h2>
        <p>{viewSubtitle(mode)}</p>
      </div>
      <button className="ghost full-button" type="button" onClick={onResetView}>
        <RefreshCcw aria-hidden="true" size={16} />
        Reset View
      </button>
      <button className="ghost full-button" type="button" onClick={onChangeSource}>
        <Settings2 aria-hidden="true" size={16} />
        Change Sources
      </button>
      <section className="inspector-section">
        <h3>Active Scope</h3>
        <div className="detail-list">
          <Detail label="Dates" value={`${formatDate(activeDateStart)} to ${formatDate(activeDateEnd)}`} />
          <Detail label="Cities" value={viewState.cities.length ? `${viewState.cities.length} selected` : "All cities"} />
          <Detail label="Metric family" value={metricFamilyLabel(viewState.metricFamily)} />
          <Detail label="Snapshots" value={SNAPSHOT_INTERVALS.find((item) => item.value === viewState.snapshotInterval)?.label ?? "All snapshots"} />
        </div>
      </section>
      <section className="inspector-section">
        <h3>Date Filter</h3>
        <div className="segmented-control" role="group" aria-label="Date filter mode">
          <button
            className={viewState.dateMode === "range" ? "active" : ""}
            type="button"
            onClick={() => onViewStateChange({ dateMode: "range", start: viewState.start || defaults.start, end: viewState.end || defaults.end })}
          >
            Range
          </button>
          <button
            className={viewState.dateMode === "single" ? "active" : ""}
            type="button"
            onClick={() => onViewStateChange({ dateMode: "single", date: viewState.date || viewState.start || defaults.date })}
          >
            Single
          </button>
        </div>
        {viewState.dateMode === "single" ? (
          <label className="inspector-field">
            Target date
            <input
              type="date"
              min={defaults.start}
              max={defaults.end}
              value={viewState.date}
              onChange={(event) => onViewStateChange({ date: event.target.value })}
            />
          </label>
        ) : (
          <div className="inline-date-fields">
            <label className="inspector-field">
              Start
              <input
                type="date"
                min={defaults.start}
                max={defaults.end}
                value={viewState.start}
                onChange={(event) => onViewStateChange({ start: event.target.value })}
              />
            </label>
            <label className="inspector-field">
              End
              <input
                type="date"
                min={defaults.start}
                max={defaults.end}
                value={viewState.end}
                onChange={(event) => onViewStateChange({ end: event.target.value })}
              />
            </label>
          </div>
        )}
      </section>
      <section className="inspector-section">
        <h3>Analysis Controls</h3>
        <label className="inspector-field">
          Metric family
          <select value={viewState.metricFamily} onChange={(event) => onViewStateChange({ metricFamily: event.target.value as MetricFamily })}>
            {METRIC_FAMILIES.map((family) => (
              <option key={family.value} value={family.value}>
                {family.label}
              </option>
            ))}
          </select>
        </label>
        <label className="inspector-field">
          Snapshot interval
          <select value={viewState.snapshotInterval} onChange={(event) => onViewStateChange({ snapshotInterval: event.target.value as SnapshotInterval })}>
            {SNAPSHOT_INTERVALS.map((interval) => (
              <option key={interval.value} value={interval.value}>
                {interval.label}
              </option>
            ))}
          </select>
        </label>
        <label className="inspector-field">
          Model
          <select value={viewState.model} onChange={(event) => onViewStateChange({ model: event.target.value })}>
            <option value="all">All models</option>
            {workbench.metadata.models.map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </select>
        </label>
        <label className="inspector-field">
          Checkpoint
          <select value={viewState.checkpoint} onChange={(event) => onViewStateChange({ checkpoint: event.target.value })}>
            <option value="all">All checkpoints</option>
            {workbench.catalog.checkpoints.map((checkpoint) => (
              <option key={checkpoint} value={checkpoint}>
                {titleCase(checkpoint)}
              </option>
            ))}
          </select>
        </label>
      </section>
      <section className="inspector-section">
        <h3>Cities</h3>
        <div className="inspector-actions">
          <button type="button" className="tiny ghost" onClick={() => onViewStateChange({ cities: [] })}>
            All
          </button>
          <button type="button" className="tiny ghost" onClick={() => onViewStateChange({ cities: workbench.metadata.cities.slice(0, 1) })}>
            First
          </button>
        </div>
        <div className="city-chip-grid">
          {workbench.metadata.cities.map((city) => (
            <button
              key={city}
              className={!viewState.cities.length || selectedCitySet.has(city) ? "active" : ""}
              type="button"
              onClick={() => onViewStateChange({ cities: toggleCity(viewState.cities, city, workbench.metadata.cities) })}
            >
              {city}
            </button>
          ))}
        </div>
      </section>
      <section className="inspector-section">
        <h3>Dataset Range</h3>
        <div className="detail-list">
          <Detail label="Start" value={formatDate(workbench.metadata.date_range.start)} />
          <Detail label="End" value={formatDate(workbench.metadata.date_range.end)} />
          <Detail label="Artifact start" value={formatDate(workbench.metadata.artifact_date_range?.start)} />
          <Detail label="Artifact end" value={formatDate(workbench.metadata.artifact_date_range?.end)} />
        </div>
      </section>
      <section className="inspector-section">
        <h3>Largest Tables</h3>
        <div className="mini-bars">
          {counts.slice(0, 8).map(([name, rows]) => (
            <div key={name}>
              <span>{titleCase(name)}</span>
              <strong>{formatNumber(rows)}</strong>
              <i style={{ inlineSize: `${Math.max(6, (rows / Math.max(1, counts[0][1])) * 100)}%` }} />
            </div>
          ))}
        </div>
      </section>
    </aside>
  );
}

function useAnalysis<T>(section: string) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    getAnalysis<T>(section)
      .then((result) => {
        if (alive) setData(result.data);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [section]);
  return { data, loading, error };
}

function useSeries(metric: string) {
  const [data, setData] = useState<{ rows: DataRow[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!metric) return undefined;
    let alive = true;
    setLoading(true);
    setError("");
    getSeries(metric)
      .then((result) => {
        if (alive) setData({ rows: result.rows });
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [metric]);
  return { data, loading, error };
}

function useTable(tableName: string) {
  const [data, setData] = useState<{ rows: DataRow[] } | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!tableName) return undefined;
    let alive = true;
    setLoading(true);
    getTable(tableName)
      .then((result) => {
        if (alive) setData({ rows: result.rows });
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [tableName]);
  return { data, loading };
}

function canUseMode(mode: { requires_report?: boolean; requires_strategy?: boolean }, metadata: LoadResponse["metadata"]) {
  if (mode.requires_report && !metadata.has_model_reports) return false;
  if (mode.requires_strategy && !metadata.has_strategy_reports) return false;
  return true;
}

function linkedToExport(items: SourceInfo[], exportId: string): SourceInfo[] {
  if (!exportId) return items;
  return items.filter((item) => item.source_export_id === exportId);
}

function matchingModelReportId(strategy: SourceInfo | undefined, reports: SourceInfo[]): string {
  if (!strategy?.model_report_id) return "";
  return reports.find((report) => report.id === strategy.model_report_id)?.id ?? "";
}

function readInitialMode(): ViewMode {
  const value = new URLSearchParams(window.location.search).get("view") as ViewMode | null;
  return value && MODE_META[value] ? value : "overview";
}

function metricFamilyLabel(value: MetricFamily): string {
  return METRIC_FAMILIES.find((item) => item.value === value)?.label ?? "All metric families";
}

function toggleCity(selected: string[], city: string, allCities: string[]): string[] {
  const active = selected.length ? new Set(selected) : new Set(allCities);
  if (active.has(city)) active.delete(city);
  else active.add(city);
  return active.size === allCities.length ? [] : allCities.filter((item) => active.has(item));
}

function viewSubtitle(mode: ViewMode): string {
  const copy: Record<ViewMode, string> = {
    overview: "Loaded artifact coverage and current state",
    trends: "Compare metrics across time, cities, and models",
    replay: "Inspect one city-day from first snapshot to settlement",
    performance: "Model quality by checkpoint, city, and metric",
    features: "Relate weather features to model misses",
    disagreement: "Find forecast-source divergence and outliers",
    "market-model": "Compare model probabilities against archived market prices",
    calibration: "Probability reliability and observed outcomes",
    strategy: "Paper strategy PnL, thresholds, trades, and diagnostics",
    settlements: "Final highs, brackets, and miss distances",
    quality: "Collector, coverage, provider, and label health",
    tables: "Inspect raw and derived tables",
  };
  return copy[mode];
}

function Kpi({ label, value, tone }: { label: string; value: unknown; tone?: "good" | "warn" | "bad" }) {
  return (
    <div className={`kpi-card ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{typeof value === "string" ? value : formatNumber(value)}</strong>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{asText(value) || "n/a"}</strong>
    </div>
  );
}

function rowsFromUnknown(value: unknown): DataRow[] {
  return Array.isArray(value) ? (value.filter((row) => row && typeof row === "object") as DataRow[]) : [];
}

function totalFromRows(rows: DataRow[], key: string): number | null {
  if (!rows.length) return null;
  return rows.reduce((total, row) => total + (asNumber(row[key]) ?? 0), 0);
}

function totalFromMap(value: unknown): number | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return Object.values(value as Record<string, unknown>).reduce<number>(
    (total, entry) => total + (asNumber(entry) ?? 0),
    0,
  );
}

function filterMetricRows(rows: DataRow[], metricKey: string, family: MetricFamily): DataRow[] {
  if (family === "all") return rows;
  return rows.filter((row) => metricMatchesFamily(asText(row[metricKey]), family));
}

function filterRowsForView<T extends DataRow>(
  rows: T[],
  state: ViewState,
  options: { groupKey?: string; timeKeys?: string[] } = {},
): T[] {
  const intervalHours = state.snapshotInterval === "all" ? 0 : Number.parseInt(state.snapshotInterval, 10);
  return rows.filter((row) => {
    const dateText = rowDate(row, options.timeKeys);
    if (state.dateMode === "single" && state.date && dateText && dateText !== state.date) return false;
    if (state.dateMode === "range" && state.start && dateText && dateText < state.start) return false;
    if (state.dateMode === "range" && state.end && dateText && dateText > state.end) return false;

    if (state.cities.length) {
      const city = asText(row.city || (options.groupKey ? row[options.groupKey] : ""));
      if (city && !state.cities.includes(city)) return false;
    }
    if (state.model !== "all") {
      const model = asText(row.model_name || row.model || row.report_name);
      if (model && model !== state.model) return false;
    }
    if (state.checkpoint !== "all") {
      const checkpoint = asText(row.checkpoint);
      if (checkpoint && checkpoint !== state.checkpoint) return false;
    }
    if (intervalHours) {
      const timestampHour = rowTimestampHour(row, options.timeKeys);
      if (timestampHour !== null && timestampHour % intervalHours !== 0) return false;
    }
    return true;
  });
}

function metricMatchesFamily(key: string, family: MetricFamily): boolean {
  if (family === "all") return true;
  const normalized = key.toLowerCase();
  if (family === "temperature") {
    return /(high|temperature|temp|anchor|projected|expected|observed|final)/.test(normalized) && !metricMatchesFamily(key, "temperature_delta");
  }
  if (family === "temperature_delta") return /(minus|delta|bias|error_f|temperature_error)/.test(normalized) && !/absolute/.test(normalized);
  if (family === "spread_error") return /(spread|range|absolute_error|miss|mae|rmse|brier|log_loss|rps)/.test(normalized);
  if (family === "probability_price") return /(probability|price|ask|bid|midpoint|market|winner|edge|clv|hit)/.test(normalized);
  return /(score|count|rows|coverage|events|snapshots|accuracy|roi|pnl|trades)/.test(normalized);
}

function rowDate(row: DataRow, preferredKeys: string[] = []): string {
  const keys = [...preferredKeys, "target_date", "target_date_local", "date", "forecast_date", "snapshot_time_utc", "snapshot_hour_utc", "entry_time_utc", "x"];
  for (const key of keys) {
    const value = asText(row[key]);
    if (/^\d{4}-\d{2}-\d{2}/.test(value)) return value.slice(0, 10);
  }
  return "";
}

function rowTimestampHour(row: DataRow, preferredKeys: string[] = []): number | null {
  const keys = [...preferredKeys, "snapshot_time_utc", "snapshot_hour_utc", "entry_time_utc", "x"];
  for (const key of keys) {
    const value = asText(row[key]);
    if (!value) continue;
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) return date.getUTCHours();
    const match = value.match(/T(\d{2}):/);
    if (match) return Number(match[1]);
  }
  return null;
}

function ControlStrip({ children }: { children: ReactNode }) {
  return <div className="control-strip">{children}</div>;
}

function EmptyState({ label }: { label: string }) {
  return (
    <section className="empty-panel large">
      <Search aria-hidden="true" size={24} />
      <p>{label}</p>
    </section>
  );
}

function chartBase(): EChartsOption {
  const isLight = document.documentElement.dataset.theme === "light";
  return {
    backgroundColor: "transparent",
    textStyle: { color: cssVar("--text") },
    tooltip: {
      trigger: "axis",
      ...tooltipSkin(isLight),
    },
    legend: {
      top: 0,
      textStyle: { color: cssVar("--muted"), fontSize: 11 },
    },
    grid: { left: 76, right: 72, top: 52, bottom: 82, containLabel: true },
    dataZoom: [{ type: "inside" }],
  };
}

function tooltipSkin(isLight = document.documentElement.dataset.theme === "light") {
  return {
    appendToBody: true,
    confine: false,
    position: tooltipPosition(),
    backgroundColor: isLight ? "#ffffff" : "#151c21",
    borderColor: cssVar("--line"),
    textStyle: { color: cssVar("--text") },
    extraCssText:
      "max-width:min(520px,80vw);white-space:normal;overflow-wrap:anywhere;box-shadow:0 14px 34px rgba(0,0,0,.24);pointer-events:none;",
  };
}

function heatmapBaseOption(): EChartsOption {
  const isLight = document.documentElement.dataset.theme === "light";
  return {
    ...chartBase(),
    tooltip: {
      trigger: "item",
      ...tooltipSkin(isLight),
      formatter: heatmapTooltip,
    },
    grid: { left: 112, right: 24, top: 28, bottom: 112, containLabel: true },
    dataZoom: [{ type: "inside", yAxisIndex: 0 }],
    axisPointer: {
      show: true,
      snap: true,
      triggerOn: "mousemove|click",
      label: {
        show: true,
        backgroundColor: cssVar("--warn"),
        color: "#061311",
        fontWeight: 800,
      },
      lineStyle: {
        color: cssVar("--warn"),
        width: 3,
        type: "solid",
      },
      z: 20,
    },
  };
}

function heatmapYAxis(labels: string[]) {
  return {
    type: "category",
    data: labels,
    axisLabel: axisLabel(),
    axisPointer: {
      show: true,
      type: "line",
      lineStyle: {
        color: cssVar("--warn"),
        width: 3,
        type: "solid",
      },
      label: {
        show: true,
        backgroundColor: cssVar("--warn"),
        color: "#061311",
        fontWeight: 800,
      },
      z: 30,
    },
  };
}

function heatmapVisualMap(min: number, max: number) {
  return {
    show: true,
    min,
    max,
    calculable: false,
    orient: "horizontal",
    left: "center",
    bottom: 14,
    itemWidth: 14,
    itemHeight: 132,
    text: [formatNumber(max), formatNumber(min)],
    textGap: 10,
    textStyle: { color: cssVar("--muted"), fontSize: 11 },
  };
}

function heatmapSeries(data: Array<Array<string | number | null>>) {
  return {
    type: "heatmap",
    data,
    emphasis: {
      itemStyle: {
        borderColor: cssVar("--warn"),
        borderWidth: 2,
        shadowBlur: 14,
        shadowColor: "rgba(241, 185, 75, 0.42)",
      },
    },
  };
}

function valueXAxis(name: string, metricKey?: string) {
  return {
    type: "value",
    name: cleanLabel(name),
    nameLocation: "middle",
    nameGap: 54,
    nameTextStyle: axisNameTextStyle(),
    axisLabel: valueAxisLabel(metricKey ?? name),
    splitLine: splitLine(),
  };
}

function categoryXAxis(
  name: string,
  kind: "category" | "date" | "time" = "category",
  boundaryGap = true,
  data?: string[],
) {
  return {
    type: "category",
    ...(data ? { data } : {}),
    boundaryGap,
    name: cleanLabel(name),
    nameLocation: "middle",
    nameGap: 50,
    nameTextStyle: axisNameTextStyle(),
    axisLabel: axisLabel(kind),
  };
}

function valueYAxis(name: string, metricKey?: string) {
  return {
    type: "value",
    name: cleanLabel(name),
    nameLocation: "middle",
    nameGap: 58,
    nameTextStyle: axisNameTextStyle(),
    axisLabel: valueAxisLabel(metricKey ?? name),
    splitLine: splitLine(),
  };
}

function categoryYAxis(name: string, data: string[], inverse = false) {
  return {
    type: "category",
    data,
    inverse,
    name: cleanLabel(name),
    nameLocation: "middle",
    nameGap: 86,
    nameTextStyle: axisNameTextStyle(),
    axisLabel: axisLabel(),
  };
}

function axisNameTextStyle() {
  return {
    color: cssVar("--text"),
    fontSize: 12,
    fontWeight: 850,
  };
}

function heatmapTooltip(params: unknown): string {
  const item = params as { marker?: string; name?: string; value?: unknown[] };
  const value = Array.isArray(item.value) ? item.value[2] : undefined;
  const label = escapeHtml(cleanLabel(item.name || "Cell"));
  return `<div class="chart-tooltip"><span>${item.marker ?? ""}${label}</span><strong>${escapeHtml(formatNumber(value))}</strong></div>`;
}

function tooltipPosition() {
  return (
    point: number[],
    _params: unknown,
    _dom: unknown,
    _rect: unknown,
    size: { contentSize: number[]; viewSize: number[] },
  ) => {
    const gap = 12;
    const contentWidth = size.contentSize[0] ?? 0;
    const contentHeight = size.contentSize[1] ?? 0;
    const viewWidth = size.viewSize[0] ?? 0;
    const viewHeight = size.viewSize[1] ?? 0;
    const x = Math.max(gap, Math.min(point[0] + gap, viewWidth - contentWidth - gap));
    const y = Math.max(gap, Math.min(point[1] - contentHeight - gap, viewHeight - contentHeight - gap));
    return [x, y];
  };
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function lineOption(rows: DataRow[], xKey: string, yKey: string, groupKey: string, metricKey: string): EChartsOption {
  const groups = unique(rows.map((row) => row[groupKey] || "value"));
  const series = groups.map((group, index) => ({
    name: group,
    type: "line",
    showSymbol: rows.length < 120,
    symbolSize: 5,
    smooth: true,
    connectNulls: false,
    lineStyle: { width: 2, color: colorForIndex(index) },
    itemStyle: { color: colorForIndex(index) },
    data: rows
      .filter((row) => asText(row[groupKey] || "value") === group)
      .map((row) => [asText(row[xKey]), asNumber(row[yKey])]),
  }));
  return {
    ...chartBase(),
    xAxis: categoryXAxis(titleCase(xKey), "time", false),
    yAxis: valueYAxis(titleCase(metricKey), metricKey),
    series: asChartSeries(series),
  };
}

function eventReplayOption(event: EventReplay): EChartsOption {
  const timeline = event.timeline ?? [];
  const series: Record<string, unknown>[] = REPLAY_FIELDS.filter(([key]) => timeline.some((row) => asNumber(row[key]) !== null)).map(
    ([key, label], index) => ({
      name: label,
      type: "line",
      smooth: true,
      symbolSize: 4,
      lineStyle: { width: key === "observed_high_so_far_f" ? 3 : 2, color: colorForIndex(index) },
      itemStyle: { color: colorForIndex(index) },
      data: timeline.map((row) => [asText(row.snapshot_time_utc), asNumber(row[key])]),
    }),
  );
  const modelKeys = Object.keys(timeline[0] ?? {}).filter((key) => key.endsWith("_expected_high_f"));
  modelKeys.forEach((key, index) => {
    series.push({
      name: titleCase(key.replace("_expected_high_f", "")),
      type: "line",
      smooth: true,
      symbolSize: 4,
      lineStyle: { width: 2, type: "dashed", color: colorForIndex(index + REPLAY_FIELDS.length) },
      itemStyle: { color: colorForIndex(index + REPLAY_FIELDS.length) },
      data: timeline.map((row) => [asText(row.snapshot_time_utc), asNumber(row[key])]),
    });
  });
  if (asNumber(event.final_high_f) !== null && series[0]) {
    Object.assign(series[0], {
      markLine: {
        symbol: "none",
        lineStyle: { type: "dashed", color: cssVar("--bad"), width: 2 },
        label: { formatter: `Final ${formatNumber(event.final_high_f)}F`, color: cssVar("--bad") },
        data: [{ yAxis: asNumber(event.final_high_f) }],
      },
    });
  }
  return {
    ...chartBase(),
    xAxis: categoryXAxis("Snapshot time", "time", false),
    yAxis: valueYAxis("Temperature (F)", "temperature_f"),
    series: asChartSeries(series),
  };
}

function marketTimelineOption(rows: DataRow[]): EChartsOption {
  const metrics = [
    ["model_probability", "Model"],
    ["market_probability", "Market"],
    ["yes_ask_dollars", "Ask"],
    ["model_minus_market", "Edge"],
  ] as const;
  return {
    ...chartBase(),
    xAxis: categoryXAxis("Snapshot time", "time", false),
    yAxis: valueYAxis("Probability / ask price", "probability"),
    series: asChartSeries(metrics.map(([key, label], index) => ({
      name: label,
      type: "line",
      smooth: true,
      symbolSize: 4,
      lineStyle: { width: 2, color: colorForIndex(index) },
      itemStyle: { color: colorForIndex(index) },
      data: rows.map((row) => [asText(row.snapshot_time_utc), asNumber(row[key])]),
    }))),
  };
}

function disagreementHeatmapOption(rows: DataRow[]): EChartsOption {
  const metrics = ["hrrr_minus_nws", "nbm_minus_nws", "ensemble_minus_nws", "source_range_f"];
  const dates = unique(rows.map((row) => row.target_date || formatDate(row.snapshot_time_utc))).sort();
  const grouped = new Map<string, number[]>();
  for (const row of rows) {
    const date = asText(row.target_date || formatDate(row.snapshot_time_utc));
    if (!dates.includes(date)) continue;
    metrics.forEach((metric) => {
      const value = asNumber(row[metric]);
      if (value === null) return;
      const key = `${date}|${metric}`;
      grouped.set(key, [...(grouped.get(key) ?? []), value]);
    });
  }
  const data = Array.from(grouped.entries()).map(([key, values]) => {
    const [date, metric] = key.split("|");
    return [dates.indexOf(date), metrics.indexOf(metric), average(values)];
  });
  const base = heatmapBaseOption();
  return {
    ...base,
    xAxis: { type: "category", data: dates, axisLabel: axisLabel("date"), axisPointer: { show: false } },
    yAxis: heatmapYAxis(metrics.map(titleCase)),
    visualMap: {
      ...heatmapVisualMap(-8, 8),
      min: -8,
      max: 8,
      inRange: { color: ["#5c8df6", "#25323a", "#f1b94b", "#ed6a5a"] },
    },
    series: asChartSeries([heatmapSeries(data)]),
  };
}

function metricHeatmapOption(rows: DataRow[], yKey: string, xKey: string, valueKey: string): EChartsOption {
  const xs = unique(rows.map((row) => row[xKey]));
  const ys = unique(rows.map((row) => row[yKey]));
  const data = rows
    .map((row) => ({
      x: xs.indexOf(asText(row[xKey])),
      y: ys.indexOf(asText(row[yKey])),
      value: asNumber(row[valueKey]),
    }))
    .filter((item) => item.x >= 0 && item.y >= 0 && item.value !== null)
    .map((item) => [item.x, item.y, item.value]);
  const maxValue = Math.max(1, ...data.map((item) => Number(item[2])));
  const base = heatmapBaseOption();
  return {
    ...base,
    xAxis: { type: "category", data: xs.map(titleCase), axisLabel: axisLabel(), axisPointer: { show: false } },
    yAxis: heatmapYAxis(ys.map(titleCase)),
    dataZoom: [
      { type: "inside", xAxisIndex: 0 },
      { type: "inside", yAxisIndex: 0 },
    ],
    visualMap: {
      ...heatmapVisualMap(0, maxValue),
      min: 0,
      max: maxValue,
      inRange: { color: ["#25323a", "#25b8a6", "#f1b94b", "#ed6a5a"] },
    },
    series: asChartSeries([heatmapSeries(data)]),
  };
}

function scatterOption(rows: DataRow[], xKey: string, yKey: string, groupKey: string, percent = false): EChartsOption {
  const groups = unique(rows.map((row) => row[groupKey] || "value"));
  const scatterSeries = groups.map((group, index) => ({
    name: group,
    type: "scatter",
    symbolSize: 7,
    itemStyle: { color: colorForIndex(index), opacity: 0.82 },
    data: rows
      .filter((row) => asText(row[groupKey] || "value") === group)
      .map((row) => [asNumber(row[xKey]), asNumber(row[yKey])])
      .filter(([x, y]) => x !== null && y !== null),
  }));
  return {
    ...chartBase(),
    grid: { left: 76, right: 104, top: 58, bottom: 82, containLabel: true },
    xAxis: { ...valueXAxis(titleCase(xKey), percent ? "probability" : xKey), ...(percent ? { min: 0, max: 1 } : {}) },
    yAxis: { ...valueYAxis(titleCase(yKey), percent ? "probability" : yKey), ...(percent ? { min: 0, max: 1 } : {}) },
    series: asChartSeries([
      ...(percent
        ? [
            {
              name: "Parity",
              type: "line",
              symbol: "none",
              silent: true,
              lineStyle: { type: "dashed", color: cssVar("--muted"), width: 2 },
              data: [[0, 0], [1, 1]],
            },
          ]
        : []),
      ...scatterSeries,
    ]),
  };
}

function calibrationOption(rows: DataRow[]): EChartsOption {
  const modelGroups = unique(rows.map((row) => row.model_name || "model"));
  return {
    ...chartBase(),
    grid: { left: 76, right: 104, top: 58, bottom: 82, containLabel: true },
    xAxis: { ...valueXAxis("Predicted", "probability"), min: 0, max: 1 },
    yAxis: { ...valueYAxis("Observed", "probability"), min: 0, max: 1 },
    series: asChartSeries([
      {
        name: "Perfect",
        type: "line",
        symbol: "none",
        lineStyle: { type: "dashed", color: cssVar("--muted") },
        data: [[0, 0], [1, 1]],
      },
      ...modelGroups.map((group, index) => ({
        name: group,
        type: "line",
        smooth: true,
        symbolSize: 7,
        lineStyle: { width: 3, color: colorForIndex(index) },
        itemStyle: { color: colorForIndex(index) },
        data: rows
          .filter((row) => asText(row.model_name || "model") === group)
          .map((row) => [asNumber(row.mean_probability), asNumber(row.observed_frequency)]),
      })),
    ]),
  };
}

function emptyChartOption(message: string): EChartsOption {
  return {
    ...chartBase(),
    xAxis: { show: false, type: "value" },
    yAxis: { show: false, type: "value" },
    dataZoom: [],
    graphic: {
      type: "text",
      left: "center",
      top: "middle",
      style: {
        text: message,
        fill: cssVar("--muted"),
        fontSize: 13,
        fontWeight: 800,
        lineHeight: 20,
        textAlign: "center",
        width: 320,
      },
    },
    series: [],
  };
}

function strategyEquityOption(rows: DataRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No daily strategy PnL rows were found in the selected strategy report.");
  return {
    ...chartBase(),
    tooltip: {
      trigger: "axis",
      ...tooltipSkin(),
      axisPointer: {
        type: "line",
        lineStyle: {
          color: cssVar("--accent"),
          width: 1,
          type: "dashed",
        },
      },
    },
    grid: { left: 84, right: 84, top: 58, bottom: 86, containLabel: true },
    xAxis: {
      ...categoryXAxis("Target date", "date", false),
      axisPointer: {
        type: "line",
        lineStyle: {
          color: cssVar("--accent"),
          width: 1,
          type: "dashed",
        },
      },
    },
    yAxis: {
      ...valueYAxis("Profit / loss (USD)", "pnl"),
      axisLabel: { ...valueAxisLabel("pnl"), formatter: (value: number) => formatCurrency(value) },
    },
    series: asChartSeries([
      {
        name: "Daily net PnL",
        type: "bar",
        z: 1,
        itemStyle: { color: cssVar("--accent-2") },
        data: rows.map((row) => [asText(row.target_date), asNumber(row.pnl)]),
      },
      {
        name: "Cumulative net PnL",
        type: "line",
        smooth: true,
        z: 3,
        yAxisIndex: 0,
        lineStyle: { width: 3, color: cssVar("--accent") },
        itemStyle: { color: cssVar("--accent") },
        data: rows.map((row) => [asText(row.target_date), asNumber(row.cumulative_pnl)]),
      },
    ]),
  };
}

function strategyGateAxisLabel(xKey: string): string {
  if (xKey === "min_predicted_reward") return "Minimum predicted reward required to trade";
  if (xKey === "min_trade_probability") return "Minimum trade probability required";
  if (xKey === "min_raw_edge") return "Minimum raw model edge required to trade";
  return titleCase(xKey);
}

function gateChartSubtitle(rows: DataRow[]): string {
  if (!rows.length) return "No policy gate rows are available for the current source.";
  if (rows.some((row) => row.min_trade_probability !== undefined)) {
    return "Each dot is a policy gate. X/Y are gate thresholds, color is ROI, and size is trade count.";
  }
  return "Each dot is a policy gate. X is the threshold, Y/color are ROI, and size is trade count.";
}

function rankStrategyGates(rows: DataRow[]): DataRow[] {
  return [...rows].sort((left, right) => {
    const roiDelta = (asNumber(right.roi) ?? Number.NEGATIVE_INFINITY) - (asNumber(left.roi) ?? Number.NEGATIVE_INFINITY);
    if (roiDelta) return roiDelta;
    return (asNumber(right.trades) ?? 0) - (asNumber(left.trades) ?? 0);
  });
}

function bestRowBy(rows: DataRow[], key: string): DataRow | undefined {
  return rows.reduce<DataRow | undefined>((best, row) => {
    const value = asNumber(row[key]);
    if (value === null) return best;
    const bestValue = best ? asNumber(best[key]) : null;
    return bestValue === null || value > bestValue ? row : best;
  }, undefined);
}

function describeStrategyGate(row: DataRow | undefined): string {
  if (!row) return "n/a";
  return `${gateThresholdSummary(row)} | ROI ${formatPercent(row.roi)} | PnL ${formatCurrency(row.pnl)} | ${formatNumber(row.trades)} trades`;
}

function gateThresholdSummary(row: DataRow): string {
  const parts: string[] = [];
  if (asNumber(row.min_predicted_reward) !== null) parts.push(`reward >= ${formatNumber(row.min_predicted_reward)}`);
  if (asNumber(row.min_trade_probability) !== null) parts.push(`p >= ${formatPercent(row.min_trade_probability)}`);
  if (asNumber(row.min_raw_edge) !== null) parts.push(`edge >= ${formatNumber(row.min_raw_edge)}`);
  return parts.join(", ") || "gate n/a";
}

function thresholdTooltip(params: unknown): string {
  const item = params as { marker?: string; value?: unknown[] };
  const value = Array.isArray(item.value) ? item.value : [];
  const rawEdge = asNumber(value[5]);
  return [
    `<div class="chart-tooltip chart-tooltip-column">`,
    `<span>${item.marker ?? ""}Policy gate</span>`,
    rawEdge !== null ? `<strong>Raw edge: ${escapeHtml(formatNumber(rawEdge))}</strong>` : "",
    `<strong>ROI: ${escapeHtml(formatPercent(value[3]))}</strong>`,
    `<strong>PnL: ${escapeHtml(formatCurrency(value[4]))}</strong>`,
    `<strong>Trades: ${escapeHtml(formatNumber(value[2]))}</strong>`,
    `</div>`,
  ].filter(Boolean).join("");
}

function thresholdSweepOption(rows: DataRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No policy gate sweep rows were found in the selected strategy report.");
  const xKey = rows.some((row) => row.min_predicted_reward !== undefined) ? "min_predicted_reward" : "min_raw_edge";
  const yKey = rows.some((row) => row.min_trade_probability !== undefined) ? "min_trade_probability" : "roi";
  const roiValues = rows.map((row) => asNumber(row.roi)).filter((value): value is number => value !== null);
  const maxAbsRoi = Math.max(0.01, ...roiValues.map((value) => Math.abs(value)));
  const visualMin = roiValues.some((value) => value < 0) ? -maxAbsRoi : 0;
  const visualMax = maxAbsRoi;
  const points = rows
    .map((row) => [
      asNumber(row[xKey]),
      asNumber(row[yKey]),
      asNumber(row.trades),
      asNumber(row.roi),
      asNumber(row.pnl),
      asNumber(row.min_raw_edge),
      strategyGateAxisLabel(xKey),
      yKey === "roi" ? "" : strategyGateAxisLabel(yKey),
    ])
    .filter((value) => value[0] !== null && value[1] !== null);
  return {
    ...chartBase(),
    tooltip: {
      trigger: "item",
      ...tooltipSkin(),
      formatter: thresholdTooltip,
    },
    grid: { left: 84, right: 84, top: 58, bottom: 104, containLabel: true },
    xAxis: valueXAxis(strategyGateAxisLabel(xKey), xKey),
    yAxis: valueYAxis(yKey === "roi" ? "Return on investment" : strategyGateAxisLabel(yKey), yKey === "roi" ? "roi" : "probability"),
    visualMap: {
      min: visualMin,
      max: visualMax,
      dimension: 3,
      orient: "horizontal",
      left: "center",
      bottom: 14,
      itemWidth: 14,
      itemHeight: 148,
      text: ["Higher ROI", "Lower ROI"],
      inRange: { color: ["#ed6a5a", "#25323a", "#25b8a6"] },
      textStyle: { color: cssVar("--muted") },
    },
    series: asChartSeries([
      {
        name: "Policy gate",
        type: "scatter",
        symbolSize: (value: unknown[]) => Math.max(5, Math.min(22, Math.sqrt(Number(value[2]) || 0) * 4)),
        itemStyle: { opacity: 0.84 },
        data: points,
      },
    ]),
  };
}

function barOption(rows: DataRow[], xKey: string, yKey: string, title: string): EChartsOption {
  if (!rows.length) return emptyChartOption(`No ${cleanLabel(title).toLowerCase()} rows were found for this chart.`);
  const labels = rows.map((row) => titleCase(row[xKey]));
  const horizontal = rows.length > 8 || labels.some((label) => label.length > 14);
  if (horizontal) {
    return {
      ...chartBase(),
      grid: { left: 156, right: 80, top: 36, bottom: 72, containLabel: true },
      xAxis: valueXAxis(title, yKey),
      yAxis: categoryYAxis(titleCase(xKey), labels, true),
      series: asChartSeries([
        {
          name: title,
          type: "bar",
          barMaxWidth: 28,
          itemStyle: { color: cssVar("--accent") },
          data: rows.map((row) => asNumber(row[yKey]) ?? 0),
        },
      ]),
    };
  }
  return {
    ...chartBase(),
    grid: { left: 84, right: 80, top: 52, bottom: 92, containLabel: true },
    xAxis: categoryXAxis(titleCase(xKey), "category", true, labels),
    yAxis: valueYAxis(title, yKey),
    series: asChartSeries([
      {
        name: title,
        type: "bar",
        itemStyle: { color: cssVar("--accent") },
        data: rows.map((row) => asNumber(row[yKey]) ?? 0),
      },
    ]),
  };
}

function asChartSeries(series: unknown): EChartsOption["series"] {
  return series as EChartsOption["series"];
}

function axisLabel(kind: "category" | "date" | "time" = "category") {
  return {
    color: cssVar("--muted"),
    fontSize: 11,
    hideOverlap: true,
    margin: 10,
    formatter: (value: string | number) => formatAxisLabel(value, kind),
  };
}

function formatAxisLabel(value: string | number, kind: "category" | "date" | "time"): string {
  if (kind === "time") return formatShortDateTime(value).replace(", ", "\n");
  if (kind === "date") return formatDate(value);
  return wrapAxisLabel(cleanLabel(value), 16);
}

function wrapAxisLabel(value: string, maxLineLength: number): string {
  if (value.length <= maxLineLength) return value;
  const words = value.split(" ");
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length > maxLineLength && current) {
      lines.push(current);
      current = word;
    } else {
      current = next;
    }
  }
  if (current) lines.push(current);
  return lines.slice(0, 3).join("\n");
}

function valueAxisLabel(metricKey: string) {
  return {
    color: cssVar("--muted"),
    formatter: (value: number) => {
      if (isProbablyPercent(metricKey)) return `${formatNumber(Math.abs(value) <= 1 ? value * 100 : value)}%`;
      if (/\b(pnl|profit|loss|reward|price|cost|usd)\b/i.test(metricKey)) return formatCurrency(value);
      return formatNumber(value);
    },
  };
}

function splitLine() {
  return { lineStyle: { color: cssVar("--grid") } };
}

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function average(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}
