import * as Dialog from "@radix-ui/react-dialog";
import type { EChartsCoreOption as EChartsOption } from "echarts/core";
import {
  Activity,
  AlertTriangle,
  Archive,
  BarChart3,
  Bot,
  Box,
  CheckCircle2,
  Copy,
  Database,
  FileBarChart,
  FlaskConical,
  GitCompareArrows,
  Grid2X2,
  Layers,
  LineChart,
  Loader2,
  Play,
  RefreshCcw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Table2,
  TerminalSquare,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type CSSProperties, type InputHTMLAttributes, type ReactNode } from "react";
import { getAnalysis, getSeries, getSources, getTable, loadWorkbench } from "./api";
import { ChartPanel } from "./components/ChartPanel";
import { DataTable } from "./components/DataTable";
import { SourcePicker } from "./components/SourcePicker";
import {
  archiveExport,
  cancelJob,
  checkModelCompatibility,
  cloneExport,
  compareExports,
  controlApiBaseUrl,
  createExport,
  createJob,
  extendExport,
  getBotStatus,
  getJobLogs,
  loadControlSnapshot,
  previewExport,
  reduceExport,
  validateExport,
  visualizationQuery,
  workbenchBackendCommand,
  type ArtifactMetadata,
  type ControlSnapshot,
  type EntrypointSpec,
  type ExportPreviewResponse,
  type JsonSchemaProperty,
  type JobRecord,
  type RegistryEntry,
  type VisualizationQueryResponse,
} from "./controlApi";
import type {
  DataRow,
  EventReplay,
  LoadResponse,
  MetricInfo,
  SourcesResponse,
  StrategyAnalysis,
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

type WorkbenchMode =
  | "dashboard"
  | "sources"
  | "dataset"
  | "export-builder"
  | "data-explorer"
  | "quality"
  | "trends"
  | "replay"
  | "disagreement"
  | "settlements"
  | "model-analysis"
  | "market-model"
  | "strategy-report"
  | "artifacts"
  | "model-lab"
  | "strategy-lab"
  | "jobs"
  | "registry"
  | "bot"
  | "settings";

type DateFilters = {
  start: string;
  end: string;
  city: string;
};

type OperationState = {
  busy: boolean;
  message: string;
  error: string;
  result: unknown;
};

const MODE_GROUPS: Array<{ label: string; modes: WorkbenchMode[] }> = [
  {
    label: "Workbench",
    modes: ["dashboard", "sources", "dataset", "data-explorer", "quality"],
  },
  {
    label: "Exports",
    modes: ["export-builder", "artifacts"],
  },
  {
    label: "Analysis",
    modes: ["trends", "replay", "disagreement", "settlements", "model-analysis", "market-model", "strategy-report"],
  },
  {
    label: "Runs",
    modes: ["model-lab", "strategy-lab", "jobs"],
  },
  {
    label: "System",
    modes: ["registry", "bot", "settings"],
  },
];

const MODE_META: Record<WorkbenchMode, { label: string; subtitle: string; icon: LucideIcon; needsSource?: boolean }> = {
  dashboard: {
    label: "Overview",
    subtitle: "Current exports, reports, jobs, and selected research source.",
    icon: Grid2X2,
  },
  sources: {
    label: "Source Hub",
    subtitle: "Choose the active export plus optional model, quality, and strategy reports.",
    icon: Database,
  },
  dataset: {
    label: "Dataset Detail",
    subtitle: "Inspect coverage, tables, schemas, and metadata health for the active export.",
    icon: Layers,
  },
  "export-builder": {
    label: "Export Builder",
    subtitle: "Preview profiles, create exports, and run guarded dataset operations.",
    icon: Archive,
  },
  "data-explorer": {
    label: "Data Explorer",
    subtitle: "Query local artifact tables with aggregation, filters, sampling, and charts.",
    icon: Search,
  },
  quality: {
    label: "Quality Review",
    subtitle: "Provider errors, missing city-hours, coverage checks, and quality report tables.",
    icon: ShieldCheck,
    needsSource: true,
  },
  trends: {
    label: "Trend Explorer",
    subtitle: "Weather, market, model, and strategy time series from the loaded source.",
    icon: LineChart,
    needsSource: true,
  },
  replay: {
    label: "Event Replay",
    subtitle: "One city-day timeline through weather snapshots, model paths, and settlement.",
    icon: Play,
    needsSource: true,
  },
  disagreement: {
    label: "Source Disagreement",
    subtitle: "Find when NWS, observations, HRRR, NBM, and ensembles diverged.",
    icon: AlertTriangle,
    needsSource: true,
  },
  settlements: {
    label: "Settlement Grid",
    subtitle: "Scan final highs, winners, and forecast miss distance by city and date.",
    icon: Table2,
    needsSource: true,
  },
  "model-analysis": {
    label: "Model Analysis",
    subtitle: "Checkpoint metrics, feature/error patterns, and probability calibration.",
    icon: FlaskConical,
    needsSource: true,
  },
  "market-model": {
    label: "Market vs Model",
    subtitle: "Archived market probabilities against model probabilities.",
    icon: GitCompareArrows,
    needsSource: true,
  },
  "strategy-report": {
    label: "Strategy Report",
    subtitle: "Paper strategy PnL, gate sweeps, trades, and policy calibration.",
    icon: TrendingUp,
    needsSource: true,
  },
  artifacts: {
    label: "Artifact Library",
    subtitle: "Browse exports, model reports, quality reports, and strategy reports.",
    icon: FileBarChart,
  },
  "model-lab": {
    label: "Model Lab",
    subtitle: "Run registered model entrypoints against selected datasets.",
    icon: Sparkles,
  },
  "strategy-lab": {
    label: "Strategy Lab",
    subtitle: "Run registered strategy entrypoints against exports and model reports.",
    icon: BarChart3,
  },
  jobs: {
    label: "Jobs",
    subtitle: "Inspect tracked jobs, parameters, commands, logs, and cancellation.",
    icon: Activity,
  },
  registry: {
    label: "Registry",
    subtitle: "Browse registered data sources, profiles, models, strategies, visualizations, and bot runtimes.",
    icon: Box,
  },
  bot: {
    label: "Bot Monitor",
    subtitle: "Reserved deployed-bot telemetry surface for the registered runtime contract.",
    icon: Bot,
  },
  settings: {
    label: "Settings",
    subtitle: "Connection diagnostics, roots, API mode, and local workbench defaults.",
    icon: Settings,
  },
};

const DEFAULT_FILTERS: DateFilters = {
  start: "",
  end: "",
  city: "all",
};

const INITIAL_OPERATION: OperationState = {
  busy: false,
  message: "",
  error: "",
  result: null,
};

export function WorkbenchApp() {
  const [mode, setMode] = useState<WorkbenchMode>(() => routeFromUrl());
  const [sources, setSources] = useState<SourcesResponse | null>(null);
  const [sourceError, setSourceError] = useState("");
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [picker, setPicker] = useState<PickerState>({ exportId: "", reportId: "", qualityId: "", strategyId: "" });
  const [workbench, setWorkbench] = useState<LoadResponse | null>(null);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [workbenchLoading, setWorkbenchLoading] = useState(false);
  const [workbenchError, setWorkbenchError] = useState("");
  const [control, setControl] = useState<ControlSnapshot | null>(null);
  const [controlLoading, setControlLoading] = useState(false);
  const [activeExportId, setActiveExportId] = useState("");
  const [activeArtifactId, setActiveArtifactId] = useState("");
  const [activeReportId, setActiveReportId] = useState("");
  const [activeJobId, setActiveJobId] = useState("");
  const [filters, setFilters] = useState<DateFilters>(DEFAULT_FILTERS);

  const refreshSources = useCallback(async () => {
    setSourcesLoading(true);
    setSourceError("");
    try {
      const result = await getSources();
      setSources(result);
      setPicker((current) => {
        const exportId = current.exportId || result.exports[0]?.id || "";
        return {
          exportId,
          reportId: current.reportId || linkedToExport(result.reports, exportId)[0]?.id || "",
          qualityId: current.qualityId || linkedToExport(result.quality_reports, exportId)[0]?.id || "",
          strategyId: current.strategyId || linkedToExport(result.strategy_reports, exportId)[0]?.id || "",
        };
      });
    } catch (error) {
      setSourceError(readableError(error));
    } finally {
      setSourcesLoading(false);
    }
  }, []);

  const refreshControl = useCallback(async () => {
    setControlLoading(true);
    try {
      const result = await loadControlSnapshot();
      setControl(result);
      setActiveExportId((current) => current || result.exports[0]?.id || "");
      setActiveArtifactId((current) => current || result.artifacts[0]?.id || result.exports[0]?.id || "");
      setActiveReportId((current) => current || result.reports[0]?.id || "");
      setActiveJobId((current) => current || result.jobs[0]?.id || "");
    } finally {
      setControlLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshSources();
    void refreshControl();
  }, [refreshControl, refreshSources]);

  useEffect(() => {
    const next = new URL(window.location.href);
    next.searchParams.set("view", mode);
    if (activeExportId) next.searchParams.set("export", activeExportId);
    if (activeArtifactId) next.searchParams.set("artifact", activeArtifactId);
    if (activeReportId) next.searchParams.set("report", activeReportId);
    if (activeJobId) next.searchParams.set("job", activeJobId);
    window.history.replaceState(null, "", next);
  }, [activeArtifactId, activeExportId, activeJobId, activeReportId, mode]);

  async function handleLoadWorkbench() {
    setWorkbenchLoading(true);
    setWorkbenchError("");
    try {
      const result = await loadWorkbench({
        export_id: picker.exportId,
        report_id: picker.reportId,
        quality_id: picker.qualityId,
        strategy_id: picker.strategyId,
      });
      setWorkbench(result);
      setSourceOpen(false);
      setMode((current) => current === "sources" ? "dataset" : current);
      setFilters({
        start: result.metadata.date_range.start ?? result.metadata.artifact_date_range?.start ?? "",
        end: result.metadata.date_range.end ?? result.metadata.artifact_date_range?.end ?? "",
        city: "all",
      });
      setActiveExportId((current) => current || result.selection.export_id || "");
    } catch (error) {
      setWorkbenchError(readableError(error));
    } finally {
      setWorkbenchLoading(false);
    }
  }

  const selectedExport = findById(control?.exports ?? [], activeExportId) ?? control?.exports[0];
  const scannedSelectedExport = selectedExport ? findById(control?.artifacts ?? [], selectedExport.id) : undefined;
  const selectedArtifact =
    findById(control?.artifacts ?? [], activeArtifactId) ??
    findById(control?.reports ?? [], activeArtifactId) ??
    findById(control?.exports ?? [], activeArtifactId) ??
    scannedSelectedExport ??
    control?.artifacts[0] ??
    selectedExport;
  const selectedReport = findById(control?.reports ?? [], activeReportId) ?? control?.reports[0];
  const selectedJob = findById(control?.jobs ?? [], activeJobId) ?? control?.jobs[0];
  const meta = MODE_META[mode];

  return (
    <main className="app-shell weather-workbench">
      <aside className="left-rail">
        <div className="rail-brand">
          <span className="brand-mark" aria-hidden="true">
            <LineChart size={20} />
          </span>
          <div>
            <h1>Weather Workbench</h1>
            <p>{workbench ? sourceLabel(sources?.exports.find((item) => item.id === workbench.selection.export_id)) : "No source loaded"}</p>
          </div>
        </div>

        <nav className="mode-nav" aria-label="Weather workbench views">
          {MODE_GROUPS.map((group) => (
            <section key={group.label}>
              <p>{group.label}</p>
              {group.modes.map((item) => {
                const Icon = MODE_META[item].icon;
                const disabled = MODE_META[item].needsSource && !workbench;
                return (
                  <button
                    className={mode === item ? "active" : ""}
                    disabled={disabled}
                    key={item}
                    onClick={() => setMode(item)}
                    title={disabled ? "Load a source to enable this view." : MODE_META[item].subtitle}
                    type="button"
                  >
                    <Icon aria-hidden="true" size={16} />
                    <span>{MODE_META[item].label}</span>
                  </button>
                );
              })}
            </section>
          ))}
        </nav>

        <div className="rail-actions">
          <button className="ghost full-button" type="button" onClick={() => setSourceOpen(true)}>
            <Database aria-hidden="true" size={16} />
            Change Source
          </button>
          <button className="ghost full-button" disabled={controlLoading} type="button" onClick={() => void refreshControl()}>
            {controlLoading ? <Loader2 className="spin" aria-hidden="true" size={16} /> : <RefreshCcw aria-hidden="true" size={16} />}
            Refresh Backend
          </button>
        </div>
      </aside>

      <section className="workbench-pane">
        <header className="topbar">
          <div className="topbar-title">
            <p className="eyebrow">{meta.label}</p>
            <h2>{meta.subtitle}</h2>
          </div>
          <div className="status-pills">
            <StatusPill status={control?.errors.length ? "warning" : "ready"} label={control?.errors.length ? "Partial API" : "API Ready"} />
            <StatusPill status={workbench || selectedExport ? "ready" : "warning"} label={workbench ? "Source Loaded" : selectedExport ? "Export Selected" : "No Source"} />
            <StatusPill status={controlLoading || workbenchLoading ? "running" : "ready"} label={controlLoading || workbenchLoading ? "Loading" : "Idle"} />
          </div>
          <div className="topbar-actions">
            <button className="ghost" type="button" onClick={() => setSourceOpen(true)}>
              <Database aria-hidden="true" size={16} />
              Source
            </button>
            <button className="ghost" disabled={controlLoading} type="button" onClick={() => void refreshControl()}>
              <RefreshCcw aria-hidden="true" size={16} />
              Refresh
            </button>
          </div>
        </header>

        <section className="mobile-mode-scroll" aria-label="Workbench views">
          {MODE_GROUPS.flatMap((group) => group.modes).map((item) => (
            <button
              className={mode === item ? "active" : ""}
              disabled={MODE_META[item].needsSource && !workbench}
              key={item}
              onClick={() => setMode(item)}
              type="button"
            >
              {MODE_META[item].label}
            </button>
          ))}
        </section>

        <section className="content-stage">
          {sourceError ? <div className="notice error">Source API: {sourceError}</div> : null}
          {workbenchError ? <div className="notice error">Workbench load: {workbenchError}</div> : null}
          <ViewRouter
            activeArtifact={selectedArtifact}
            activeExport={selectedExport}
            activeJob={selectedJob}
            activeReport={selectedReport}
            control={control}
            filters={filters}
            mode={mode}
            onRefreshControl={refreshControl}
            onSelectArtifact={setActiveArtifactId}
            onSelectExport={setActiveExportId}
            onSelectJob={setActiveJobId}
            onSelectReport={setActiveReportId}
            onSetFilters={setFilters}
            onSetMode={setMode}
            onRefreshSources={refreshSources}
            onSourceOpen={() => setSourceOpen(true)}
            onWorkbenchLoad={handleLoadWorkbench}
            picker={picker}
            setPicker={setPicker}
            sources={sources}
            sourcesLoading={sourcesLoading}
            workbench={workbench}
            workbenchLoading={workbenchLoading}
          />
        </section>
      </section>

      <WorkbenchInspector
        activeArtifact={selectedArtifact}
        activeExport={selectedExport}
        activeJob={selectedJob}
        activeReport={selectedReport}
        control={control}
        filters={filters}
        mode={mode}
        onChangeSource={() => setSourceOpen(true)}
        onRefreshControl={refreshControl}
        onSetFilters={setFilters}
        workbench={workbench}
      />

      <Dialog.Root open={sourceOpen} onOpenChange={setSourceOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content">
            <Dialog.Title className="sr-only">Change workbench source</Dialog.Title>
            <SourcePicker
              sources={sources}
              value={picker}
              loadingSources={sourcesLoading}
              loading={workbenchLoading}
              onChange={setPicker}
              onLoad={handleLoadWorkbench}
              onRefresh={() => void refreshSources()}
            />
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </main>
  );
}

type RouterProps = {
  activeArtifact?: ArtifactMetadata;
  activeExport?: ArtifactMetadata;
  activeJob?: JobRecord;
  activeReport?: ArtifactMetadata;
  control: ControlSnapshot | null;
  filters: DateFilters;
  mode: WorkbenchMode;
  onRefreshControl: () => Promise<void>;
  onSelectArtifact: (id: string) => void;
  onSelectExport: (id: string) => void;
  onSelectJob: (id: string) => void;
  onSelectReport: (id: string) => void;
  onSetFilters: (filters: DateFilters) => void;
  onSetMode: (mode: WorkbenchMode) => void;
  onRefreshSources: () => Promise<void>;
  onSourceOpen: () => void;
  onWorkbenchLoad: () => Promise<void>;
  picker: PickerState;
  setPicker: (picker: PickerState) => void;
  sources: SourcesResponse | null;
  sourcesLoading: boolean;
  workbench: LoadResponse | null;
  workbenchLoading: boolean;
};

type PathRow = {
  label: string;
  value: string;
};

function ViewRouter(props: RouterProps) {
  const { mode, workbench } = props;
  if (MODE_META[mode].needsSource && !workbench) {
    return <NeedSourcePanel onSourceOpen={props.onSourceOpen} />;
  }
  switch (mode) {
    case "dashboard":
      return <DashboardView {...props} />;
    case "sources":
      return <SourcesView {...props} />;
    case "dataset":
      return <DatasetView {...props} />;
    case "export-builder":
      return <ExportBuilderView {...props} />;
    case "data-explorer":
      return <DataExplorerView {...props} />;
    case "quality":
      return <QualityView {...props} />;
    case "trends":
      return <TrendExplorerView workbench={workbench} filters={props.filters} />;
    case "replay":
      return <ReplayView workbench={workbench} filters={props.filters} />;
    case "disagreement":
      return <AnalysisRowsView section="source_disagreement" title="Source Disagreement" chart="heatmap" filters={props.filters} />;
    case "settlements":
      return <AnalysisRowsView section="settlement_grid" title="Settlement Grid" chart="table" filters={props.filters} />;
    case "model-analysis":
      return <ModelAnalysisView workbench={workbench} filters={props.filters} />;
    case "market-model":
      return <AnalysisRowsView section="market_model_points" title="Market vs Model" chart="scatter" filters={props.filters} />;
    case "strategy-report":
      return <StrategyReportView filters={props.filters} />;
    case "artifacts":
      return <ArtifactsView {...props} />;
    case "model-lab":
      return <LabView kind="model" {...props} />;
    case "strategy-lab":
      return <LabView kind="strategy" {...props} />;
    case "jobs":
      return <JobsView {...props} />;
    case "registry":
      return <RegistryView control={props.control} />;
    case "bot":
      return <BotView control={props.control} />;
    case "settings":
      return <SettingsView control={props.control} sources={props.sources} workbench={props.workbench} />;
    default:
      return <DashboardView {...props} />;
  }
}

function DashboardView({
  activeExport,
  control,
  onSelectExport,
  onSelectJob,
  onSetMode,
  onSourceOpen,
  workbench,
}: RouterProps) {
  const counts = control?.dashboard.artifact_counts ?? {};
  const recentJobs = control?.jobs.slice(0, 6) ?? [];
  const tableRows = tableCountRows(activeExport ?? workbenchArtifact(workbench)).slice(0, 10);
  return (
    <div className="view-stack">
      <div className="kpi-grid">
        <Kpi label="Exports" value={control?.exports.length ?? 0} />
        <Kpi label="Model Reports" value={counts.model_report ?? reportsByType(control, "model_report").length} />
        <Kpi label="Strategy Reports" value={counts.strategy_report ?? reportsByType(control, "strategy_report").length} />
        <Kpi label="Quality Reports" value={counts.quality_report ?? reportsByType(control, "quality_report").length} />
        <Kpi label="Models" value={control?.models.length ?? 0} />
        <Kpi label="Open Jobs" value={(control?.jobs ?? []).filter((job) => isActiveJob(job.status)).length} tone={(control?.jobs ?? []).some((job) => job.status === "failed") ? "bad" : "good"} />
      </div>

      <div className="view-grid wide-first">
        <Panel
          title="Active Dataset"
          subtitle="The whole workbench pivots around this selected local export."
          actions={<button type="button" onClick={onSourceOpen}>Change source</button>}
        >
          {workbench ? (
            <div className="detail-list detail-list-three">
              <Detail label="Loaded export" value={workbench.selection.export_id} />
              <Detail label="Rows" value={formatNumber(sumCounts(workbench.metadata.table_counts))} />
              <Detail label="Cities" value={workbench.metadata.cities.join(", ") || "n/a"} />
              <Detail label="Date range" value={dateRangeFrom(workbench.metadata.date_range)} />
              <Detail label="Model report" value={workbench.selection.report_id || "none"} />
              <Detail label="Strategy report" value={workbench.selection.strategy_id || "none"} />
            </div>
          ) : (
            <NeedSourcePanel compact onSourceOpen={onSourceOpen} />
          )}
        </Panel>

        <Panel
          title="Backend Freshness"
          subtitle="Control routes are read independently from the loaded Trends source."
          actions={<button className="ghost" type="button" onClick={() => onSetMode("settings")}>Settings</button>}
        >
          <div className="detail-list">
            <Detail label="Loaded at" value={formatDateTime(control?.loadedAt)} />
            <Detail label="Registry root" value={control?.registryRoot || "builtin"} />
            <Detail label="API state" value={control?.errors.length ? "partial" : "ready"} />
          </div>
          {control?.errors.length ? <InlineErrors errors={control.errors} /> : null}
        </Panel>
      </div>

      <div className="view-grid">
        <ChartPanel
          title="Selected Export Tables"
          subtitle="Largest tables by row count"
          option={barOption(tableRows, "table", "rows", "Rows")}
          explanation="Counts rows per local artifact table. Sudden gaps or unexpectedly small tables usually indicate an incomplete export or a deliberately reduced dataset."
        />
        <Panel title="Recent Jobs" actions={<button type="button" onClick={() => onSetMode("jobs")}>Open jobs</button>}>
          <DataRows
            columns={["Job", "Task", "Status", "Output"]}
            rows={recentJobs.map((job) => [
              <button className="link-cell" key={job.id} type="button" onClick={() => { onSelectJob(job.id); onSetMode("jobs"); }}>{job.id}</button>,
              `${job.kind ?? "-"} / ${job.entrypoint ?? "-"}`,
              <StatusPill key={`${job.id}-status`} status={job.status} />,
              shortPath(job.output_path),
            ])}
          />
        </Panel>
      </div>

      <Panel title="Export Inventory" actions={<button type="button" onClick={() => onSetMode("dataset")}>Inspect dataset</button>}>
        <DataRows
          columns={["Export", "Range", "Cities", "Rows"]}
          rows={(control?.exports ?? []).slice(0, 8).map((item) => [
            <button className="link-cell" key={item.id} type="button" onClick={() => { onSelectExport(item.id); onSetMode("dataset"); }}>{item.id}</button>,
            dateRangeFrom(item.coverage?.date_range),
            (item.coverage?.cities ?? []).join(", ") || "-",
            formatNumber(sumCounts(item.table_counts)),
          ])}
        />
      </Panel>
    </div>
  );
}

function SourcesView({
  onRefreshSources,
  onWorkbenchLoad,
  picker,
  setPicker,
  sources,
  sourcesLoading,
  workbenchLoading,
}: RouterProps) {
  return (
    <div className="view-stack">
      <SourcePicker
        sources={sources}
        value={picker}
        loadingSources={sourcesLoading}
        loading={workbenchLoading}
        onChange={setPicker}
        onLoad={onWorkbenchLoad}
        onRefresh={() => void onRefreshSources()}
      />
    </div>
  );
}

function DatasetView({ activeExport, control, onSelectExport, workbench }: RouterProps) {
  const selected = activeExport;
  if (!selected) return <EmptyState label="No local export is available from the backend." />;
  const tables = tableCountRows(selected);
  const fallbackCoverage = fallbackExportCoverage(selected, workbench);
  const cities = selected.coverage?.cities?.length ? selected.coverage.cities : fallbackCoverage.cities;
  const files = selected.files?.length ? selected.files : Object.keys(selected.table_counts ?? {});
  const dateRange = selected.coverage?.date_range?.start || selected.coverage?.date_range?.end
    ? selected.coverage.date_range
    : fallbackCoverage.dateRange;
  const targetDates = selected.coverage?.target_dates || countDateRangeDays(dateRange);
  const snapshotHours = selected.coverage?.snapshot_hours?.length || fallbackCoverage.snapshotHours;
  return (
    <div className="view-stack">
      <ControlStrip>
        <label>
          Dataset
          <select value={selected.id} onChange={(event) => onSelectExport(event.target.value)}>
            {(control?.exports ?? []).map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
          </select>
        </label>
      </ControlStrip>

      <div className="kpi-grid">
        <Kpi label="Rows" value={sumCounts(selected.table_counts)} />
        <Kpi label="Tables" value={files.length} />
        <Kpi label="Cities" value={cities.length} />
        <Kpi label="Target Dates" value={targetDates} />
        <Kpi label="Snapshot Hours" value={snapshotHours} />
        <Kpi label="Status" value={selected.status ?? "unknown"} />
      </div>

      <div className="view-grid wide-first">
        <ChartPanel
          title="Table Rows"
          subtitle={selected.id}
          option={barOption(tables, "table", "rows", "Rows")}
          height={420}
          explanation="Ranks included export tables by row count so large raw payload or snapshot tables do not hide smaller settlement and label tables."
        />
        <Panel title="Coverage">
          <div className="detail-list">
            <Detail label="Date range" value={dateRangeFrom(dateRange)} />
            <Detail label="Cities" value={cities.join(", ") || "-"} />
            <Detail label="Path" value={selected.path ?? "-"} />
            <Detail label="Manifest" value={selected.metadata_health?.has_manifest ? "present" : "missing"} tone={selected.metadata_health?.has_manifest ? "good" : "warn"} />
            <Detail label="Run manifest" value={selected.metadata_health?.has_run_manifest ? "present" : "missing"} />
            <Detail label="Schemas" value={selected.metadata_health?.has_schemas ? "present" : "inferred"} />
          </div>
        </Panel>
      </div>

      <Panel title="City Coverage">
        <div className="chip-row">
          {cities.map((city) => <span className="chip" key={city}>{city}</span>)}
          {!cities.length ? <span className="muted">No city coverage detected.</span> : null}
        </div>
      </Panel>

      <DataTable
        title="Table Inventory"
        rows={tables.map((row) => ({
          ...row,
          file_present: files.includes(asText(row.table)),
          schema_columns: Object.keys(selected.schemas?.[asText(row.table)]?.columns ?? {}).length,
        }))}
        preferredColumns={["table", "rows", "file_present", "schema_columns"]}
      />
    </div>
  );
}

function ExportBuilderView({ activeExport, control, onRefreshControl }: RouterProps) {
  const profiles = control?.exportProfiles ?? [];
  const exports = control?.exports ?? [];
  const selected = activeExport ?? exports[0];
  const [profileId, setProfileId] = useState(profiles[0]?.id ?? "");
  const [start, setStart] = useState(selected?.coverage?.date_range?.start ?? "2026-07-01");
  const [end, setEnd] = useState(selected?.coverage?.date_range?.end ?? "2026-07-21");
  const [outputPath, setOutputPath] = useState("");
  const [compareId, setCompareId] = useState(exports.find((item) => item.id !== selected?.id)?.id ?? "");
  const [extensionId, setExtensionId] = useState(exports.find((item) => item.id !== selected?.id)?.id ?? "");
  const [destination, setDestination] = useState("");
  const [selectedTables, setSelectedTables] = useState<string[]>(selected?.files ?? []);
  const [selectedCities, setSelectedCities] = useState<string[]>(selected?.coverage?.cities ?? []);
  const [operation, setOperation] = useState<OperationState>(INITIAL_OPERATION);
  const [preview, setPreview] = useState<ExportPreviewResponse | null>(null);

  useEffect(() => {
    if (!profileId && profiles[0]?.id) setProfileId(profiles[0].id);
  }, [profileId, profiles]);

  useEffect(() => {
    if (!selected) return;
    setSelectedTables(selected.files ?? []);
    setSelectedCities(selected.coverage?.cities ?? []);
    setStart((current) => current || selected.coverage?.date_range?.start || "");
    setEnd((current) => current || selected.coverage?.date_range?.end || "");
  }, [selected]);

  async function runOperation(label: string, action: () => Promise<unknown>, refresh = false) {
    setOperation({ busy: true, message: label, error: "", result: null });
    try {
      const result = await action();
      setOperation({ busy: false, message: `${label} complete`, error: "", result });
      if (refresh) await onRefreshControl();
    } catch (error) {
      setOperation({ busy: false, message: label, error: readableError(error), result: null });
    }
  }

  return (
    <div className="view-stack">
      <div className="view-grid wide-first">
        <Panel title="Create Export" subtitle="Registry profiles define tables, protected columns, and required columns.">
          <div className="form-grid">
            <label>
              Profile
              <select value={profileId} onChange={(event) => setProfileId(event.target.value)}>
                {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.label ?? profile.id}</option>)}
              </select>
            </label>
            <label>
              Start
              <input type="date" value={start} onChange={(event) => setStart(event.target.value)} />
            </label>
            <label>
              End
              <input type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
            </label>
            <label>
              Output path
              <input value={outputPath} onChange={(event) => setOutputPath(event.target.value)} placeholder="data/export_YYYYMMDD_control" />
            </label>
          </div>
          <div className="row-actions">
            <button type="button" disabled={!profileId || operation.busy} onClick={() => runOperation("Preview export", async () => {
              const result = await previewExport(profileId);
              setPreview(result);
              return result;
            })}>
              Preview
            </button>
            <button type="button" disabled={!profileId || !start || !end || operation.busy} onClick={() => runOperation("Create export job", () => createExport({ profile_id: profileId, start, end, output_path: outputPath || undefined }), true)}>
              Create export
            </button>
          </div>
        </Panel>

        <Panel title="Profile Preview">
          {preview ? (
            <>
              <div className="detail-list">
                <Detail label="Profile" value={preview.profile_id ?? preview.id} />
                <Detail label="Source" value={preview.source ?? "-"} />
                <Detail label="Estimated rows" value={typeof preview.estimated_rows === "number" ? formatNumber(preview.estimated_rows) : JSON.stringify(preview.estimated_rows ?? {})} />
              </div>
              <DataRows
                columns={["Table", "Include", "Required", "Columns"]}
                rows={previewTableRows(preview).map((table) => [
                  table.table ?? table.name ?? "-",
                  table.include === false ? "no" : "yes",
                  table.required ? "yes" : "no",
                  (table.effective_columns ?? table.include_columns ?? []).slice(0, 8).join(", ") || "-",
                ])}
              />
            </>
          ) : (
            <EmptyState compact label="Preview a profile to inspect the export plan." />
          )}
        </Panel>
      </div>

      <Panel title="Dataset Operations" subtitle="All file operations are path-guarded by the backend.">
        {selected ? (
          <div className="operation-grid">
            <div className="operation-card">
              <h3>Validate</h3>
              <p>Check required tables, city coverage, and date metadata.</p>
              <span className="operation-target" title={selected.id}>{selected.id}</span>
              <button type="button" onClick={() => runOperation("Validate export", () => validateExport(selected.id))}>Validate selected export</button>
            </div>
            <div className="operation-card">
              <h3>Compare</h3>
              <select value={compareId} onChange={(event) => setCompareId(event.target.value)}>
                {exports.filter((item) => item.id !== selected.id).map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
              </select>
              <button type="button" disabled={!compareId} onClick={() => runOperation("Compare exports", () => compareExports(selected.id, compareId))}>Compare</button>
            </div>
            <div className="operation-card">
              <h3>Clone</h3>
              <input value={destination} onChange={(event) => setDestination(event.target.value)} placeholder={`data/${selected.id}_clone`} />
              <button type="button" disabled={!destination} onClick={() => runOperation("Clone export", () => cloneExport(selected.id, destination), true)}>Clone</button>
            </div>
            <div className="operation-card">
              <h3>Reduce</h3>
              <input value={destination} onChange={(event) => setDestination(event.target.value)} placeholder={`data/${selected.id}_reduced`} />
              <CheckboxGrid label="Tables" values={selected.files ?? []} selected={selectedTables} onChange={setSelectedTables} />
              <CheckboxGrid label="Cities" values={selected.coverage?.cities ?? []} selected={selectedCities} onChange={setSelectedCities} />
              <button type="button" disabled={!destination} onClick={() => runOperation("Reduce export", () => reduceExport({ export_id: selected.id, destination, tables: selectedTables, cities: selectedCities, start, end }), true)}>Reduce</button>
            </div>
            <div className="operation-card">
              <h3>Extend</h3>
              <select value={extensionId} onChange={(event) => setExtensionId(event.target.value)}>
                {exports.filter((item) => item.id !== selected.id).map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
              </select>
              <input value={destination} onChange={(event) => setDestination(event.target.value)} placeholder={`data/${selected.id}_extended`} />
              <button type="button" disabled={!extensionId || !destination} onClick={() => runOperation("Extend export", () => extendExport(selected.id, extensionId, destination), true)}>Extend</button>
            </div>
            <div className="operation-card danger-zone">
              <h3>Archive</h3>
              <p>Moves the selected export under data/.archive.</p>
              <button className="ghost" type="button" onClick={() => runOperation("Archive export", () => archiveExport(selected.id), true)}>Archive</button>
            </div>
          </div>
        ) : (
          <EmptyState label="No export selected." />
        )}
      </Panel>

      <OperationResult operation={operation} />
    </div>
  );
}

function DataExplorerView({ activeArtifact, control, filters, onSelectArtifact }: RouterProps) {
  const artifacts = uniqueArtifacts([...(control?.artifacts ?? []), ...(control?.exports ?? []), ...(control?.reports ?? [])]);
  const artifact = activeArtifact ?? artifacts[0];
  const [schemaProbe, setSchemaProbe] = useState<VisualizationQueryResponse | null>(null);
  const metadata = artifact?.metadata as DataRow | undefined;
  const metadataSchemas = metadata?.schemas as ArtifactMetadata["schemas"] | undefined;
  const schemas = useMemo(() => ({
    ...(metadataSchemas ?? {}),
    ...(artifact?.schemas ?? {}),
    ...(schemaProbe?.table ? { [schemaProbe.table]: schemaProbe.schema } : {}),
  }), [artifact?.schemas, metadataSchemas, schemaProbe]);
  const tables = useMemo(() => Object.keys(schemas).length ? Object.keys(schemas) : artifact?.files ?? [], [artifact?.files, schemas]);
  const [table, setTable] = useState(tables[0] ?? "");
  const columns = useMemo(() => Object.keys(schemas[table]?.columns ?? {}), [schemas, table]);
  const [xField, setXField] = useState("");
  const [yField, setYField] = useState("");
  const [groupField, setGroupField] = useState("");
  const [chartMode, setChartMode] = useState<"line" | "bar" | "scatter" | "heatmap">("bar");
  const [aggregation, setAggregation] = useState<"none" | "count" | "avg" | "sum" | "min" | "max">("count");
  const [result, setResult] = useState<VisualizationQueryResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (tables.length && !tables.includes(table)) setTable(tables[0]);
  }, [table, tables]);

  useEffect(() => {
    setSchemaProbe(null);
  }, [artifact?.id]);

  useEffect(() => {
    if (!artifact?.path || !table || columns.length) return undefined;
    let cancelled = false;
    visualizationQuery({
      artifact_path: artifact.path,
      query: {
        table,
        aggregation: "none",
        sample: { limit: 1 },
        page_size: 1,
      },
    })
      .then((response) => {
        if (!cancelled) setSchemaProbe(response);
      })
      .catch((err) => {
        if (!cancelled) setError(readableError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [artifact?.path, columns.length, table]);

  useEffect(() => {
    const schemaColumns = Object.keys(schemas[table]?.columns ?? {});
    const schema = schemas[table]?.columns ?? {};
    const firstTime = schemaColumns.find((column) => /time|date|hour/i.test(column)) ?? schemaColumns[0] ?? "";
    const firstNumber = schemaColumns.find((column) => isNumericSchemaType(schema[column]?.type)) ?? "";
    setXField((current) => current && schemaColumns.includes(current) ? current : firstTime);
    setYField((current) => current && schemaColumns.includes(current) ? current : firstNumber);
    setGroupField((current) => current && schemaColumns.includes(current) ? current : schemaColumns.includes("city") ? "city" : "");
    setAggregation((current) => (current !== "count" && current !== "none" && !firstNumber ? "count" : current));
    setResult(null);
    setError("");
  }, [schemas, table]);

  async function runQuery() {
    if (!artifact?.path || !table) return;
    if (aggregation !== "count" && aggregation !== "none" && !yField) {
      setError("Choose a numeric Y field for avg/sum/min/max, or switch Aggregate to count.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const valueName = aggregation === "none" ? undefined : aggregation === "count" ? "count" : "value";
      const response = await visualizationQuery({
        artifact_path: artifact.path,
        query: {
          table,
          x: xField || undefined,
          y: aggregation === "count" ? undefined : yField || undefined,
          group: groupField ? [groupField] : [],
          filters: {
            cities: filters.city !== "all" ? [filters.city] : undefined,
            date_from: filters.start || undefined,
            date_to: filters.end || undefined,
          },
          aggregation: aggregation === "none"
            ? "none"
            : aggregation === "count"
              ? { op: "count", as: "count" }
              : { op: aggregation, field: yField, as: valueName },
          sample: aggregation === "none" ? { limit: 2000 } : undefined,
          decimate_to: 900,
          page_size: 1000,
        },
      });
      setResult(response);
    } catch (err) {
      setError(readableError(err));
    } finally {
      setLoading(false);
    }
  }

  const resolvedY = aggregation === "none" ? yField : aggregation === "count" ? "count" : "value";
  const resolvedYLabel = aggregationLabel(aggregation, yField);
  const option = result
    ? explorerOption(result.rows, xField, resolvedY, groupField, chartMode, resolvedYLabel)
    : emptyChartOption("Run a query to render chart-ready rows.");

  return (
    <div className="view-stack">
      <ControlStrip>
        <label>
          Artifact
          <select value={artifact?.id ?? ""} onChange={(event) => onSelectArtifact(event.target.value)}>
            {artifacts.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
          </select>
        </label>
        <label>
          Table
          <select value={table} onChange={(event) => setTable(event.target.value)}>
            {tables.map((name) => <option key={name} value={name}>{titleCase(name)}</option>)}
          </select>
        </label>
        <label>
          X
          <select value={xField} onChange={(event) => setXField(event.target.value)}>
            {columns.map((column) => <option key={column} value={column}>{titleCase(column)}</option>)}
          </select>
        </label>
        <label>
          Y
          <select value={yField} onChange={(event) => setYField(event.target.value)}>
            <option value="">None</option>
            {columns.map((column) => <option key={column} value={column}>{titleCase(column)}</option>)}
          </select>
        </label>
        <label>
          Group
          <select value={groupField} onChange={(event) => setGroupField(event.target.value)}>
            <option value="">None</option>
            {columns.map((column) => <option key={column} value={column}>{titleCase(column)}</option>)}
          </select>
        </label>
        <label>
          Aggregate
          <select value={aggregation} onChange={(event) => setAggregation(event.target.value as typeof aggregation)}>
            {["none", "count", "avg", "sum", "min", "max"].map((item) => (
              <option key={item} value={item} disabled={!yField && !["none", "count"].includes(item)}>{item}</option>
            ))}
          </select>
        </label>
        <label>
          Chart
          <select value={chartMode} onChange={(event) => setChartMode(event.target.value as typeof chartMode)}>
            {["line", "bar", "scatter", "heatmap"].map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <button type="button" disabled={!artifact?.path || !table || loading} onClick={() => void runQuery()}>
          {loading ? "Querying..." : "Run Query"}
        </button>
      </ControlStrip>
      {error ? <div className="notice error">{error}</div> : null}
      <ChartPanel
        title="Visualization Query"
        subtitle={result ? `${formatNumber(result.metadata.returned_rows)} returned of ${formatNumber(result.metadata.filtered_rows)} filtered rows` : "Awaiting query"}
        option={option}
        height={460}
        explanation="Queries one local artifact table through the backend visualization endpoint. Aggregation and decimation happen server-side so the browser stays responsive."
      />
      {result ? (
        <DataTable
          title={`${titleCase(result.table)} Rows`}
          rows={result.rows}
          preferredColumns={[xField, resolvedY, groupField, "row_count"].filter(Boolean)}
        />
      ) : null}
    </div>
  );
}

function QualityView({ filters }: RouterProps) {
  const { data, loading, error } = useAnalysis<Record<string, unknown>>("quality", true);
  const quality = data;
  if (loading && !quality) return <EmptyState label="Loading quality report..." />;
  if (!quality?.available) return <EmptyState label="No quality report is attached to the loaded source." />;
  const summary = (quality.summary ?? {}) as DataRow;
  const errors = filterRows(rowsFromUnknown(quality.provider_errors), filters, ["snapshot_time_utc"]);
  const missing = filterRows(rowsFromUnknown(quality.missing_city_hours), filters, ["snapshot_hour_utc", "target_date"]);
  const counts = rowsFromUnknown(quality.table_counts);
  const missingCount = summarizedCount(summary.missing_city_hours, missing.length);
  const providerErrorCount = summarizedCount(summary.provider_errors, errors.length);
  return (
    <div className="view-stack">
      {error ? <div className="notice error">{error}</div> : null}
      <div className="kpi-grid">
        <Kpi label="Expected City-Hours" value={summary.expected_city_hours} />
        <Kpi label="Actual City-Hours" value={summary.actual_city_hours} />
        <Kpi label="Missing City-Hours" value={missingCount} tone={missingCount ? "warn" : "good"} />
        <Kpi label="Provider Errors" value={providerErrorCount} tone={providerErrorCount ? "bad" : "good"} />
        <Kpi label="Pending Labels" value={summary.pending_final_highs} tone="warn" />
        <Kpi label="Pending Settlements" value={summary.pending_settlements} tone="warn" />
      </div>
      <div className="view-grid">
        <ChartPanel title="Quality Table Counts" option={barOption(counts, "table", "rows", "Rows")} />
        <DataTable title="Provider Errors" rows={errors} preferredColumns={["provider", "city", "snapshot_time_utc", "error_type", "error_message", "errors"]} />
      </div>
      <DataTable title="Missing City-Hours" rows={missing} preferredColumns={["target_date", "snapshot_hour_utc", "city", "table"]} />
    </div>
  );
}

function TrendExplorerView({ filters, workbench }: { filters: DateFilters; workbench: LoadResponse | null }) {
  const metrics = workbench?.metadata.metrics ?? [];
  const defaultMetric = metrics.find((item) => item.key.includes("nws_anchor"))?.key ?? metrics[0]?.key ?? "";
  const [metric, setMetric] = useState(defaultMetric);
  const { data, loading, error } = useSeries(metric, Boolean(metric));
  const rows = filterRows(data?.rows ?? [], filters, ["x"]);
  useEffect(() => {
    if (!metric && defaultMetric) setMetric(defaultMetric);
  }, [defaultMetric, metric]);
  return (
    <div className="view-stack">
      <ControlStrip>
        <label>
          Metric
          <select value={metric} onChange={(event) => setMetric(event.target.value)}>
            {metrics.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
          </select>
        </label>
      </ControlStrip>
      {error ? <div className="notice error">{error}</div> : null}
      <ChartPanel
        title={metricLabel(metrics as MetricInfo[], metric)}
        subtitle={loading ? "Loading series..." : `${formatNumber(rows.length)} observations`}
        option={lineOption(rows, "x", "value", "group", metric)}
        height={470}
      />
      <DataTable title="Series Rows" rows={rows} preferredColumns={["x", "group", "value", "metric"]} />
    </div>
  );
}

function ReplayView({ filters, workbench }: { filters: DateFilters; workbench: LoadResponse | null }) {
  const catalogEvents = filterRows(workbench?.catalog.events ?? [], filters) as EventReplay[];
  const [eventKey, setEventKey] = useState(catalogEvents[0]?.event_key ?? "");
  const { data, loading } = useAnalysis<EventReplay[]>("event_replays", Boolean(workbench));
  const events = filterRows(data ?? [], filters) as EventReplay[];
  const event = events.find((item) => item.event_key === eventKey) ?? events[0];
  useEffect(() => {
    if (!eventKey && events[0]?.event_key) setEventKey(events[0].event_key);
    if (eventKey && events.length && !events.some((item) => item.event_key === eventKey)) setEventKey(events[0].event_key);
  }, [eventKey, events]);
  if (!event) return <EmptyState label={loading ? "Loading event replays..." : "No replayable events match the filters."} />;
  const timeline = filterRows(event.timeline ?? [], filters, ["snapshot_time_utc"]);
  return (
    <div className="view-stack">
      <ControlStrip>
        <label>
          Event
          <select value={event.event_key} onChange={(eventChange) => setEventKey(eventChange.target.value)}>
            {events.map((item) => <option key={item.event_key} value={item.event_key}>{item.label}</option>)}
          </select>
        </label>
        <div className="outcome-pills">
          <span>Final high {formatNumber(event.final_high_f)} F</span>
          <span>Winner {asText(event.winner_label || event.winner_ticker) || "pending"}</span>
        </div>
      </ControlStrip>
      <ChartPanel title="Event Replay" option={eventReplayOption({ ...event, timeline })} height={470} />
      <DataTable title="Replay Timeline" rows={timeline} preferredColumns={["snapshot_time_utc", "checkpoint", "nws_anchor_high_f", "observed_high_so_far_f", "hrrr_projected_high_f", "nbm_projected_high_f", "ensemble_raw_median_high_f", "market_top_probability", "market_top_ask"]} />
    </div>
  );
}

function AnalysisRowsView({ chart, filters, section, title }: { chart: "heatmap" | "scatter" | "table"; filters: DateFilters; section: string; title: string }) {
  const { data, loading, error } = useAnalysis<DataRow[]>(section, true);
  const rows = filterRows(data ?? [], filters);
  if (loading && !rows.length) return <EmptyState label={`Loading ${title.toLowerCase()}...`} />;
  const option = chart === "scatter"
    ? scatterOption(rows, "model_probability", "market_probability", "city", "Market vs model")
    : chart === "heatmap"
      ? disagreementOption(rows)
      : emptyChartOption("This view is table-first.");
  return (
    <div className="view-stack">
      {error ? <div className="notice error">{error}</div> : null}
      {chart !== "table" ? <ChartPanel title={title} subtitle={`${formatNumber(rows.length)} rows`} option={option} height={460} /> : null}
      <DataTable title={title} rows={rows} />
    </div>
  );
}

function ModelAnalysisView({ filters, workbench }: { filters: DateFilters; workbench: LoadResponse | null }) {
  const [tab, setTab] = useState<"checkpoint" | "features" | "calibration">("checkpoint");
  const checkpoint = useAnalysis<DataRow[]>("checkpoint_metrics", Boolean(workbench));
  const features = useAnalysis<DataRow[]>("feature_error_points", Boolean(workbench));
  const calibration = useAnalysis<DataRow[]>("calibration_bins", Boolean(workbench));
  const rows = tab === "checkpoint" ? filterRows(checkpoint.data ?? [], filters) : tab === "features" ? filterRows(features.data ?? [], filters) : filterRows(calibration.data ?? [], filters);
  const option = tab === "checkpoint"
    ? metricHeatmapOption(rows, "checkpoint", "metric", "value")
    : tab === "features"
      ? scatterOption(rows, "source_range_f", "absolute_error_f", "city", "Feature vs error")
      : scatterOption(rows, "mean_probability", "observed_frequency", "model_name", "Calibration");
  return (
    <div className="view-stack">
      <div className="tab-row">
        {(["checkpoint", "features", "calibration"] as const).map((item) => (
          <button className={tab === item ? "active ghost" : "ghost"} key={item} type="button" onClick={() => setTab(item)}>{titleCase(item)}</button>
        ))}
      </div>
      <ChartPanel title={MODE_META["model-analysis"].label} subtitle={titleCase(tab)} option={option} height={470} />
      <DataTable title={titleCase(tab)} rows={rows} />
    </div>
  );
}

function StrategyReportView({ filters }: { filters: DateFilters }) {
  const { data, loading, error } = useAnalysis<StrategyAnalysis>("strategy", true);
  if (loading && !data) return <EmptyState label="Loading strategy report..." />;
  if (!data?.available) return <EmptyState label="No strategy report is attached to the loaded source." />;
  const trades = filterRows(data.trades, filters, ["entry_time_utc", "target_date"]);
  const daily = strategyDailyPnlRows(data.daily_pnl, trades, filters);
  const gate = strategyGateChart(data, filters);
  const diagnostics = strategyDiagnostics(data, trades, filters);
  const summary = strategySummaryFromTrades(trades, daily, data.overview);
  return (
    <div className="view-stack">
      {error ? <div className="notice error">{error}</div> : null}
      <div className="kpi-grid">
        <Kpi label="Trades" value={summary.trades} />
        <Kpi label="Total PnL" value={formatCurrency(summary.total_pnl)} tone={(asNumber(summary.total_pnl) ?? 0) < 0 ? "bad" : "good"} />
        <Kpi label="ROI" value={formatPercent(summary.roi)} />
        <Kpi label="Hit Rate" value={formatPercent(summary.hit_rate)} />
        <Kpi label="Drawdown" value={formatCurrency(summary.max_drawdown)} tone="warn" />
        <Kpi label="Positive CLV" value={formatPercent(summary.positive_clv_rate)} />
      </div>
      <div className="view-grid">
        <ChartPanel title="Strategy PnL" subtitle={filters.city === "all" ? undefined : `Derived from filtered ${filters.city.toUpperCase()} trades`} option={strategyPnlOption(daily)} height={420} />
        <ChartPanel title={gate.title} subtitle={gate.subtitle} option={gate.option} height={420} />
        <ChartPanel title="Edge Threshold Effect" subtitle="Higher minimum edge versus success rate, ROI, PnL, and remaining sample size." option={edgeThresholdEffectOption(diagnostics.edgeThresholdRows)} height={430} />
        <ChartPanel title="Side Outcome Mix" subtitle="Percent of YES and NO contracts that profited, lost, or finished flat." option={sideOutcomeMixOption(diagnostics.sideOutcomeRows)} height={430} />
        <ChartPanel title="Side Profit/Loss" subtitle="Gross profit, gross loss, and net PnL for YES versus NO contracts." option={sideProfitLossOption(diagnostics.sideSummaryRows)} height={430} />
        <ChartPanel title="Edge Buckets" subtitle="Trade outcomes grouped by realized strategy edge." option={strategyBucketPerformanceOption(diagnostics.edgeBucketRows, "Edge bucket")} height={430} />
        <ChartPanel title="Confidence Buckets" subtitle="Model probability bands compared against hit rate, ROI, and PnL." option={strategyBucketPerformanceOption(diagnostics.probabilityBucketRows, "Model probability bucket")} height={430} />
        <ChartPanel title="Entry Price Buckets" subtitle="Where contract price paid is helping or hurting payoff-weighted returns." option={strategyBucketPerformanceOption(diagnostics.priceBucketRows, "Entry price bucket")} height={430} />
        <ChartPanel title="Trade Edge vs PnL" subtitle="Each point is a trade, sized by contracts and colored by side." option={tradeEdgePnlOption(trades)} height={430} />
      </div>
      <DataTable title="Side Outcome Diagnostics" rows={diagnostics.sideSummaryRows} preferredColumns={["side", "trades", "contracts", "profitable_contract_rate", "loss_contract_rate", "total_pnl", "gross_profit", "gross_loss", "avg_win_pnl", "avg_loss_pnl", "roi", "avg_entry_price", "avg_edge"]} />
      <DataTable title="Bucket Diagnostics" rows={diagnostics.bucketRows} preferredColumns={["bucket_type", "group", "trades", "contracts", "hit_rate", "profitable_contract_rate", "roi", "total_pnl", "gross_profit", "gross_loss", "avg_entry_price", "avg_edge", "positive_clv_rate"]} />
      <DataTable title="Trades" rows={trades} preferredColumns={["entry_time_utc", "target_date", "city", "side", "market_ticker", "entry_price", "model_probability", "edge", "contracts", "pnl", "roi"]} />
    </div>
  );
}

function strategyDailyPnlRows(dailyRows: DataRow[], filteredTrades: DataRow[], filters: DateFilters): DataRow[] {
  if (filters.city !== "all") return dailyPnlFromTrades(filteredTrades);
  const filteredDaily = filterRows(dailyRows, filters);
  return filteredDaily.length ? filteredDaily : dailyPnlFromTrades(filteredTrades);
}

function dailyPnlFromTrades(trades: DataRow[]): DataRow[] {
  const grouped = new Map<string, { pnl: number; trades: number; hits: number }>();
  for (const trade of trades) {
    const date = asText(trade.target_date || trade.entry_time_utc).slice(0, 10);
    if (!date) continue;
    const current = grouped.get(date) ?? { pnl: 0, trades: 0, hits: 0 };
    current.pnl += asNumber(trade.pnl) ?? 0;
    current.trades += 1;
    current.hits += asNumber(trade.hit) ?? 0;
    grouped.set(date, current);
  }
  let cumulative = 0;
  return [...grouped.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([target_date, row]) => {
      cumulative += row.pnl;
      return {
        target_date,
        trades: row.trades,
        pnl: row.pnl,
        cumulative_pnl: cumulative,
        hit_rate: row.trades ? row.hits / row.trades : null,
      };
    });
}

function strategyGateChart(data: StrategyAnalysis, filters: DateFilters): { title: string; subtitle: string; option: EChartsOption } {
  const sweep = filterRows(data.threshold_sweep, filters, ["target_date", "date", "snapshot_time_utc"]);
  if (sweep.length) {
    return {
      title: "Gate Sweep",
      subtitle: "Backtest sweep rows from the selected strategy report.",
      option: thresholdSweepOption(sweep, "No policy gate sweep rows match the current filters."),
    };
  }

  const candidates = filterRows(data.candidate_points, filters, ["target_date", "snapshot_hour_utc", "snapshot_time_utc"]);
  const derived = deriveCandidateGateRows(candidates);
  if (derived.length) {
    return {
      title: "Gate Diagnostics",
      subtitle: "Derived from prediction candidates; ignores budget and max-position constraints.",
      option: thresholdSweepOption(derived, "No derived gate rows match the current filters."),
    };
  }

  const buckets = filterRows(data.bucket_rows, filters);
  if (buckets.length) {
    const yKey = buckets.some((row) => asNumber(row.pnl) !== null) ? "pnl" : "roi";
    return {
      title: "Gate Diagnostics",
      subtitle: "No sweep table was found; showing the report bucket table instead.",
      option: barOption(buckets, "group", yKey, yKey === "pnl" ? "PnL" : "ROI"),
    };
  }

  return {
    title: "Gate Sweep",
    subtitle: "This strategy report does not include a sweep table or enough prediction rows to derive one.",
    option: emptyChartOption("No gate sweep, bucket, or candidate rows are available."),
  };
}

function deriveCandidateGateRows(rows: DataRow[]): DataRow[] {
  const candidates = rows
    .map((row) => {
      const predictedReward = asNumber(row.predicted_reward);
      const reward = asNumber(row.reward);
      const tradeProbability = asNumber(row.trade_probability);
      const risk = asNumber(row.entry_ask) ?? asNumber(row.entry_price) ?? asNumber(row.market_midpoint);
      if (predictedReward === null || reward === null || risk === null || risk <= 0) return null;
      return { predictedReward, reward, tradeProbability: tradeProbability ?? 1, risk };
    })
    .filter((row): row is { predictedReward: number; reward: number; tradeProbability: number; risk: number } => row !== null);
  if (!candidates.length) return [];

  const thresholds = candidateRewardThresholds(candidates.map((row) => row.predictedReward));
  const probabilityGates = [0.5, 0.65, 0.8];
  const output: DataRow[] = [];
  for (const minTradeProbability of probabilityGates) {
    for (const minPredictedReward of thresholds) {
      const selected = candidates.filter((row) => row.predictedReward >= minPredictedReward && row.tradeProbability >= minTradeProbability);
      if (!selected.length) continue;
      const pnl = selected.reduce((sum, row) => sum + row.reward, 0);
      const risk = selected.reduce((sum, row) => sum + row.risk, 0);
      const hits = selected.filter((row) => row.reward > 0).length;
      output.push({
        min_predicted_reward: minPredictedReward,
        min_trade_probability: minTradeProbability,
        trades: selected.length,
        pnl,
        roi: risk ? pnl / risk : null,
        hit_rate: selected.length ? hits / selected.length : null,
      });
    }
  }
  return output;
}

function candidateRewardThresholds(values: number[]): number[] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const fixed = [-0.05, -0.02, 0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.18, 0.25, 0.35, 0.5];
  const thresholds = fixed.filter((value) => value >= min - 0.001 && value <= max + 0.001);
  if (thresholds.length) return thresholds;
  return unique(values.map((value) => Number(value.toFixed(3))))
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value))
    .slice(0, 14)
    .sort((left, right) => left - right);
}

type StrategyDiagnostics = {
  edgeThresholdRows: DataRow[];
  edgeBucketRows: DataRow[];
  probabilityBucketRows: DataRow[];
  priceBucketRows: DataRow[];
  sideOutcomeRows: DataRow[];
  sideSummaryRows: DataRow[];
  bucketRows: DataRow[];
};

type BucketSpec = {
  label: string;
  min: number;
  max: number;
};

function strategyDiagnostics(_data: StrategyAnalysis, trades: DataRow[], _filters: DateFilters): StrategyDiagnostics {
  const edgeBucketRows = bucketTrades(trades, "Edge", edgeBucketSpecs(), tradeEdge);
  const probabilityBucketRows = bucketTrades(trades, "Model probability", probabilityBucketSpecs(), tradeModelProbability);
  const priceBucketRows = bucketTrades(trades, "Entry price", entryPriceBucketSpecs(), tradeEntryPrice);
  const sideOutcomeRows = sideOutcomeDiagnostics(trades);
  const sideSummaryRows = sideSummaryDiagnostics(trades);
  return {
    edgeThresholdRows: edgeThresholdDiagnostics(trades),
    edgeBucketRows,
    probabilityBucketRows,
    priceBucketRows,
    sideOutcomeRows,
    sideSummaryRows,
    bucketRows: [...edgeBucketRows, ...probabilityBucketRows, ...priceBucketRows],
  };
}

function strategySummaryFromTrades(trades: DataRow[], daily: DataRow[], fallback: DataRow): DataRow {
  if (!trades.length) {
    return {
      ...fallback,
      trades: 0,
      total_pnl: 0,
      roi: null,
      hit_rate: null,
      max_drawdown: 0,
      positive_clv_rate: null,
    };
  }
  const rollup = rollupTrades(trades);
  return {
    ...fallback,
    trades: rollup.trades,
    total_contracts: rollup.contracts,
    total_risk: rollup.risk,
    total_pnl: rollup.total_pnl,
    roi: rollup.roi,
    hit_rate: rollup.hit_rate,
    max_drawdown: maxDrawdownFromDaily(daily),
    positive_clv_rate: rollup.positive_clv_rate,
  };
}

function edgeThresholdDiagnostics(trades: DataRow[]): DataRow[] {
  const edgeRows = trades
    .map((trade) => ({ trade, edge: tradeEdge(trade) }))
    .filter((row): row is { trade: DataRow; edge: number } => row.edge !== null);
  if (!edgeRows.length) return [];
  const thresholds = thresholdValues(edgeRows.map((row) => row.edge), [0, 0.03, 0.05, 0.08, 0.12, 0.18, 0.25, 0.35, 0.5]);
  return thresholds
    .map<DataRow>((threshold) => {
      const selected = edgeRows.filter((row) => row.edge >= threshold).map((row) => row.trade);
      return {
        min_edge: threshold,
        group: `>= ${formatNumber(threshold)}`,
        ...rollupTrades(selected),
      };
    })
    .filter((row) => asNumber(row.trades));
}

function bucketTrades(
  trades: DataRow[],
  bucketType: string,
  specs: BucketSpec[],
  getValue: (trade: DataRow) => number | null,
): DataRow[] {
  return specs
    .map<DataRow>((spec) => {
      const selected = trades.filter((trade) => {
        const value = getValue(trade);
        return value !== null && value >= spec.min && value < spec.max;
      });
      return {
        bucket_type: bucketType,
        group: spec.label,
        bucket_min: Number.isFinite(spec.min) ? spec.min : null,
        bucket_max: Number.isFinite(spec.max) ? spec.max : null,
        ...rollupTrades(selected),
      };
    })
    .filter((row) => asNumber(row.trades));
}

function sideOutcomeDiagnostics(trades: DataRow[]): DataRow[] {
  const sides = sortedSideNames(unique(trades.map((trade) => normalizedSide(trade))));
  const rows: DataRow[] = [];
  for (const side of sides) {
    const sideTrades = trades.filter((trade) => normalizedSide(trade) === side);
    const totalContracts = sideTrades.reduce((sum, trade) => sum + tradeContracts(trade), 0);
    for (const outcome of ["profited", "lost", "flat"]) {
      const selected = sideTrades.filter((trade) => tradeOutcome(trade) === outcome);
      const contracts = selected.reduce((sum, trade) => sum + tradeContracts(trade), 0);
      const pnl = selected.reduce((sum, trade) => sum + (asNumber(trade.pnl) ?? 0), 0);
      rows.push({
        side,
        outcome,
        trades: selected.length,
        contracts,
        pct_contracts: totalContracts ? contracts / totalContracts : 0,
        pnl,
        avg_pnl: selected.length ? pnl / selected.length : null,
        avg_pnl_per_contract: contracts ? pnl / contracts : null,
      });
    }
  }
  return rows;
}

function sideSummaryDiagnostics(trades: DataRow[]): DataRow[] {
  return sortedSideNames(unique(trades.map((trade) => normalizedSide(trade))))
    .map<DataRow>((side) => ({
      side,
      ...rollupTrades(trades.filter((trade) => normalizedSide(trade) === side)),
    }))
    .filter((row) => asNumber(row.trades));
}

function rollupTrades(trades: DataRow[]): DataRow {
  const contracts = trades.reduce((sum, trade) => sum + tradeContracts(trade), 0);
  const risk = trades.reduce((sum, trade) => sum + tradeRisk(trade), 0);
  const totalPnl = trades.reduce((sum, trade) => sum + (asNumber(trade.pnl) ?? 0), 0);
  const hits = trades.reduce((sum, trade) => sum + tradeHit(trade), 0);
  const contractHits = trades.reduce((sum, trade) => sum + tradeHit(trade) * tradeContracts(trade), 0);
  const profitableContracts = trades.filter((trade) => (asNumber(trade.pnl) ?? 0) > 0).reduce((sum, trade) => sum + tradeContracts(trade), 0);
  const lossContracts = trades.filter((trade) => (asNumber(trade.pnl) ?? 0) < 0).reduce((sum, trade) => sum + tradeContracts(trade), 0);
  const grossProfit = trades.filter((trade) => (asNumber(trade.pnl) ?? 0) > 0).reduce((sum, trade) => sum + (asNumber(trade.pnl) ?? 0), 0);
  const grossLoss = trades.filter((trade) => (asNumber(trade.pnl) ?? 0) < 0).reduce((sum, trade) => sum + (asNumber(trade.pnl) ?? 0), 0);
  const winTrades = trades.filter((trade) => (asNumber(trade.pnl) ?? 0) > 0);
  const lossTrades = trades.filter((trade) => (asNumber(trade.pnl) ?? 0) < 0);
  const clvValues = trades.map((trade) => asNumber(trade.clv)).filter((value): value is number => value !== null);
  return {
    trades: trades.length,
    contracts,
    risk,
    total_pnl: totalPnl,
    pnl: totalPnl,
    roi: risk ? totalPnl / risk : null,
    hit_rate: trades.length ? hits / trades.length : null,
    contract_hit_rate: contracts ? contractHits / contracts : null,
    profitable_contract_rate: contracts ? profitableContracts / contracts : null,
    loss_contract_rate: contracts ? lossContracts / contracts : null,
    gross_profit: grossProfit,
    gross_loss: grossLoss,
    avg_win_pnl: winTrades.length ? grossProfit / winTrades.length : null,
    avg_loss_pnl: lossTrades.length ? grossLoss / lossTrades.length : null,
    avg_pnl_per_trade: trades.length ? totalPnl / trades.length : null,
    avg_pnl_per_contract: contracts ? totalPnl / contracts : null,
    avg_entry_price: averageNumber(trades.map(tradeEntryPrice)),
    avg_edge: averageNumber(trades.map(tradeEdge)),
    avg_model_probability: averageNumber(trades.map(tradeModelProbability)),
    avg_clv: averageNumber(clvValues),
    positive_clv_rate: clvValues.length ? clvValues.filter((value) => value > 0).length / clvValues.length : null,
  };
}

function maxDrawdownFromDaily(rows: DataRow[]): number {
  let peak = 0;
  let maxDrawdown = 0;
  for (const row of [...rows].sort((left, right) => asText(left.target_date).localeCompare(asText(right.target_date)))) {
    const cumulative = asNumber(row.cumulative_pnl);
    if (cumulative === null) continue;
    peak = Math.max(peak, cumulative);
    maxDrawdown = Math.min(maxDrawdown, cumulative - peak);
  }
  return maxDrawdown;
}

function tradeContracts(trade: DataRow): number {
  return Math.max(0, asNumber(trade.contracts) ?? asNumber(trade.quantity) ?? 1);
}

function tradeEntryPrice(trade: DataRow): number | null {
  return asNumber(trade.entry_price) ?? asNumber(trade.entry_ask) ?? asNumber(trade.market_midpoint);
}

function tradeRisk(trade: DataRow): number {
  const explicit = asNumber(trade.risk) ?? asNumber(trade.cost);
  if (explicit !== null) return explicit;
  return (tradeEntryPrice(trade) ?? 0) * tradeContracts(trade);
}

function tradeEdge(trade: DataRow): number | null {
  return asNumber(trade.edge) ?? asNumber(trade.raw_edge) ?? asNumber(trade.predicted_reward);
}

function tradeModelProbability(trade: DataRow): number | null {
  return asNumber(trade.model_probability) ?? asNumber(trade.outcome_probability) ?? asNumber(trade.win_probability);
}

function tradeHit(trade: DataRow): number {
  const hit = asNumber(trade.hit);
  if (hit !== null) return hit;
  return (asNumber(trade.pnl) ?? 0) > 0 ? 1 : 0;
}

function normalizedSide(trade: DataRow): string {
  return asText(trade.side || trade.contract_side || "unknown").toLowerCase() || "unknown";
}

function tradeOutcome(trade: DataRow): "profited" | "lost" | "flat" {
  const pnl = asNumber(trade.pnl) ?? 0;
  if (pnl > 0) return "profited";
  if (pnl < 0) return "lost";
  return "flat";
}

function averageNumber(values: Array<number | null>): number | null {
  const numeric = values.filter((value): value is number => value !== null && Number.isFinite(value));
  return numeric.length ? numeric.reduce((sum, value) => sum + value, 0) / numeric.length : null;
}

function thresholdValues(values: number[], fixed: number[]): number[] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const candidates = [...fixed.filter((value) => value >= min - 0.001 && value <= max + 0.001)];
  const sorted = [...values].sort((left, right) => left - right);
  for (const pct of [0, 0.2, 0.4, 0.6, 0.8]) {
    const value = sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor(pct * (sorted.length - 1))))];
    candidates.push(Number(value.toFixed(3)));
  }
  return [...new Set(candidates)].sort((left, right) => left - right).slice(0, 16);
}

function sortedSideNames(sides: string[]): string[] {
  const rank: Record<string, number> = { yes: 0, no: 1, unknown: 3 };
  return [...sides].sort((left, right) => (rank[left] ?? 2) - (rank[right] ?? 2) || left.localeCompare(right));
}

function edgeBucketSpecs(): BucketSpec[] {
  return [
    { label: "< 0.03", min: Number.NEGATIVE_INFINITY, max: 0.03 },
    { label: "0.03-0.05", min: 0.03, max: 0.05 },
    { label: "0.05-0.08", min: 0.05, max: 0.08 },
    { label: "0.08-0.12", min: 0.08, max: 0.12 },
    { label: "0.12-0.18", min: 0.12, max: 0.18 },
    { label: "0.18-0.25", min: 0.18, max: 0.25 },
    { label: "0.25+", min: 0.25, max: Number.POSITIVE_INFINITY },
  ];
}

function probabilityBucketSpecs(): BucketSpec[] {
  return [
    { label: "< 0.50", min: Number.NEGATIVE_INFINITY, max: 0.5 },
    { label: "0.50-0.70", min: 0.5, max: 0.7 },
    { label: "0.70-0.85", min: 0.7, max: 0.85 },
    { label: "0.85-0.93", min: 0.85, max: 0.93 },
    { label: "0.93-0.97", min: 0.93, max: 0.97 },
    { label: "0.97+", min: 0.97, max: Number.POSITIVE_INFINITY },
  ];
}

function entryPriceBucketSpecs(): BucketSpec[] {
  return [
    { label: "< 0.15", min: Number.NEGATIVE_INFINITY, max: 0.15 },
    { label: "0.15-0.35", min: 0.15, max: 0.35 },
    { label: "0.35-0.55", min: 0.35, max: 0.55 },
    { label: "0.55-0.75", min: 0.55, max: 0.75 },
    { label: "0.75+", min: 0.75, max: Number.POSITIVE_INFINITY },
  ];
}

function ArtifactsView({ activeArtifact, activeReport, control, onSelectArtifact, onSelectReport }: RouterProps) {
  const artifacts = control?.artifacts ?? [];
  const reports = control?.reports ?? [];
  const selected = activeArtifact ?? activeReport ?? artifacts[0] ?? reports[0];
  return (
    <div className="two-pane">
      <Panel title="Artifacts" subtitle="Every discovered local artifact, including exports and reports.">
        <div className="artifact-list">
          {artifacts.map((artifact) => (
            <button className={`artifact-row ${artifact.id === selected?.id ? "active" : ""}`} key={artifact.id} type="button" onClick={() => onSelectArtifact(artifact.id)}>
              <span>
                <strong>{artifact.id}</strong>
                <em>{artifact.artifact_type ?? "artifact"} | {shortPath(artifact.path)}</em>
              </span>
              <span className="artifact-meta">
                <StatusPill status={artifact.status ?? "unknown"} />
                <b>{formatNumber(sumCounts(artifact.table_counts))} rows</b>
              </span>
            </button>
          ))}
        </div>
      </Panel>
      <Panel title="Reports" subtitle="Model, strategy, and quality reports.">
        <div className="artifact-list">
          {reports.map((report) => (
            <button className={`artifact-row ${report.id === selected?.id ? "active" : ""}`} key={report.id} type="button" onClick={() => { onSelectArtifact(report.id); onSelectReport(report.id); }}>
              <span>
                <strong>{report.id}</strong>
                <em>{report.artifact_type ?? "report"} | source {report.source_export_id ?? "-"}</em>
              </span>
              <span className="artifact-meta"><b>{formatNumber(sumCounts(report.table_counts))} rows</b></span>
            </button>
          ))}
        </div>
      </Panel>
      <Panel className="span-all" title="Selected Artifact">
        {selected ? <ArtifactSummary artifact={selected} /> : <EmptyState compact label="Select an artifact." />}
      </Panel>
    </div>
  );
}

function LabView({ activeExport, activeReport, control, kind, onRefreshControl, onSelectJob, onSetMode, workbench }: RouterProps & { kind: "model" | "strategy" }) {
  const entries = kind === "model" ? control?.models ?? [] : control?.strategies ?? [];
  const modelReports = (control?.reports ?? []).filter((report) => report.artifact_type === "model_report");
  const linkedModelReports = activeExport?.id
    ? modelReports.filter((report) => report.source_export_id === activeExport.id)
    : [];
  const preferredModelReportPath =
    workbench?.selection.report_path ||
    (activeReport?.artifact_type === "model_report" ? activeReport.path : "") ||
    linkedModelReports[0]?.path ||
    modelReports[0]?.path ||
    "";
  const [entryId, setEntryId] = useState(entries[0]?.id ?? "");
  const entry = findById(entries, entryId) ?? entries[0];
  const entrypoints = Object.entries(entry?.entrypoints ?? {});
  const [entrypoint, setEntrypoint] = useState(entrypoints[0]?.[0] ?? "");
  const selectedEntrypoint = entry?.entrypoints?.[entrypoint] as EntrypointSpec | undefined;
  const schema = selectedEntrypoint?.params_schema ?? entry?.params_schema;
  const [params, setParams] = useState<DataRow>({});
  const [modelReportPath, setModelReportPath] = useState(preferredModelReportPath);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!entryId && entries[0]?.id) setEntryId(entries[0].id);
  }, [entries, entryId]);

  useEffect(() => {
    const nextEntrypoint = Object.keys(entry?.entrypoints ?? {})[0] ?? "";
    if (entry && !entry.entrypoints?.[entrypoint]) setEntrypoint(nextEntrypoint);
  }, [entry, entrypoint]);

  useEffect(() => {
    setParams(defaultParams(schema));
  }, [entrypoint, schema]);

  useEffect(() => {
    if (kind !== "strategy" || !preferredModelReportPath) return;
    setModelReportPath((current) => {
      if (!current || isQualityReportPath(current)) return preferredModelReportPath;
      return current;
    });
  }, [kind, preferredModelReportPath]);

  async function runCompatibility() {
    if (!entry || !activeExport?.path || kind !== "model") return;
    setBusy(true);
    setError("");
    try {
      const result = await checkModelCompatibility({ model_id: entry.id, dataset_path: activeExport.path });
      setMessage(result.compatible ? "Dataset is compatible." : `Blocking: ${result.blocking.join(", ")}`);
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(false);
    }
  }

  async function runJob() {
    if (!entry || !entrypoint) return;
    if (kind === "strategy" && !asText(modelReportPath)) {
      setError("Model report path is required for strategy runs.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const job = await createJob({
        kind,
        registry_id: entry.id,
        entrypoint,
        params: {
          ...coercedParams(params, schema),
          dataset_path: activeExport?.path ?? "",
          model_report_path: kind === "strategy" ? asText(modelReportPath) : undefined,
          output_path: asText(params.output_path),
          timeout_seconds: params.timeout_seconds || 1800,
        },
      });
      setMessage(`Queued ${job.id}`);
      onSelectJob(job.id);
      onSetMode("jobs");
      await onRefreshControl();
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="view-stack">
      <div className="two-pane">
        <Panel title={kind === "model" ? "Registered Models" : "Registered Strategies"}>
          <div className="artifact-list">
            {entries.map((item) => (
              <button className={`artifact-row ${item.id === entry?.id ? "active" : ""}`} key={item.id} type="button" onClick={() => setEntryId(item.id)}>
                <span>
                  <strong>{item.label ?? item.id}</strong>
                  <em>{item.id} | v{String(item.version ?? 1)}</em>
                </span>
              </button>
            ))}
          </div>
        </Panel>
        <Panel title="Run Configuration" subtitle={entry?.purpose ?? entry?.description ?? "Registry-defined command runner."}>
          <div className="form-grid">
            <label>
              Entrypoint
              <select value={entrypoint} onChange={(event) => setEntrypoint(event.target.value)}>
                {entrypoints.map(([key, spec]) => <option key={key} value={key}>{spec.label ?? titleCase(key)}</option>)}
              </select>
            </label>
            <label>
              Dataset
              <input value={activeExport?.path ?? ""} readOnly />
            </label>
            {kind === "strategy" ? (
              <label>
                Model report
                <div className="path-input-row">
                  <input
                    value={modelReportPath}
                    onChange={(event) => setModelReportPath(event.target.value)}
                    placeholder="reports/model/neuralcaster_v2_..."
                    spellCheck={false}
                  />
                  <CopyPathButton value={modelReportPath} />
                </div>
              </label>
            ) : null}
          </div>
          {kind === "strategy" ? (
            <PathQuickFill
              fallbackPath={workbench?.selection.report_path ?? ""}
              modelReports={modelReports}
              linkedModelReports={linkedModelReports}
              onUse={setModelReportPath}
            />
          ) : null}
          <SchemaForm params={params} schema={schema} onChange={setParams} />
          <div className="row-actions">
            {kind === "model" ? <button className="ghost" disabled={busy || !activeExport?.path} type="button" onClick={() => void runCompatibility()}>Check compatibility</button> : null}
            <button disabled={busy || !entry || !entrypoint || !activeExport?.path} type="button" onClick={() => void runJob()}>
              {busy ? "Working..." : `Run ${kind}`}
            </button>
          </div>
          {message ? <div className="notice">{message}</div> : null}
          {error ? <div className="notice error">{error}</div> : null}
        </Panel>
      </div>
      <Panel title="Artifact Contract">
        <div className="detail-list detail-list-three">
          <Detail label="Selected" value={entry?.id ?? "-"} />
          <Detail label="Version" value={String(entry?.version ?? "-")} />
          <Detail label="Produces" value={selectedEntrypoint?.produces?.contract ?? selectedEntrypoint?.produces?.artifact_type ?? "-"} />
          <Detail label="Required files" value={(entry?.artifact_contract?.required_files ?? []).join(", ") || "-"} />
          <Detail label="Optional files" value={(entry?.artifact_contract?.optional_files ?? []).join(", ") || "-"} />
          <Detail label="Working source" value={entry?.source ?? entry?.provider ?? "-"} />
        </div>
      </Panel>
    </div>
  );
}

function JobsView({ activeJob, control, onRefreshControl, onSelectJob }: RouterProps) {
  const [logs, setLogs] = useState("");
  const [logError, setLogError] = useState("");
  const selected = activeJob ?? control?.jobs[0];
  useEffect(() => {
    if (!selected?.id) return;
    let cancelled = false;
    getJobLogs(selected.id)
      .then((result) => {
        if (!cancelled) {
          setLogs(result.log);
          setLogError("");
        }
      })
      .catch((error) => {
        if (!cancelled) setLogError(readableError(error));
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.id]);

  async function handleCancel() {
    if (!selected?.id) return;
    await cancelJob(selected.id);
    await onRefreshControl();
  }

  return (
    <div className="view-stack">
      <DataTable
        title="Job Queue"
        rows={control?.jobs ?? []}
        preferredColumns={["id", "kind", "registry_id", "entrypoint", "status", "created_utc", "updated_utc", "output_path", "returncode", "error"]}
        selectedRowKey="id"
        selectedRowValue={selected?.id}
        onRowSelect={(row) => onSelectJob(asText(row.id))}
      />
      <Panel
        title="Selected Job"
        actions={
          <div className="row-actions tight">
            <button className="ghost" type="button" onClick={() => void onRefreshControl()}>Refresh</button>
            {selected && isActiveJob(selected.status) ? <button className="ghost" type="button" onClick={() => void handleCancel()}>Cancel</button> : null}
          </div>
        }
      >
        {selected ? (
          <div className="job-layout">
            <div className="detail-list">
              <Detail label="Job" value={selected.id} />
              <Detail label="Status" value={selected.status} />
              <Detail label="Registry" value={`${selected.kind ?? "-"} / ${selected.registry_id ?? "-"}`} />
              <Detail label="Entrypoint" value={selected.entrypoint ?? "-"} />
              <Detail label="Output" value={selected.output_path ?? "-"} />
              <Detail label="Return code" value={String(selected.returncode ?? "-")} />
            </div>
            <pre className="log-box">{logError ? logError : logs || "No log output."}</pre>
          </div>
        ) : (
          <EmptyState compact label="No jobs tracked yet." />
        )}
      </Panel>
    </div>
  );
}

function RegistryView({ control }: { control: ControlSnapshot | null }) {
  const groups = Object.entries(control?.registry ?? {});
  return (
    <div className="view-stack">
      {groups.map(([kind, entries]) => (
        <Panel key={kind} title={titleCase(kind)} subtitle={`${formatNumber(entries.length)} registered`}>
          <div className="registry-grid">
            {entries.map((entry) => (
              <div className="registry-card" key={`${kind}-${entry.id}`}>
                <strong>{entry.label ?? entry.id}</strong>
                <span>{entry.id}</span>
                <p>{entry.purpose ?? entry.description ?? "No description provided."}</p>
                <div className="chip-row">
                  <span className="chip">v{String(entry.version ?? 1)}</span>
                  {Object.keys(entry.entrypoints ?? {}).slice(0, 4).map((name) => <span className="chip" key={name}>{name}</span>)}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      ))}
    </div>
  );
}

function BotView({ control }: { control: ControlSnapshot | null }) {
  const [status, setStatus] = useState<DataRow | null>(null);
  const runtime = control?.registry.bot_runtime?.[0];
  useEffect(() => {
    let cancelled = false;
    getBotStatus().then((result) => {
      if (!cancelled) setStatus(result);
    }).catch(() => {
      if (!cancelled) setStatus({ status: "deferred", message: "Bot endpoint is not reachable." });
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="view-stack">
      <Panel title="Runtime Contract" subtitle="Telemetry is reserved until deployed bot monitoring is wired.">
        <div className="detail-list detail-list-three">
          <Detail label="Runtime" value={runtime?.label ?? runtime?.id ?? "not registered"} />
          <Detail label="Provider" value={runtime?.provider ?? "-"} />
          <Detail label="Endpoint status" value={asText(status?.status ?? "deferred")} />
          <Detail label="Message" value={asText(status?.message ?? "No live telemetry configured.")} />
        </div>
      </Panel>
      <div className="deferred-grid">
        {["Runtime Health", "Positions", "Orders", "Decisions", "Risk Exposure", "Latency", "Collector Health", "Alerts"].map((item) => (
          <Panel title={item} key={item}>
            <div className="deferred-card">
              <Bot aria-hidden="true" size={30} />
              <strong>Deferred</strong>
              <span>Reserved for the deployed weather bot monitoring phase.</span>
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}

function SettingsView({ control, sources, workbench }: { control: ControlSnapshot | null; sources: SourcesResponse | null; workbench: LoadResponse | null }) {
  return (
    <div className="view-stack">
      <div className="view-grid">
        <Panel title="API">
          <div className="detail-list">
            <Detail label="Control API" value={controlApiBaseUrl} />
            <Detail label="State" value={control?.errors.length ? "partial" : "ready"} />
            <Detail label="Last backend refresh" value={formatDateTime(control?.loadedAt)} />
            <Detail label="Errors" value={String(control?.errors.length ?? 0)} />
          </div>
        </Panel>
        <Panel title="Roots">
          <div className="detail-list">
            <Detail label="Registry root" value={control?.registryRoot || "builtin"} />
            <Detail label="Data root" value={sources?.roots.data ?? "n/a"} />
            <Detail label="Model report root" value={sources?.roots.report ?? "n/a"} />
            <Detail label="Quality root" value={sources?.roots.quality ?? "n/a"} />
            <Detail label="Strategy root" value={sources?.roots.strategy ?? "n/a"} />
            <Detail label="Loaded source" value={workbench?.selection.data_path ?? "none"} />
          </div>
        </Panel>
      </div>
      {control?.errors.length ? <InlineErrors errors={control.errors} /> : null}
    </div>
  );
}

function WorkbenchInspector({
  activeArtifact,
  activeExport,
  activeJob,
  activeReport,
  control,
  filters,
  mode,
  onChangeSource,
  onRefreshControl,
  onSetFilters,
  workbench,
}: {
  activeArtifact?: ArtifactMetadata;
  activeExport?: ArtifactMetadata;
  activeJob?: JobRecord;
  activeReport?: ArtifactMetadata;
  control: ControlSnapshot | null;
  filters: DateFilters;
  mode: WorkbenchMode;
  onChangeSource: () => void;
  onRefreshControl: () => Promise<void>;
  onSetFilters: (filters: DateFilters) => void;
  workbench: LoadResponse | null;
}) {
  const cities = workbench?.metadata.cities ?? activeExport?.coverage?.cities ?? [];
  return (
    <aside className="inspector">
      <div className="inspector-head">
        <p className="eyebrow">Inspector</p>
        <h2>{MODE_META[mode].label}</h2>
        <p>{MODE_META[mode].subtitle}</p>
      </div>

      <button className="full-button" type="button" onClick={onChangeSource}>
        <Database aria-hidden="true" size={16} />
        Change Source
      </button>
      <button className="ghost full-button" type="button" onClick={() => void onRefreshControl()}>
        <RefreshCcw aria-hidden="true" size={16} />
        Refresh Backend
      </button>

      <PathClipboardPanel
        rows={uniquePathRows([
          { label: "Loaded export", value: activeExport?.path ?? workbench?.selection.data_path },
          { label: "Loaded model report", value: workbench?.selection.report_path },
          { label: "Loaded quality report", value: workbench?.selection.quality_path },
          { label: "Loaded strategy report", value: workbench?.selection.strategy_path },
          { label: "Selected report", value: activeReport?.path },
          { label: "Selected artifact", value: activeArtifact?.path },
        ])}
      />

      <section className="inspector-section">
        <h3>Filters</h3>
        <label className="inspector-field">
          City
          <select value={filters.city} onChange={(event) => onSetFilters({ ...filters, city: event.target.value })}>
            <option value="all">All cities</option>
            {cities.map((city) => <option key={city} value={city}>{city}</option>)}
          </select>
        </label>
        <div className="inline-date-fields">
          <label className="inspector-field">
            Start
            <input type="date" value={filters.start} onChange={(event) => onSetFilters({ ...filters, start: event.target.value })} />
          </label>
          <label className="inspector-field">
            End
            <input type="date" value={filters.end} onChange={(event) => onSetFilters({ ...filters, end: event.target.value })} />
          </label>
        </div>
      </section>

      <section className="inspector-section">
        <h3>Selected Export</h3>
        <div className="detail-list">
          <Detail label="ID" value={activeExport?.id ?? workbench?.selection.export_id ?? "-"} />
          <Detail label="Rows" value={formatNumber(sumCounts(activeExport?.table_counts ?? workbench?.metadata.table_counts))} />
          <Detail label="Range" value={dateRangeFrom(activeExport?.coverage?.date_range ?? workbench?.metadata.date_range)} />
          <Detail label="Path" value={activeExport?.path ?? workbench?.selection.data_path ?? "-"} />
        </div>
      </section>

      <section className="inspector-section">
        <h3>Selected Artifact</h3>
        <div className="detail-list">
          <Detail label="Artifact" value={activeArtifact?.id ?? "-"} />
          <Detail label="Type" value={activeArtifact?.artifact_type ?? "-"} />
          <Detail label="Files" value={String(activeArtifact?.files?.length ?? 0)} />
          <Detail label="Contract" value={activeArtifact?.contract ?? "legacy"} />
        </div>
      </section>

      <section className="inspector-section">
        <h3>Selected Report / Job</h3>
        <div className="detail-list">
          <Detail label="Report" value={activeReport?.id ?? "-"} />
          <Detail label="Job" value={activeJob?.id ?? "-"} />
          <Detail label="Job status" value={activeJob?.status ?? "-"} />
        </div>
      </section>

      <section className="inspector-section">
        <h3>Backend Counts</h3>
        <div className="mini-bars">
          <MiniBar label="Exports" value={control?.exports.length ?? 0} max={Math.max(1, control?.artifacts.length ?? 1)} />
          <MiniBar label="Reports" value={control?.reports.length ?? 0} max={Math.max(1, control?.artifacts.length ?? 1)} />
          <MiniBar label="Jobs" value={control?.jobs.length ?? 0} max={Math.max(1, control?.jobs.length ?? 1)} />
        </div>
      </section>
    </aside>
  );
}

function PathQuickFill({
  fallbackPath,
  linkedModelReports,
  modelReports,
  onUse,
}: {
  fallbackPath: string;
  linkedModelReports: ArtifactMetadata[];
  modelReports: ArtifactMetadata[];
  onUse: (path: string) => void;
}) {
  const rows = uniquePathRows([
    { label: "Loaded model report", value: fallbackPath },
    ...linkedModelReports.slice(0, 6).map((report) => ({ label: `Linked model: ${report.id}`, value: report.path })),
    ...modelReports
      .filter((report) => !linkedModelReports.some((linked) => linked.id === report.id))
      .slice(0, 6)
      .map((report) => ({ label: `Model report: ${report.id}`, value: report.path })),
  ]);
  if (!rows.length) {
    return <div className="notice error">No model reports were discovered. Load or create a model report under reports/model.</div>;
  }
  return (
    <div className="path-picker">
      <span>Model report paths</span>
      {rows.map((row) => (
        <div className="copy-path-row compact" key={row.value}>
          <button className="ghost tiny use-path-button" type="button" onClick={() => onUse(row.value)}>
            Use
          </button>
          <input aria-label={row.label} value={row.value} readOnly />
          <CopyPathButton value={row.value} label={`Copy ${row.label}`} />
        </div>
      ))}
    </div>
  );
}

function PathClipboardPanel({ rows }: { rows: PathRow[] }) {
  if (!rows.length) return null;
  return (
    <section className="inspector-section">
      <h3>Loaded Paths</h3>
      <div className="copy-path-list">
        {rows.map((row) => (
          <label className="copy-path-row" key={`${row.label}-${row.value}`}>
            <span>{row.label}</span>
            <input value={row.value} readOnly />
            <CopyPathButton value={row.value} label={`Copy ${row.label}`} />
          </label>
        ))}
      </div>
    </section>
  );
}

function CopyPathButton({ label = "Copy path", value }: { label?: string; value?: string | null }) {
  const [copied, setCopied] = useState(false);
  async function handleCopy() {
    if (!value) return;
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
      else fallbackCopy(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      fallbackCopy(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    }
  }
  const Icon = copied ? CheckCircle2 : Copy;
  return (
    <button className="icon-button ghost copy-path-button" disabled={!value} title={copied ? "Copied" : label} type="button" onClick={() => void handleCopy()}>
      <Icon aria-hidden="true" size={16} />
      <span className="sr-only">{copied ? "Copied" : label}</span>
    </button>
  );
}

function ArtifactSummary({ artifact }: { artifact: ArtifactMetadata }) {
  return (
    <div className="view-stack">
      <div className="detail-list detail-list-three">
        <Detail label="ID" value={artifact.id} />
        <Detail label="Type" value={artifact.artifact_type ?? "-"} />
        <Detail label="Status" value={artifact.status ?? "-"} />
        <Detail label="Source export" value={artifact.source_export_id ?? "-"} />
        <Detail label="Rows" value={formatNumber(sumCounts(artifact.table_counts))} />
        <Detail label="Path" value={artifact.path ?? "-"} />
      </div>
      <ChartPanel title="Artifact Tables" option={barOption(tableCountRows(artifact), "table", "rows", "Rows")} />
      <DataTable title="Files" rows={(artifact.files ?? []).map((file) => ({ file, rows: artifact.table_counts?.[file] ?? 0, schema_columns: Object.keys(artifact.schemas?.[file]?.columns ?? {}).length }))} />
      {artifact.summary ? <JsonBlock title="Summary JSON" value={artifact.summary} /> : null}
    </div>
  );
}

function SchemaForm({ onChange, params, schema }: { onChange: (params: DataRow) => void; params: DataRow; schema?: { properties?: Record<string, JsonSchemaProperty>; required?: string[] } }) {
  const properties = schema?.properties ?? {};
  const keys = Object.keys(properties);
  const required = new Set(schema?.required ?? []);
  if (!keys.length) return <div className="empty-panel">No configurable parameters are registered for this entrypoint.</div>;
  return (
    <div className="schema-form">
      {keys.map((key) => {
        const spec = properties[key];
        const value = params[key] ?? "";
        const hint = parameterHint(key, spec, required.has(key));
        return (
          <label className="schema-row" key={key}>
            <span>
              {spec.title ?? titleCase(key)}
              <em>{schemaTypeLabel(key, spec, required.has(key))}</em>
            </span>
            {spec.enum?.length ? (
              <select value={asText(value)} onChange={(event) => onChange({ ...params, [key]: event.target.value })}>
                {spec.enum.map((item) => <option key={String(item)} value={String(item)}>{String(item)}</option>)}
              </select>
            ) : spec.type === "boolean" ? (
              <select value={String(Boolean(value))} onChange={(event) => onChange({ ...params, [key]: event.target.value === "true" })}>
                <option value="false">false</option>
                <option value="true">true</option>
              </select>
            ) : (
              <input
                {...inputPropsForSchema(key, spec)}
                value={asText(value)}
                onChange={(event) => onChange({ ...params, [key]: event.target.value })}
              />
            )}
            {hint ? <small className="schema-hint">{hint}</small> : null}
          </label>
        );
      })}
      <label className="schema-row">
        <span>Output path<em>control</em></span>
        <input value={asText(params.output_path)} onChange={(event) => onChange({ ...params, output_path: event.target.value })} placeholder="reports/model/custom_run" />
      </label>
      <label className="schema-row">
        <span>Timeout seconds<em>control</em></span>
        <input value={asText(params.timeout_seconds ?? 1800)} onChange={(event) => onChange({ ...params, timeout_seconds: event.target.value })} />
      </label>
    </div>
  );
}

function inputPropsForSchema(key: string, spec: JsonSchemaProperty): InputHTMLAttributes<HTMLInputElement> {
  const format = schemaFormat(key, spec);
  const type = schemaPrimaryType(spec);
  const props: InputHTMLAttributes<HTMLInputElement> = {
    placeholder: parameterPlaceholder(key, spec),
    title: parameterHint(key, spec, false),
  };
  if (format === "date") {
    props.type = "date";
    props.pattern = "\\d{4}-\\d{2}-\\d{2}";
  } else if (format === "date-time") {
    props.type = "text";
    props.placeholder ||= "YYYY-MM-DDTHH:mm:ssZ";
  } else if (type === "number" || type === "integer") {
    props.type = "number";
    props.step = type === "integer" ? "1" : "any";
    if (spec.minimum !== undefined) props.min = spec.minimum;
    if (spec.maximum !== undefined) props.max = spec.maximum;
  } else {
    props.type = "text";
  }
  if (spec.pattern) props.pattern = spec.pattern;
  return props;
}

function schemaTypeLabel(key: string, spec: JsonSchemaProperty, isRequired: boolean): string {
  const parts = [schemaPrimaryType(spec) ?? "value"];
  const format = schemaFormat(key, spec);
  if (format) parts.push(format);
  if (isRequired) parts.push("required");
  return parts.join(" / ");
}

function parameterHint(key: string, spec: JsonSchemaProperty, isRequired: boolean): string {
  const parts: string[] = [];
  if (spec.description) parts.push(spec.description);
  const format = schemaFormat(key, spec);
  if (format) parts.push(`Format: ${formatHelp(format)}.`);
  if (spec.pattern) parts.push(`Pattern: ${spec.pattern}.`);
  if (spec.minimum !== undefined || spec.maximum !== undefined) {
    const range = [spec.minimum !== undefined ? `min ${spec.minimum}` : "", spec.maximum !== undefined ? `max ${spec.maximum}` : ""].filter(Boolean).join(", ");
    if (range) parts.push(`Range: ${range}.`);
  }
  const example = parameterExample(spec);
  if (example !== "") parts.push(`Example: ${example}.`);
  if (isRequired) parts.push("Required.");
  return parts.join(" ");
}

function parameterPlaceholder(key: string, spec: JsonSchemaProperty): string {
  const explicit = asText(spec.placeholder);
  if (explicit) return explicit;
  const example = parameterExample(spec);
  if (example) return example;
  const format = schemaFormat(key, spec);
  if (format === "date") return "YYYY-MM-DD";
  if (format === "date-time") return "YYYY-MM-DDTHH:mm:ssZ";
  if (format === "time") return "HH:mm:ss";
  if (format === "duration") return "PT1H";
  if (format === "path") return "relative/or/full/path";
  return "";
}

function parameterExample(spec: JsonSchemaProperty): string {
  const examples = Array.isArray(spec.examples) ? spec.examples : [];
  const value = examples.length ? examples[0] : spec.example;
  return value === undefined || value === null ? "" : asText(value);
}

function schemaFormat(key: string, spec: JsonSchemaProperty): string {
  const explicit = asText(spec.format).toLowerCase();
  if (explicit) return explicit;
  if (/_date$/.test(key) || /(^|_)(start|end)$/.test(key)) return "date";
  return "";
}

function schemaPrimaryType(spec: JsonSchemaProperty): string {
  const type = spec.type;
  if (Array.isArray(type)) return asText(type.find((item) => item !== "null") ?? type[0]);
  return asText(type);
}

function formatHelp(format: string): string {
  if (format === "date") return "YYYY-MM-DD";
  if (format === "date-time") return "ISO 8601 date-time, for example 2026-07-17T05:00:00Z";
  if (format === "time") return "HH:mm:ss";
  if (format === "duration") return "ISO 8601 duration, for example PT1H";
  if (format === "path") return "relative path or absolute local path";
  return format;
}

function OperationResult({ operation }: { operation: OperationState }) {
  if (!operation.message && !operation.error && !operation.result) return null;
  return (
    <Panel title="Operation Result">
      {operation.busy ? <div className="notice"><Loader2 className="spin" aria-hidden="true" size={16} /> {operation.message}</div> : null}
      {operation.error ? <div className="notice error">{operation.error}</div> : null}
      {operation.message && !operation.error ? <div className="notice">{operation.message}</div> : null}
      {operation.result ? <JsonBlock title="Response" value={operation.result} /> : null}
    </Panel>
  );
}

function NeedSourcePanel({ compact = false, onSourceOpen }: { compact?: boolean; onSourceOpen: () => void }) {
  return (
    <section className={`empty-panel ${compact ? "" : "large"}`}>
      <Database aria-hidden="true" size={compact ? 24 : 44} />
      <strong>Load a source to use this view.</strong>
      <span>Choose a frozen export and optional model, quality, and strategy reports from the source hub.</span>
      <button type="button" onClick={onSourceOpen}>Open Source Hub</button>
    </section>
  );
}

function Panel({ actions, children, className = "", subtitle, title }: { actions?: ReactNode; children: ReactNode; className?: string; subtitle?: string; title: string }) {
  return (
    <section className={`summary-panel workbench-panel ${className}`}>
      <header className="panel-head">
        <div>
          <h3>{title}</h3>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {actions ? <div className="panel-actions">{actions}</div> : null}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}

function ControlStrip({ children }: { children: ReactNode }) {
  return <div className="control-strip">{children}</div>;
}

function Kpi({ label, tone, value }: { label: string; tone?: "good" | "warn" | "bad"; value: unknown }) {
  return (
    <section className={`kpi-card ${tone ?? ""}`}>
      <span>{label}</span>
      <strong title={kpiTitle(value)}>{formatKpiValue(value)}</strong>
    </section>
  );
}

function Detail({ label, tone, value }: { label: string; tone?: "good" | "warn" | "bad"; value: unknown }) {
  return (
    <div className={tone ? `detail-${tone}` : ""}>
      <span>{label}</span>
      <strong>{asText(value) || "n/a"}</strong>
    </div>
  );
}

function StatusPill({ label, status }: { label?: string; status: string }) {
  const normalized = status.toLowerCase();
  const Icon = normalized.includes("fail") || normalized.includes("error")
    ? AlertTriangle
    : normalized.includes("run") || normalized.includes("queue")
      ? Loader2
      : CheckCircle2;
  return (
    <span className={`status-pill status-${normalized}`}>
      <Icon className={Icon === Loader2 ? "spin" : ""} aria-hidden="true" size={14} />
      {label ?? titleCase(status)}
    </span>
  );
}

function MiniBar({ label, max, value }: { label: string; max: number; value: number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{formatNumber(value)}</strong>
      <i style={{ inlineSize: `${Math.max(4, Math.min(100, max ? (value / max) * 100 : 0))}%` }} />
    </div>
  );
}

function CheckboxGrid({ label, onChange, selected, values }: { label: string; onChange: (values: string[]) => void; selected: string[]; values: string[] }) {
  if (!values.length) return null;
  return (
    <div className="checkbox-grid">
      <span>{label}</span>
      {values.map((value) => (
        <label key={value}>
          <input
            checked={selected.includes(value)}
            type="checkbox"
            onChange={() => onChange(toggle(selected, value))}
          />
          {value}
        </label>
      ))}
    </div>
  );
}

function DataRows({ columns, rows }: { columns: string[]; rows: ReactNode[][] }) {
  const style = { "--data-columns": columns.length } as CSSProperties;
  return (
    <div className="data-rows" style={style}>
      <div className="data-row head">{columns.map((column) => <strong key={column}>{column}</strong>)}</div>
      {rows.map((row, rowIndex) => (
        <div className="data-row" key={rowIndex}>
          {row.map((cell, cellIndex) => <span key={cellIndex}>{cell}</span>)}
        </div>
      ))}
      {!rows.length ? <p className="muted">No rows available.</p> : null}
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <details className="json-block" open>
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function InlineErrors({ errors }: { errors: string[] }) {
  const offlineError = errors.find((error) => error.startsWith("Backend offline"));
  return (
    <div className="notice error">
      <strong>{offlineError ? "Backend offline" : "Backend partial load"}</strong>
      {offlineError ? (
        <>
          <p>{offlineError}</p>
          <code className="notice-command">{workbenchBackendCommand}</code>
        </>
      ) : (
        <ul>
          {errors.slice(0, 6).map((error) => <li key={error}>{error}</li>)}
        </ul>
      )}
    </div>
  );
}

function EmptyState({ compact = false, label }: { compact?: boolean; label: string }) {
  return (
    <section className={`empty-panel ${compact ? "" : "large"}`}>
      <TerminalSquare aria-hidden="true" size={compact ? 24 : 42} />
      <strong>{label}</strong>
    </section>
  );
}

function useAnalysis<T>(section: string, enabled: boolean) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    setLoading(true);
    setError("");
    getAnalysis<T>(section)
      .then((result) => {
        if (!cancelled) setData(result.data);
      })
      .catch((err) => {
        if (!cancelled) setError(readableError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, section]);
  return { data, loading, error };
}

function useSeries(metric: string, enabled: boolean) {
  const [data, setData] = useState<{ rows: DataRow[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled || !metric) return undefined;
    let cancelled = false;
    setLoading(true);
    setError("");
    getSeries(metric)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(readableError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, metric]);
  return { data, loading, error };
}

function useTable(tableName: string, enabled: boolean) {
  const [data, setData] = useState<{ rows: DataRow[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled || !tableName) return undefined;
    let cancelled = false;
    setLoading(true);
    setError("");
    getTable(tableName)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(readableError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, tableName]);
  return { data, loading, error };
}

function linkedToExport<T extends { source_export_id?: string }>(items: T[], exportId: string): T[] {
  if (!exportId) return items;
  const linked = items.filter((item) => item.source_export_id === exportId);
  return linked.length ? linked : items.slice(0, 12);
}

function routeFromUrl(): WorkbenchMode {
  const value = new URL(window.location.href).searchParams.get("view") as WorkbenchMode | null;
  return value && value in MODE_META ? value : "dashboard";
}

function findById<T extends { id: string }>(items: T[], id: string): T | undefined {
  return items.find((item) => item.id === id);
}

function reportsByType(control: ControlSnapshot | null, type: string): ArtifactMetadata[] {
  return (control?.reports ?? []).filter((report) => report.artifact_type === type);
}

function uniqueArtifacts(items: ArtifactMetadata[]): ArtifactMetadata[] {
  const byId = new Map<string, ArtifactMetadata>();
  for (const item of items) {
    const current = byId.get(item.id);
    const currentScore = artifactCompletenessScore(current);
    const nextScore = artifactCompletenessScore(item);
    if (!current || nextScore >= currentScore) byId.set(item.id, item);
  }
  return [...byId.values()];
}

function artifactCompletenessScore(item?: ArtifactMetadata): number {
  if (!item) return 0;
  return (
    Object.keys(item.schemas ?? {}).length * 4 +
    Object.keys(item.table_counts ?? {}).length * 2 +
    (item.files?.length ?? 0)
  );
}

function workbenchArtifact(workbench: LoadResponse | null): ArtifactMetadata | undefined {
  if (!workbench) return undefined;
  return {
    id: workbench.selection.export_id,
    artifact_type: "local_export",
    path: workbench.selection.data_path,
    table_counts: workbench.metadata.table_counts,
    coverage: {
      cities: workbench.metadata.cities,
      date_range: workbench.metadata.date_range,
    },
  };
}

function previewTableRows(preview: ExportPreviewResponse): Array<DataRow & { table?: string; name?: string; effective_columns?: string[]; include_columns?: string[] }> {
  const tables = preview.tables;
  if (Array.isArray(tables)) return tables as Array<DataRow & { table?: string; name?: string; effective_columns?: string[]; include_columns?: string[] }>;
  if (tables && typeof tables === "object") {
    return Object.entries(tables).map(([name, spec]) => ({
      table: name,
      ...(spec as DataRow),
    }));
  }
  return [];
}

function fallbackExportCoverage(artifact: ArtifactMetadata, workbench: LoadResponse | null): { cities: string[]; dateRange?: { start?: string | null; end?: string | null }; snapshotHours: number | null } {
  const sameExport = Boolean(workbench && workbench.selection.export_id === artifact.id);
  return {
    cities: sameExport ? workbench?.metadata.cities ?? [] : [],
    dateRange: sameExport ? workbench?.metadata.date_range : undefined,
    snapshotHours: sameExport ? workbench?.catalog.checkpoints.length ?? null : null,
  };
}

function tableCountRows(artifact?: ArtifactMetadata): DataRow[] {
  return Object.entries(artifact?.table_counts ?? {})
    .map(([table, rows]) => ({ table, rows }))
    .sort((left, right) => Number(right.rows) - Number(left.rows));
}

function rowsFromUnknown(value: unknown): DataRow[] {
  if (Array.isArray(value)) return value.filter((item): item is DataRow => Boolean(item) && typeof item === "object");
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>).map(([key, item]) => {
      if (item && typeof item === "object") return { key, ...(item as DataRow) };
      return { key, value: item };
    });
  }
  return [];
}

function summarizedCount(value: unknown, fallback = 0): number {
  const direct = asNumber(value);
  if (direct !== null) return direct;
  if (Array.isArray(value)) return value.length;
  if (!value || typeof value !== "object") return fallback;
  const record = value as Record<string, unknown>;
  for (const key of ["count", "total", "rows", "missing", "errors", "value"]) {
    const number = asNumber(record[key]);
    if (number !== null) return number;
  }
  const nested = Object.values(record).map((item) => {
    if (Array.isArray(item)) return item.length;
    if (typeof item === "number") return item;
    return 0;
  });
  const nestedTotal = nested.reduce((sum, item) => sum + item, 0);
  return nestedTotal || fallback || Object.keys(record).length;
}

function formatKpiValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "n/a";
  const number = asNumber(value);
  if (number !== null) return formatNumber(number);
  if (Array.isArray(value)) return formatNumber(value.length);
  if (typeof value === "object") return formatNumber(summarizedCount(value));
  return asText(value) || "n/a";
}

function kpiTitle(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined;
  try {
    return JSON.stringify(value);
  } catch {
    return undefined;
  }
}

function filterRows(rows: DataRow[], filters: DateFilters, timeKeys: string[] = ["target_date", "snapshot_time_utc", "x"]): DataRow[] {
  return rows.filter((row) => {
    if (filters.city !== "all" && asText(row.city) !== filters.city && asText(row.group) !== filters.city) return false;
    const dateValue = timeKeys.map((key) => asText(row[key])).find(Boolean);
    const key = dateValue?.slice(0, 10) ?? "";
    if (filters.start && key && key < filters.start) return false;
    if (filters.end && key && key > filters.end) return false;
    return true;
  });
}

function sumCounts(counts?: Record<string, number>): number {
  return Object.values(counts ?? {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

function countDateRangeDays(range?: { start?: string | null; end?: string | null }): number | null {
  if (!range?.start || !range?.end) return null;
  const start = Date.parse(`${range.start}T00:00:00Z`);
  const end = Date.parse(`${range.end}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  return Math.round((end - start) / 86_400_000) + 1;
}

function dateRangeFrom(range?: { start?: string | null; end?: string | null }): string {
  if (!range?.start && !range?.end) return "-";
  return `${range.start ?? "?"} to ${range.end ?? "?"}`;
}

function isNumericSchemaType(type?: string): boolean {
  return Boolean(type && /number|integer|float|double|decimal/i.test(type));
}

function aggregationLabel(aggregation: string, yField: string): string {
  const field = titleCase(yField || "Rows");
  if (aggregation === "none") return field;
  if (aggregation === "count") return `Count of ${field}`;
  if (aggregation === "avg") return `Avg of ${field}`;
  if (aggregation === "sum") return `Sum of ${field}`;
  if (aggregation === "min") return `Min of ${field}`;
  if (aggregation === "max") return `Max of ${field}`;
  return titleCase(aggregation);
}

function escapeHtml(value: unknown): string {
  return asText(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function shortPath(value?: string | null): string {
  if (!value) return "-";
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts.slice(-2).join("/");
}

function uniquePathRows(rows: { label: string; value?: string | null }[]): PathRow[] {
  const seen = new Set<string>();
  const output: PathRow[] = [];
  for (const row of rows) {
    const value = asText(row.value).trim();
    if (!value || value === "-" || seen.has(value)) continue;
    seen.add(value);
    output.push({ label: row.label, value });
  }
  return output;
}

function isQualityReportPath(value: string): boolean {
  return /[\\/]reports[\\/]quality[\\/]/i.test(value);
}

function fallbackCopy(value: string): void {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.insetInlineStart = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

function isActiveJob(status: string): boolean {
  return ["queued", "running"].includes(status);
}

function defaultParams(schema?: { properties?: Record<string, JsonSchemaProperty> }): DataRow {
  const output: DataRow = {};
  for (const [key, spec] of Object.entries(schema?.properties ?? {})) {
    if (spec.default !== undefined) output[key] = spec.default;
    else if (spec.enum?.length) output[key] = spec.enum[0];
    else if (spec.type === "boolean") output[key] = false;
    else output[key] = "";
  }
  output.timeout_seconds = 1800;
  output.output_path = "";
  return output;
}

function coercedParams(params: DataRow, schema?: { properties?: Record<string, JsonSchemaProperty> }): DataRow {
  const output: DataRow = {};
  for (const [key, value] of Object.entries(params)) {
    const spec = schema?.properties?.[key];
    if (value === "" || value === undefined) continue;
    if (spec?.type === "number" || spec?.type === "integer") output[key] = Number(value);
    else if (spec?.type === "boolean") output[key] = value === true || value === "true";
    else output[key] = value;
  }
  return output;
}

function toggle(items: string[], item: string): string[] {
  return items.includes(item) ? items.filter((current) => current !== item) : [...items, item];
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function chartGrid(overrides: DataRow = {}): DataRow {
  return { left: 104, right: 74, top: 66, bottom: 108, containLabel: true, ...overrides };
}

function strategyAxisTooltip(): DataRow {
  return {
    trigger: "axis",
    confine: true,
    backgroundColor: cssVar("--surface"),
    borderColor: cssVar("--line"),
    textStyle: { color: cssVar("--text") },
  };
}

function chartBase(): EChartsOption {
  return {
    backgroundColor: "transparent",
    animationDuration: 220,
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: cssVar("--surface"),
      borderColor: cssVar("--line"),
      textStyle: { color: cssVar("--text") },
    },
    legend: {
      type: "scroll",
      top: 8,
      right: 16,
      textStyle: { color: cssVar("--muted") },
    },
    grid: chartGrid(),
  };
}

function lineOption(rows: DataRow[], xKey: string, yKey: string, groupKey: string, title: string): EChartsOption {
  if (!rows.length) return emptyChartOption("No rows match the current filters.");
  const groups = unique(rows.map((row) => row[groupKey] || "value"));
  const categories = sortedCategoryValues(rows.map((row) => row[xKey]));
  return {
    ...chartBase(),
    xAxis: categoryAxis(titleCase(xKey), categories),
    yAxis: valueAxis(titleCase(title || yKey)),
    series: groups.map((group, index) => ({
      name: group,
      type: "line",
      smooth: true,
      showSymbol: rows.length < 160,
      symbolSize: 5,
      lineStyle: { width: 2, color: colorForIndex(index) },
      itemStyle: { color: colorForIndex(index) },
      data: sortRowsForAxis(rows.filter((row) => asText(row[groupKey] || "value") === group), xKey, categories)
        .map((row) => [asText(row[xKey]), asNumber(row[yKey])]),
    })),
  } as EChartsOption;
}

function explorerLineOption(rows: DataRow[], xKey: string, yKey: string, groupKey: string, title: string): EChartsOption {
  if (!rows.length) return emptyChartOption("No rows match the current filters.");
  const numericX = rows.some((row) => asNumber(row[xKey]) !== null) && rows.every((row) => asNumber(row[xKey]) !== null || row[xKey] === null || row[xKey] === undefined);
  const groups = unique(rows.map((row) => row[groupKey] || "value"));
  const categories = numericX ? [] : sortedCategoryValues(rows.map((row) => row[xKey]));
  return {
    ...chartBase(),
    xAxis: numericX ? valueAxis(titleCase(xKey)) : categoryAxis(titleCase(xKey), categories),
    yAxis: valueAxis(titleCase(title || yKey)),
    series: groups.map((group, index) => ({
      name: group,
      type: "line",
      smooth: true,
      showSymbol: rows.length < 160,
      symbolSize: 5,
      lineStyle: { width: 2, color: colorForIndex(index) },
      itemStyle: { color: colorForIndex(index) },
      data: sortRowsForAxis(rows.filter((row) => asText(row[groupKey] || "value") === group), xKey, categories)
        .map((row) => [numericX ? asNumber(row[xKey]) : asText(row[xKey]), asNumber(row[yKey])]),
    })),
  } as EChartsOption;
}

function barOption(rows: DataRow[], xKey: string, yKey: string, title: string): EChartsOption {
  if (!rows.length) return emptyChartOption("No rows available.");
  const labels = rows.map((row) => cleanLabel(row[xKey]));
  const horizontal = labels.length > 8 || labels.some((label) => label.length > 16);
  return {
    ...chartBase(),
    grid: horizontal ? chartGrid({ left: 190, right: 74, top: 44, bottom: 82 }) : chartGrid({ top: 56, bottom: 128 }),
    xAxis: horizontal ? valueAxis(title) : categoryAxis(titleCase(xKey), labels),
    yAxis: horizontal ? { type: "category", data: labels, axisLabel: { color: cssVar("--muted") } } : valueAxis(title),
    series: [{
      name: title,
      type: "bar",
      barMaxWidth: 28,
      itemStyle: { color: cssVar("--accent") },
      data: horizontal ? rows.map((row) => asNumber(row[yKey]) ?? 0) : rows.map((row) => asNumber(row[yKey]) ?? 0),
    }],
  } as EChartsOption;
}

function scatterOption(rows: DataRow[], xKey: string, yKey: string, groupKey: string, title: string): EChartsOption {
  const points = rows.filter((row) => asNumber(row[xKey]) !== null && asNumber(row[yKey]) !== null);
  if (!points.length) return emptyChartOption("No numeric points match the current filters.");
  const groups = unique(points.map((row) => row[groupKey] || "value"));
  return {
    ...chartBase(),
    xAxis: valueAxis(titleCase(xKey)),
    yAxis: valueAxis(titleCase(yKey)),
    series: groups.map((group, index) => ({
      name: group,
      type: "scatter",
      symbolSize: 7,
      itemStyle: { color: colorForIndex(index), opacity: 0.82 },
      data: points.filter((row) => asText(row[groupKey] || "value") === group).map((row) => [asNumber(row[xKey]), asNumber(row[yKey]), asText(row[groupKey])]),
    })),
  } as EChartsOption;
}

function metricHeatmapOption(rows: DataRow[], yKey: string, xKey: string, valueKey: string): EChartsOption {
  if (!rows.length) return emptyChartOption("No metric rows available.");
  const xs = unique(rows.map((row) => row[xKey]));
  const ys = unique(rows.map((row) => row[yKey]));
  const values = rows.map((row) => asNumber(row[valueKey])).filter((value): value is number => value !== null);
  const max = Math.max(1, ...values);
  return {
    ...chartBase(),
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: cssVar("--surface"),
      borderColor: cssVar("--line"),
      textStyle: { color: cssVar("--text") },
      formatter: (params: { data?: { x?: string; y?: string; value?: unknown[] } }) => {
        const data = params.data;
        const value = Array.isArray(data?.value) ? data?.value[2] : undefined;
        return `<div class="chart-tooltip"><span>${escapeHtml(titleCase(yKey))}</span><strong>${escapeHtml(data?.y ?? "-")}</strong><span>${escapeHtml(titleCase(xKey))}</span><strong>${escapeHtml(data?.x ?? "-")}</strong><span>${escapeHtml(titleCase(valueKey))}</span><strong>${escapeHtml(formatNumber(value))}</strong></div>`;
      },
    },
    grid: chartGrid({ left: 190, right: 78, top: 62, bottom: 132 }),
    xAxis: categoryAxis(titleCase(xKey), xs),
    yAxis: { type: "category", data: ys.map(titleCase), axisLabel: { color: cssVar("--muted") } },
    visualMap: {
      min: 0,
      max,
      orient: "horizontal",
      left: "center",
      bottom: 10,
      inRange: { color: ["#e2efeb", "#83beb1", "#145f5a"] },
      textStyle: { color: cssVar("--muted") },
    },
    series: [{
      type: "heatmap",
      data: rows.map((row) => ({
        value: [xs.indexOf(asText(row[xKey])), ys.indexOf(asText(row[yKey])), asNumber(row[valueKey]) ?? 0],
        x: asText(row[xKey]),
        y: asText(row[yKey]),
      })),
      label: { show: false },
    }],
  } as EChartsOption;
}

function disagreementOption(rows: DataRow[]): EChartsOption {
  const metrics = ["source_range_f", "hrrr_minus_nws", "nbm_minus_nws", "ensemble_minus_nws", "observed_minus_nws"];
  const chartRows: DataRow[] = [];
  for (const row of rows) {
    const date = asText(row.target_date || row.snapshot_time_utc).slice(0, 10);
    for (const metric of metrics) {
      const value = asNumber(row[metric]);
      if (date && value !== null) chartRows.push({ date, metric, value });
    }
  }
  return metricHeatmapOption(chartRows, "metric", "date", "value");
}

function strategyPnlOption(rows: DataRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No daily PnL rows available.");
  return {
    ...chartBase(),
    xAxis: categoryAxis("Date", rows.map((row) => row.target_date)),
    yAxis: valueAxis("PnL"),
    series: [
      {
        name: "Daily PnL",
        type: "bar",
        itemStyle: { color: cssVar("--accent-2") },
        data: rows.map((row) => [asText(row.target_date), asNumber(row.pnl)]),
      },
      {
        name: "Cumulative PnL",
        type: "line",
        smooth: true,
        lineStyle: { color: cssVar("--accent"), width: 3 },
        itemStyle: { color: cssVar("--accent") },
        data: rows.map((row) => [asText(row.target_date), asNumber(row.cumulative_pnl)]),
      },
    ],
  } as EChartsOption;
}

function strategyGateAxisLabel(key: string): string {
  if (key === "min_predicted_reward") return "Minimum predicted reward";
  if (key === "min_trade_probability") return "Minimum trade probability";
  if (key === "min_raw_edge") return "Minimum raw edge";
  if (key === "edge_threshold") return "Edge threshold";
  return titleCase(key);
}

function thresholdSweepOption(rows: DataRow[], emptyMessage: string): EChartsOption {
  if (!rows.length) return emptyChartOption(emptyMessage);
  const xKey = rows.some((row) => asNumber(row.min_predicted_reward) !== null)
    ? "min_predicted_reward"
    : rows.some((row) => asNumber(row.min_raw_edge) !== null)
      ? "min_raw_edge"
      : rows.some((row) => asNumber(row.edge_threshold) !== null)
        ? "edge_threshold"
        : "roi";
  const yKey = rows.some((row) => asNumber(row.min_trade_probability) !== null) ? "min_trade_probability" : "roi";
  const points = rows
    .map((row) => [
      asNumber(row[xKey]),
      asNumber(row[yKey]),
      asNumber(row.trades) ?? asNumber(row.candidates) ?? asNumber(row.count) ?? 0,
      asNumber(row.roi),
      asNumber(row.pnl),
      asNumber(row.hit_rate),
    ])
    .filter((point) => point[0] !== null && point[1] !== null && point[3] !== null);
  if (!points.length) return emptyChartOption(emptyMessage);
  const roiValues = points.map((point) => asNumber(point[3])).filter((value): value is number => value !== null);
  const maxAbsRoi = Math.max(0.01, ...roiValues.map((value) => Math.abs(value)));
  return {
    ...chartBase(),
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: cssVar("--surface"),
      borderColor: cssVar("--line"),
      textStyle: { color: cssVar("--text") },
      formatter: (params: { marker?: string; value?: unknown[] }) => {
        const value = Array.isArray(params.value) ? params.value : [];
        return [
          `<strong>${params.marker ?? ""}Gate</strong>`,
          `${strategyGateAxisLabel(xKey)}: ${formatNumber(value[0])}`,
          yKey !== "roi" ? `${strategyGateAxisLabel(yKey)}: ${formatPercent(value[1])}` : null,
          `ROI: ${formatPercent(value[3])}`,
          `PnL: ${formatCurrency(value[4])}`,
          `Rows: ${formatNumber(value[2])}`,
          asNumber(value[5]) !== null ? `Hit rate: ${formatPercent(value[5])}` : null,
        ].filter(Boolean).join("<br />");
      },
    },
    grid: { left: 82, right: 84, top: 58, bottom: 98, containLabel: true },
    xAxis: valueAxis(strategyGateAxisLabel(xKey)),
    yAxis: valueAxis(yKey === "roi" ? "ROI" : strategyGateAxisLabel(yKey)),
    visualMap: {
      min: roiValues.some((value) => value < 0) ? -maxAbsRoi : 0,
      max: maxAbsRoi,
      dimension: 3,
      orient: "horizontal",
      left: "center",
      bottom: 12,
      text: ["Higher ROI", "Lower ROI"],
      inRange: { color: ["#bd5f48", "#2e3f48", "#2f8f83"] },
      textStyle: { color: cssVar("--muted") },
    },
    series: [
      {
        name: "Gate",
        type: "scatter",
        symbolSize: (value: unknown[]) => Math.max(6, Math.min(26, Math.sqrt(Number(value[2]) || 0) * 2.2)),
        itemStyle: { opacity: 0.84 },
        data: points,
      },
    ],
  } as EChartsOption;
}

function edgeThresholdEffectOption(rows: DataRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No edge threshold rows are available for the current filters.");
  return {
    ...chartBase(),
    tooltip: strategyAxisTooltip(),
    grid: chartGrid({ right: 92, bottom: 116 }),
    xAxis: valueAxis("Minimum edge"),
    yAxis: [rateAxis("Success / ROI"), moneyAxis("PnL")],
    series: [
      {
        name: "Hit rate",
        type: "line",
        smooth: true,
        yAxisIndex: 0,
        lineStyle: { color: cssVar("--accent"), width: 3 },
        itemStyle: { color: cssVar("--accent") },
        data: rows.map((row) => [asNumber(row.min_edge), asNumber(row.hit_rate)]),
      },
      {
        name: "Contract hit rate",
        type: "line",
        smooth: true,
        yAxisIndex: 0,
        lineStyle: { color: colorForIndex(1), width: 2 },
        itemStyle: { color: colorForIndex(1) },
        data: rows.map((row) => [asNumber(row.min_edge), asNumber(row.contract_hit_rate)]),
      },
      {
        name: "ROI",
        type: "line",
        smooth: true,
        yAxisIndex: 0,
        lineStyle: { color: colorForIndex(4), width: 2, type: "dashed" },
        itemStyle: { color: colorForIndex(4) },
        data: rows.map((row) => [asNumber(row.min_edge), asNumber(row.roi)]),
      },
      {
        name: "PnL",
        type: "bar",
        yAxisIndex: 1,
        barMaxWidth: 18,
        itemStyle: { color: cssVar("--accent-2"), opacity: 0.72 },
        data: rows.map((row) => [asNumber(row.min_edge), asNumber(row.total_pnl)]),
      },
    ],
  } as EChartsOption;
}

function strategyBucketPerformanceOption(rows: DataRow[], xLabel: string): EChartsOption {
  if (!rows.length) return emptyChartOption(`No ${xLabel.toLowerCase()} rows match the current filters.`);
  const labels = rows.map((row) => asText(row.group));
  return {
    ...chartBase(),
    tooltip: strategyAxisTooltip(),
    grid: chartGrid({ bottom: 126, right: 92 }),
    xAxis: categoryAxis(xLabel, labels),
    yAxis: [moneyAxis("PnL"), rateAxis("Rate / ROI")],
    series: [
      {
        name: "PnL",
        type: "bar",
        yAxisIndex: 0,
        barMaxWidth: 24,
        itemStyle: {
          color: (params: { dataIndex?: number }) => {
            const value = asNumber(rows[params.dataIndex ?? 0]?.total_pnl) ?? 0;
            return value < 0 ? cssVar("--bad") : cssVar("--accent-2");
          },
          opacity: 0.78,
        },
        data: rows.map((row) => [asText(row.group), asNumber(row.total_pnl)]),
      },
      {
        name: "Hit rate",
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        lineStyle: { color: cssVar("--accent"), width: 3 },
        itemStyle: { color: cssVar("--accent") },
        data: rows.map((row) => [asText(row.group), asNumber(row.hit_rate)]),
      },
      {
        name: "ROI",
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        lineStyle: { color: colorForIndex(4), width: 2, type: "dashed" },
        itemStyle: { color: colorForIndex(4) },
        data: rows.map((row) => [asText(row.group), asNumber(row.roi)]),
      },
    ],
  } as EChartsOption;
}

function sideOutcomeMixOption(rows: DataRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No YES/NO outcome rows are available for the current filters.");
  const sides = sortedSideNames(unique(rows.map((row) => row.side)));
  const outcomes = ["profited", "lost", "flat"];
  return {
    ...chartBase(),
    tooltip: {
      ...strategyAxisTooltip(),
      formatter: (params: unknown) => {
        const items = Array.isArray(params) ? params as Array<{ marker?: string; seriesName?: string; value?: unknown }> : [];
        const label = asText((items[0]?.value as unknown[])?.[0] ?? "");
        return [
          `<strong>${label.toUpperCase()}</strong>`,
          ...items.map((item) => `${item.marker ?? ""}${item.seriesName}: ${formatPercent((item.value as unknown[])?.[1])}`),
        ].join("<br />");
      },
    },
    grid: chartGrid({ bottom: 104 }),
    xAxis: categoryAxis("Side", sides),
    yAxis: rateAxis("Contracts"),
    series: outcomes.map((outcome) => ({
      name: titleCase(outcome),
      type: "bar",
      stack: "outcome",
      barMaxWidth: 42,
      itemStyle: { color: outcomeColor(outcome) },
      data: sides.map((side) => [
        side,
        asNumber(rows.find((row) => asText(row.side) === side && asText(row.outcome) === outcome)?.pct_contracts) ?? 0,
      ]),
    })),
  } as EChartsOption;
}

function sideProfitLossOption(rows: DataRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No side PnL rows are available for the current filters.");
  const sides = rows.map((row) => asText(row.side));
  return {
    ...chartBase(),
    tooltip: strategyAxisTooltip(),
    grid: chartGrid({ bottom: 104 }),
    xAxis: categoryAxis("Side", sides),
    yAxis: moneyAxis("PnL"),
    series: [
      {
        name: "Gross profit",
        type: "bar",
        barMaxWidth: 24,
        itemStyle: { color: cssVar("--good"), opacity: 0.82 },
        data: rows.map((row) => [asText(row.side), asNumber(row.gross_profit)]),
      },
      {
        name: "Gross loss",
        type: "bar",
        barMaxWidth: 24,
        itemStyle: { color: cssVar("--bad"), opacity: 0.82 },
        data: rows.map((row) => [asText(row.side), asNumber(row.gross_loss)]),
      },
      {
        name: "Net PnL",
        type: "line",
        smooth: true,
        lineStyle: { color: cssVar("--accent"), width: 3 },
        itemStyle: { color: cssVar("--accent") },
        data: rows.map((row) => [asText(row.side), asNumber(row.total_pnl)]),
      },
    ],
  } as EChartsOption;
}

function tradeEdgePnlOption(rows: DataRow[]): EChartsOption {
  const points = rows.filter((row) => tradeEdge(row) !== null && asNumber(row.pnl) !== null);
  if (!points.length) return emptyChartOption("No trade edge and PnL points are available for the current filters.");
  const sides = sortedSideNames(unique(points.map((row) => normalizedSide(row))));
  return {
    ...chartBase(),
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: cssVar("--surface"),
      borderColor: cssVar("--line"),
      textStyle: { color: cssVar("--text") },
      formatter: (params: { marker?: string; value?: unknown[] }) => {
        const value = Array.isArray(params.value) ? params.value : [];
        return [
          `<strong>${params.marker ?? ""}${asText(value[4]).toUpperCase()}</strong>`,
          `Edge: ${formatNumber(value[0])}`,
          `PnL: ${formatCurrency(value[1])}`,
          `Contracts: ${formatNumber(value[2])}`,
          `Price: ${formatPercent(value[3])}`,
          asText(value[5]) ? asText(value[5]) : null,
        ].filter(Boolean).join("<br />");
      },
    },
    grid: chartGrid({ right: 78, bottom: 108 }),
    xAxis: valueAxis("Edge"),
    yAxis: moneyAxis("PnL"),
    series: sides.map((side, index) => ({
      name: side.toUpperCase(),
      type: "scatter",
      symbolSize: (value: unknown[]) => Math.max(7, Math.min(24, Math.sqrt(Number(value[2]) || 1) * 5)),
      itemStyle: { color: side === "yes" ? cssVar("--accent") : side === "no" ? colorForIndex(4) : colorForIndex(index), opacity: 0.82 },
      markLine: { symbol: "none", lineStyle: { color: cssVar("--line"), type: "dashed" }, data: [{ yAxis: 0 }] },
      data: points
        .filter((row) => normalizedSide(row) === side)
        .map((row) => [
          tradeEdge(row),
          asNumber(row.pnl),
          tradeContracts(row),
          tradeEntryPrice(row),
          normalizedSide(row),
          asText(row.market_ticker),
        ]),
    })),
  } as EChartsOption;
}

function outcomeColor(outcome: string): string {
  if (outcome === "profited") return cssVar("--good");
  if (outcome === "lost") return cssVar("--bad");
  return cssVar("--muted");
}

function eventReplayOption(event: EventReplay): EChartsOption {
  const rows = event.timeline ?? [];
  const fields = [
    ["nws_anchor_high_f", "NWS"],
    ["observed_high_so_far_f", "Observed"],
    ["hrrr_projected_high_f", "HRRR"],
    ["nbm_projected_high_f", "NBM"],
    ["ensemble_raw_median_high_f", "Ensemble"],
    ["model_expected_high_f", "Model"],
  ];
  return {
    ...chartBase(),
    xAxis: categoryAxis("Snapshot", rows.map((row) => row.snapshot_time_utc)),
    yAxis: valueAxis("Temperature F"),
    series: fields
      .filter(([field]) => rows.some((row) => asNumber(row[field]) !== null))
      .map(([field, label], index) => ({
        name: label,
        type: "line",
        smooth: true,
        showSymbol: rows.length < 160,
        lineStyle: { width: field === "observed_high_so_far_f" ? 3 : 2, color: colorForIndex(index) },
        itemStyle: { color: colorForIndex(index) },
        data: rows.map((row) => [asText(row.snapshot_time_utc), asNumber(row[field])]),
      })),
  } as EChartsOption;
}

function explorerOption(rows: DataRow[], xKey: string, yKey: string, groupKey: string, mode: "line" | "bar" | "scatter" | "heatmap", yLabel?: string): EChartsOption {
  const title = yLabel || titleCase(yKey);
  if (mode === "bar") return barOption(rows, xKey, yKey, title);
  if (mode === "scatter") return scatterOption(rows, xKey, yKey, groupKey || "group", title);
  if (mode === "heatmap") return metricHeatmapOption(rows, groupKey || yKey, xKey, yKey);
  return explorerLineOption(rows, xKey, yKey, groupKey || "group", title);
}

function emptyChartOption(message: string): EChartsOption {
  return {
    ...chartBase(),
    xAxis: { show: false, type: "value" },
    yAxis: { show: false, type: "value" },
    series: [],
    graphic: {
      type: "text",
      left: "center",
      top: "middle",
      style: {
        text: message,
        fill: cssVar("--muted"),
        fontSize: 13,
        fontWeight: 800,
        textAlign: "center",
      },
    },
  } as EChartsOption;
}

function categoryAxis(name: string, values: unknown[]): DataRow {
  return {
    type: "category",
    name,
    nameLocation: "middle",
    nameGap: 56,
    nameTextStyle: { color: cssVar("--muted"), fontWeight: 700 },
    data: sortedCategoryValues(values),
    axisLabel: {
      color: cssVar("--muted"),
      hideOverlap: true,
      margin: 14,
      overflow: "truncate",
      width: 108,
      formatter: (value: string) => cleanLabel(value).length > 18 ? `${cleanLabel(value).slice(0, 17)}...` : cleanLabel(value),
    },
    axisLine: { lineStyle: { color: cssVar("--line") } },
    splitLine: { show: false },
  };
}

function sortedCategoryValues(values: unknown[]): string[] {
  const labels = unique(values);
  if (labels.every((label) => asNumber(label) !== null)) {
    return labels.sort((left, right) => (asNumber(left) ?? 0) - (asNumber(right) ?? 0));
  }
  return labels.sort((left, right) => left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" }));
}

function sortRowsForAxis(rows: DataRow[], xKey: string, categories: string[]): DataRow[] {
  if (!rows.length) return rows;
  if (rows.every((row) => asNumber(row[xKey]) !== null)) {
    return [...rows].sort((left, right) => (asNumber(left[xKey]) ?? 0) - (asNumber(right[xKey]) ?? 0));
  }
  const index = new Map(categories.map((label, position) => [label, position]));
  return [...rows].sort((left, right) => (index.get(asText(left[xKey])) ?? 0) - (index.get(asText(right[xKey])) ?? 0));
}

function valueAxis(name: string): DataRow {
  return {
    type: "value",
    name,
    nameLocation: "middle",
    nameGap: 62,
    nameTextStyle: { color: cssVar("--muted"), fontWeight: 700 },
    axisLabel: { color: cssVar("--muted"), margin: 12 },
    axisLine: { lineStyle: { color: cssVar("--line") } },
    splitLine: { lineStyle: { color: cssVar("--grid") } },
  };
}

function rateAxis(name: string): DataRow {
  const axis = valueAxis(name);
  const axisLabel = axis.axisLabel && typeof axis.axisLabel === "object" ? axis.axisLabel as DataRow : {};
  return {
    ...axis,
    axisLabel: { ...axisLabel, formatter: (value: number) => formatPercent(value) },
  };
}

function moneyAxis(name: string): DataRow {
  const axis = valueAxis(name);
  const axisLabel = axis.axisLabel && typeof axis.axisLabel === "object" ? axis.axisLabel as DataRow : {};
  return {
    ...axis,
    axisLabel: { ...axisLabel, formatter: (value: number) => formatCurrency(value) },
  };
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
