import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Archive,
  BarChart3,
  Bell,
  Bot,
  Box,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Copy,
  Database,
  FileBarChart,
  FlaskConical,
  Gauge,
  Grid2X2,
  History,
  Layers3,
  LineChart,
  Loader2,
  MapPinned,
  Menu,
  Play,
  RefreshCcw,
  Search,
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Table2,
  TerminalSquare,
  TriangleAlert,
  X,
  type LucideIcon,
} from "lucide-react";
import {
  ChartPanel,
  barOption,
  heatmapOption,
  lineOption,
  scatterOption,
} from "./components/ChartPanel";
import { ControlCenterApiClient, createControlCenterApi } from "./api/client";
import type { JsonSchemaProperty } from "./types";

type ViewKey =
  | "dashboard"
  | "exports"
  | "data"
  | "models"
  | "strategies"
  | "reports"
  | "jobs"
  | "bot"
  | "settings";

type ApiMode = "live" | "mock" | "offline" | "stale";

type Artifact = {
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
  summary?: Record<string, unknown>;
  coverage?: {
    cities?: string[];
    date_range?: { start?: string | null; end?: string | null };
    target_dates?: number;
    snapshot_hours?: number[];
    row_counts?: Record<string, number>;
  };
  metadata_health?: {
    has_manifest?: boolean;
    has_run_manifest?: boolean;
    has_schemas?: boolean;
    status?: string;
  };
};

type ArtifactTableSchema = {
  table?: string;
  row_grain?: string[];
  primary_time_column?: string | null;
  columns?: Record<string, { type?: string; role?: string }>;
};

type RegistryEntry = {
  id: string;
  kind: string;
  label?: string;
  version?: number | string;
  description?: string;
  purpose?: string;
  source?: string;
  provider?: string;
  inputs?: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  artifact_contract?: { required_files?: string[]; optional_files?: string[] };
  semantic_outputs?: Record<string, string>;
  date_columns?: Record<string, string>;
  tables?: Record<string, ExportTableSpec> | Array<string | { name: string }>;
  entrypoints?: Record<string, EntrypointSpec>;
  views?: Record<string, unknown>;
};

type ExportTableSpec = {
  source_table?: string;
  include?: boolean;
  required?: boolean;
  protected_columns?: string[];
  required_columns?: string[];
  include_columns?: string[];
  exclude_columns?: string[];
  reason?: string;
};

type EntrypointSpec = {
  label?: string;
  command?: string[];
  params_schema?: {
    type?: string;
    properties?: Record<string, JsonSchemaProperty>;
    required?: string[];
  };
  produces?: { artifact_type?: string; contract?: string };
};

type JobRecord = {
  id: string;
  kind?: string;
  registry_id?: string;
  entrypoint?: string;
  status: string;
  created_utc?: string;
  updated_utc?: string;
  params?: Record<string, unknown>;
  command?: string[];
  cwd?: string | null;
  output_path?: string | null;
  log_path?: string;
  returncode?: number | null;
  error?: string | null;
};

type ControlState = {
  dashboard: Record<string, unknown>;
  registry: Record<string, RegistryEntry[]>;
  registryRoot: string;
  exports: Artifact[];
  artifacts: Artifact[];
  reports: Artifact[];
  models: RegistryEntry[];
  strategies: RegistryEntry[];
  profiles: RegistryEntry[];
  jobs: JobRecord[];
};

const env = (import.meta as unknown as { env?: Record<string, string> }).env ?? {};
const apiBaseUrl = env.VITE_CONTROL_API_BASE_URL || "/control/api";
const configuredMockMode = (env.VITE_CONTROL_MOCK_MODE || "always") as
  | "never"
  | "on-error"
  | "always";
const api = createControlCenterApi({ baseUrl: apiBaseUrl, mockMode: configuredMockMode });

const nav: Array<{ group: string; items: Array<{ key: ViewKey; label: string; icon: LucideIcon }> }> = [
  {
    group: "Operate",
    items: [
      { key: "dashboard", label: "Dashboard", icon: Grid2X2 },
      { key: "exports", label: "Exports", icon: Database },
      { key: "data", label: "Data Explorer", icon: Search },
    ],
  },
  {
    group: "Analyze",
    items: [
      { key: "models", label: "Model Lab", icon: FlaskConical },
      { key: "strategies", label: "Strategy Lab", icon: Sparkles },
      { key: "reports", label: "Reports", icon: FileBarChart },
    ],
  },
  {
    group: "System",
    items: [
      { key: "jobs", label: "Jobs", icon: Activity },
      { key: "bot", label: "Bot Monitor", icon: Bot },
      { key: "settings", label: "Settings", icon: Settings },
    ],
  },
];

const cityPoints = [
  { id: "la", label: "Los Angeles", x: 16, y: 61 },
  { id: "den", label: "Denver", x: 36, y: 44 },
  { id: "aus", label: "Austin", x: 45, y: 72 },
  { id: "okc", label: "Oklahoma City", x: 52, y: 57 },
  { id: "mia", label: "Miami", x: 78, y: 78 },
  { id: "nyc", label: "New York", x: 84, y: 34 },
];

const fallbackState: ControlState = {
  dashboard: {},
  registry: {},
  registryRoot: "",
  exports: [],
  artifacts: [],
  reports: [],
  models: [],
  strategies: [],
  profiles: [],
  jobs: [],
};

export function App() {
  const [view, setView] = useState<ViewKey>(() => routeFromUrl());
  const [state, setState] = useState<ControlState>(fallbackState);
  const [apiMode, setApiMode] = useState<ApiMode>("stale");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastRefresh, setLastRefresh] = useState("");
  const [selectedExportId, setSelectedExportId] = useState("");
  const [selectedModelId, setSelectedModelId] = useState("");
  const [selectedStrategyId, setSelectedStrategyId] = useState("");
  const [selectedReportId, setSelectedReportId] = useState("");
  const [selectedJobId, setSelectedJobId] = useState("");
  const [mobileNav, setMobileNav] = useState(false);

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    const next = new URL(window.location.href);
    next.searchParams.set("view", view);
    if (selectedExportId) next.searchParams.set("export", selectedExportId);
    if (selectedModelId) next.searchParams.set("model", selectedModelId);
    if (selectedStrategyId) next.searchParams.set("strategy", selectedStrategyId);
    if (selectedReportId) next.searchParams.set("report", selectedReportId);
    if (selectedJobId) next.searchParams.set("job", selectedJobId);
    window.history.replaceState(null, "", next);
  }, [selectedExportId, selectedJobId, selectedModelId, selectedReportId, selectedStrategyId, view]);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const [dashboard, registry, exportsPayload, artifactsPayload, reportsPayload, modelsPayload, jobsPayload] =
        await Promise.all([
          api.dashboard(),
          api.registry(),
          api.exports(),
          api.artifacts(),
          api.reports(),
          api.models(),
          api.jobs(),
        ]);
      const entries = (registry.entries ?? {}) as Record<string, RegistryEntry[]>;
      const nextState = {
        dashboard: dashboard as unknown as Record<string, unknown>,
        registry: entries,
        registryRoot: registry.root,
        exports: exportsPayload.exports as Artifact[],
        artifacts: (artifactsPayload.artifacts as unknown as Artifact[]) ?? [],
        reports: reportsPayload.reports as Artifact[],
        models: modelsPayload.models as RegistryEntry[],
        strategies: modelsPayload.strategies as RegistryEntry[],
        profiles: (entries.export_profile ?? []) as RegistryEntry[],
        jobs: jobsPayload.jobs as JobRecord[],
      };
      setState(nextState);
      setSelectedExportId((current) => current || nextState.exports[0]?.id || "");
      setSelectedModelId((current) => current || nextState.models[0]?.id || "");
      setSelectedStrategyId((current) => current || nextState.strategies[0]?.id || "");
      setSelectedReportId((current) => current || nextState.reports[0]?.id || "");
      setSelectedJobId((current) => current || nextState.jobs[0]?.id || "");
      setApiMode(configuredMockMode === "always" ? "mock" : "live");
      setLastRefresh(new Date().toISOString());
    } catch (err) {
      setApiMode("offline");
      setError(err instanceof Error ? err.message : "Unable to reach the Control Center API");
    } finally {
      setLoading(false);
    }
  }

  async function cancel(jobId: string) {
    await api.cancelJob(jobId);
    await refresh();
  }

  const selectedExport = findById(state.exports, selectedExportId) ?? state.exports[0];
  const selectedModel = findById(state.models, selectedModelId) ?? state.models[0];
  const selectedStrategy = findById(state.strategies, selectedStrategyId) ?? state.strategies[0];
  const selectedReport = findById(state.reports, selectedReportId) ?? state.reports[0];
  const selectedJob = findById(state.jobs, selectedJobId) ?? state.jobs[0];
  const title = nav.flatMap((group) => group.items).find((item) => item.key === view)?.label ?? "Dashboard";

  return (
    <div className="kbc-app">
      <Sidebar current={view} mobileOpen={mobileNav} onChange={setView} onClose={() => setMobileNav(false)} />
      <div className="kbc-main">
        <TopBar
          apiMode={apiMode}
          loading={loading}
          onMenu={() => setMobileNav(true)}
          onRefresh={refresh}
          title={title}
        />
        {error ? <div className="kbc-alert critical"><TriangleAlert size={18} />{error}</div> : null}
        {view === "dashboard" ? (
          <Dashboard
            state={state}
            selectedExport={selectedExport}
            onNavigate={setView}
            onSelectExport={setSelectedExportId}
            onSelectJob={setSelectedJobId}
          />
        ) : null}
        {view === "exports" ? (
          <ExportsPage api={api} exports={state.exports} profiles={state.profiles} selected={selectedExport} onRefresh={refresh} onSelect={setSelectedExportId} />
        ) : null}
        {view === "data" ? (
          <DataExplorer api={api} exports={state.exports} selectedExport={selectedExport} onSelectExport={setSelectedExportId} />
        ) : null}
        {view === "models" ? (
          <ModelLab api={api} exports={state.exports} jobs={state.jobs} models={state.models} reports={state.reports} selected={selectedModel} selectedExport={selectedExport} onRefresh={refresh} onSelect={setSelectedModelId} />
        ) : null}
        {view === "strategies" ? (
          <StrategyLab api={api} exports={state.exports} jobs={state.jobs} reports={state.reports} selected={selectedStrategy} selectedExport={selectedExport} strategies={state.strategies} onRefresh={refresh} onSelect={setSelectedStrategyId} />
        ) : null}
        {view === "reports" ? (
          <ReportsPage artifacts={state.artifacts} reports={state.reports} selected={selectedReport} onSelect={setSelectedReportId} />
        ) : null}
        {view === "jobs" ? (
          <JobsPage jobs={state.jobs} selected={selectedJob} onCancel={cancel} onSelect={setSelectedJobId} />
        ) : null}
        {view === "bot" ? <BotMonitor state={state} /> : null}
        {view === "settings" ? (
          <SettingsPage apiMode={apiMode} lastRefresh={lastRefresh} registryRoot={state.registryRoot} />
        ) : null}
      </div>
    </div>
  );
}

function Sidebar({
  current,
  mobileOpen,
  onChange,
  onClose,
}: {
  current: ViewKey;
  mobileOpen: boolean;
  onChange: (view: ViewKey) => void;
  onClose: () => void;
}) {
  return (
    <>
      <aside className={`kbc-sidebar ${mobileOpen ? "open" : ""}`}>
        <div className="kbc-brand">
          <BrandMark />
          <div>
            <strong>KALSHI</strong>
            <span>Bot Control Center</span>
          </div>
        </div>
        <nav>
          {nav.map((group) => (
            <section className="kbc-nav-group" key={group.group}>
              <p>{group.group}</p>
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    className={current === item.key ? "kbc-nav active" : "kbc-nav"}
                    key={item.key}
                    onClick={() => {
                      onChange(item.key);
                      onClose();
                    }}
                    type="button"
                  >
                    <Icon size={18} />
                    <span>{item.label}</span>
                  </button>
                );
              })}
            </section>
          ))}
        </nav>
        <div className="kbc-sidebar-footer">
          <div className="kbc-ready">
            <ShieldCheck size={24} />
            <div>
              <strong>System Ready</strong>
              <span>Local control plane</span>
            </div>
          </div>
          <div className="kbc-user">
            <span>AK</span>
            <div>
              <strong>ops_admin</strong>
              <small>Administrator</small>
            </div>
            <ChevronDown size={16} />
          </div>
          <small>v0.2.0</small>
        </div>
      </aside>
      {mobileOpen ? <button className="kbc-backdrop" aria-label="Close navigation" onClick={onClose} type="button" /> : null}
    </>
  );
}

function BrandMark() {
  return (
    <div className="kbc-mark" aria-hidden="true">
      <i className="mark-cloud" />
      <i className="mark-arrow" />
      <i className="mark-candle one" />
      <i className="mark-candle two" />
      <i className="mark-node a" />
      <i className="mark-node b" />
      <i className="mark-node c" />
    </div>
  );
}

function TopBar({
  apiMode,
  loading,
  onMenu,
  onRefresh,
  title,
}: {
  apiMode: ApiMode;
  loading: boolean;
  onMenu: () => void;
  onRefresh: () => void;
  title: string;
}) {
  return (
    <header className="kbc-topbar">
      <button className="icon-button mobile-only" onClick={onMenu} type="button" aria-label="Open navigation">
        <Menu size={20} />
      </button>
      <div className="top-title">
        <h1>{title}</h1>
        <p>Design, operate, and analyze weather-market intelligence.</p>
      </div>
      <div className="top-actions">
        <StatusPill status={apiMode === "live" ? "healthy" : apiMode} label={apiMode === "live" ? "API Online" : labelize(apiMode)} />
        <span className="top-clock"><Clock3 size={16} /> {new Date().toISOString().slice(0, 19)} UTC</span>
        <button className="secondary" disabled={loading} onClick={onRefresh} type="button">
          {loading ? <Loader2 className="spin" size={16} /> : <RefreshCcw size={16} />}
          Refresh
        </button>
        <button className="icon-button" type="button" aria-label="Notifications"><Bell size={18} /><em>3</em></button>
        <button className="env-select" type="button">Environment <strong>Production</strong><ChevronDown size={16} /></button>
        <div className="avatar">AK</div>
      </div>
    </header>
  );
}

function Dashboard({
  state,
  selectedExport,
  onNavigate,
  onSelectExport,
  onSelectJob,
}: {
  state: ControlState;
  selectedExport?: Artifact;
  onNavigate: (view: ViewKey) => void;
  onSelectExport: (id: string) => void;
  onSelectJob: (id: string) => void;
}) {
  const counts = (state.dashboard.artifact_counts ?? {}) as Record<string, number>;
  const running = state.jobs.filter((job) => ["queued", "running"].includes(job.status));
  const coverage = coverageBuckets(selectedExport);
  return (
    <PageFrame
      inspector={<DashboardInspector state={state} selectedExport={selectedExport} />}
      eyebrow="Dashboard"
      title="Operational overview"
      subtitle="Current exports, registry coverage, report freshness, and job state."
    >
      <div className="kbc-alert">
        <AlertTriangle size={18} />
        <strong>Attention:</strong> {selectedExport ? missingDataSummary(selectedExport) : "No selected export is available."}
        <button type="button" onClick={() => onNavigate("data")}>Inspect coverage</button>
      </div>
      <div className="metric-grid">
        <MetricCard icon={Database} label="Latest Export" value={selectedExport?.id ?? "None"} detail={formatBytes(estimatedBytes(selectedExport))} status="ready" />
        <MetricCard icon={Layers3} label="Open Jobs" value={String(running.length)} detail={`${state.jobs.length} total tracked`} status={running.length ? "warning" : "ready"} />
        <MetricCard icon={Box} label="Models" value={String(state.models.length)} detail={`${state.strategies.length} strategies`} status="ready" />
        <MetricCard icon={ShieldCheck} label="System Health" value="Ready" detail="Control API configured" status="ready" />
      </div>
      <div className="split-grid">
        <Panel title="Recent Exports" action={<button type="button" onClick={() => onNavigate("exports")}>View all exports</button>}>
          <DataRows
            columns={["Export ID", "Range", "Rows", "Status"]}
            rows={state.exports.slice(0, 6).map((item) => [
              <button className="link-cell" key={item.id} onClick={() => { onSelectExport(item.id); onNavigate("exports"); }} type="button">{item.id}</button>,
              dateRange(item),
              formatNumber(sumCounts(item.table_counts)),
              <StatusPill key={`${item.id}-status`} status={item.status ?? "complete"} />,
            ])}
          />
        </Panel>
        <Panel title="Open Jobs" action={<button type="button" onClick={() => onNavigate("jobs")}>View all jobs</button>}>
          <DataRows
            columns={["Job ID", "Type", "Status", "Output"]}
            rows={state.jobs.slice(0, 6).map((job) => [
              <button className="link-cell" key={job.id} onClick={() => { onSelectJob(job.id); onNavigate("jobs"); }} type="button">{job.id}</button>,
              `${job.kind ?? "-"} / ${job.entrypoint ?? "-"}`,
              <StatusPill key={`${job.id}-status`} status={job.status} />,
              job.output_path ? shortPath(job.output_path) : "-",
            ])}
          />
        </Panel>
      </div>
      <Panel title="Data Coverage" subtitle="Dataset coverage by date, including gaps and partial days.">
        <ChartPanel
          height={280}
          option={barOption(coverage.map((item) => item.label), coverage.map((item) => item.value))}
          title=""
        />
        <div className="legend-row">
          <span><i className="legend good" />Complete</span>
          <span><i className="legend partial" />Partial</span>
          <span><i className="legend gap" />Gap</span>
        </div>
      </Panel>
      <Panel title="Registry Coverage">
        <div className="registry-grid">
          <RegistryMini label="Data Sources" value={state.registry.data_source?.length ?? 0} icon={Database} />
          <RegistryMini label="Export Profiles" value={state.profiles.length} icon={Table2} />
          <RegistryMini label="Visualizations" value={state.registry.visualization?.length ?? 0} icon={BarChart3} />
          <RegistryMini label="Bot Runtimes" value={state.registry.bot_runtime?.length ?? 0} icon={Bot} />
        </div>
      </Panel>
    </PageFrame>
  );
}

function DashboardInspector({ state, selectedExport }: { state: ControlState; selectedExport?: Artifact }) {
  return (
    <Inspector title="Environment">
      <InfoRow label="Mode" value="Local exports" />
      <InfoRow label="Registry" value={state.registryRoot ? shortPath(state.registryRoot) : "builtin"} />
      <InfoRow label="Reports" value={String(state.reports.length)} />
      <SectionTitle>System Status</SectionTitle>
      {["API Gateway", "Worker Pool", "Database", "Object Storage", "Cache"].map((item) => (
        <InfoRow key={item} label={item} value="Healthy" tone="good" />
      ))}
      <SectionTitle>Largest Tables</SectionTitle>
      {Object.entries(selectedExport?.table_counts ?? {}).slice(0, 8).map(([table, count]) => (
        <MiniBar key={table} label={labelize(table)} value={count} max={largestCount(selectedExport)} />
      ))}
    </Inspector>
  );
}

function ExportsPage({
  api,
  exports,
  profiles,
  selected,
  onRefresh,
  onSelect,
}: {
  api: ControlCenterApiClient;
  exports: Artifact[];
  profiles: RegistryEntry[];
  selected?: Artifact;
  onRefresh: () => Promise<void>;
  onSelect: (id: string) => void;
}) {
  const [profileId, setProfileId] = useState(profiles[0]?.id ?? "weather_research_default");
  const [start, setStart] = useState("2026-07-01");
  const [end, setEnd] = useState("2026-07-21");
  const [selectedCities, setSelectedCities] = useState<string[]>(["nyc", "aus", "den", "la"]);
  const [selectedTables, setSelectedTables] = useState<string[]>([]);
  const [compareId, setCompareId] = useState("");
  const [operationMessage, setOperationMessage] = useState("");
  const profile = profiles.find((item) => item.id === profileId) ?? profiles[0];
  const tableSpecs = exportTables(profile);
  const activeTables = selectedTables.length ? selectedTables : tableSpecs.filter((item) => item.include !== false).map((item) => item.name);

  useEffect(() => {
    setProfileId((current) => current || profiles[0]?.id || "");
  }, [profiles]);

  async function runExport() {
    setOperationMessage("Creating export job...");
    const job = await api.createExport({ profile_id: profileId, start, end });
    setOperationMessage(`Started ${job.id}`);
    await onRefresh();
  }

  async function validate() {
    if (!selected) return;
    const result = await api.validateExport({ export_id: selected.id });
    setOperationMessage(result.valid ? "Selected export is valid." : `Blocking: ${result.blocking.join("; ")}`);
  }

  async function compare() {
    if (!selected || !compareId) return;
    const result = await api.compareExports({ left_export_id: selected.id, right_export_id: compareId });
    setOperationMessage(`Compared exports. Table deltas: ${Object.keys(result.table_deltas).length}, new cities: ${result.city_delta.join(", ") || "none"}`);
  }

  async function reduce() {
    if (!selected) return;
    const destination = `data/${selected.id}_reduced_control`;
    await api.reduceExport({ export_id: selected.id, destination, tables: activeTables, cities: selectedCities, start, end });
    setOperationMessage(`Reduced export created at ${destination}`);
    await onRefresh();
  }

  return (
    <PageFrame
      eyebrow="Export Dataset"
      title="Create, validate, reduce, and compare research exports"
      subtitle="Registry profiles protect required columns while keeping local exports reproducible."
      inspector={
        <Inspector title="Export Inspector">
          <InfoRow label="Selected" value={selected?.id ?? "None"} />
          <InfoRow label="Rows" value={formatNumber(sumCounts(selected?.table_counts))} />
          <InfoRow label="Cities" value={(selected?.coverage?.cities ?? []).join(", ") || "-"} />
          <InfoRow label="Range" value={dateRange(selected)} />
          <SectionTitle>Metadata Health</SectionTitle>
          <HealthCheck label="Manifest" ok={Boolean(selected?.metadata_health?.has_manifest)} />
          <HealthCheck label="Run Manifest" ok={Boolean(selected?.metadata_health?.has_run_manifest)} />
          <HealthCheck label="Schemas" ok={Boolean(selected?.metadata_health?.has_schemas)} />
          <SectionTitle>Validation</SectionTitle>
          <button className="secondary wide" onClick={validate} type="button"><ShieldCheck size={16} />Validate Selected</button>
          <select value={compareId} onChange={(event) => setCompareId(event.target.value)}>
            <option value="">Compare with...</option>
            {exports.filter((item) => item.id !== selected?.id).map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
          </select>
          <button className="secondary wide" onClick={compare} type="button"><Copy size={16} />Compare Exports</button>
        </Inspector>
      }
    >
      <div className="export-layout">
        <Panel title="Existing Exports" subtitle={`Showing ${exports.length} discovered local exports.`} action={<button type="button" onClick={onRefresh}><RefreshCcw size={16} />Refresh</button>}>
          <div className="responsive-table">
            <table>
              <thead>
                <tr>
                  <th>Export Name</th>
                  <th>Date Range</th>
                  <th>Cities</th>
                  <th>Tables</th>
                  <th>Rows</th>
                  <th>Status</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {exports.map((item) => (
                  <tr className={item.id === selected?.id ? "selected" : ""} key={item.id} onClick={() => onSelect(item.id)}>
                    <td><strong>{item.id}</strong><span>{shortPath(item.path)}</span></td>
                    <td>{dateRange(item)}</td>
                    <td>{item.coverage?.cities?.length ?? 0}</td>
                    <td>{item.files?.length ?? 0}</td>
                    <td>{formatNumber(sumCounts(item.table_counts))}</td>
                    <td><StatusPill status={item.status ?? "complete"} /></td>
                    <td>{dateShort(item.created_utc ?? item.modified_utc)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Create Export" className="create-export">
          <FormField label="Profile">
            <select value={profileId} onChange={(event) => setProfileId(event.target.value)}>
              {profiles.map((item) => <option key={item.id} value={item.id}>{item.label ?? item.id}</option>)}
            </select>
          </FormField>
          <div className="date-row">
            <FormField label="Start"><input value={start} onChange={(event) => setStart(event.target.value)} /></FormField>
            <FormField label="End"><input value={end} onChange={(event) => setEnd(event.target.value)} /></FormField>
          </div>
          <div className="chip-row">
            {["7D", "30D", "90D", "Custom"].map((item) => <button className={item === "30D" ? "chip active" : "chip"} key={item} type="button">{item}</button>)}
          </div>
          <SectionTitle>Cities</SectionTitle>
          <div className="chip-row">
            {["nyc", "aus", "den", "la", "mia", "okc"].map((city) => (
              <button
                className={selectedCities.includes(city) ? "chip active" : "chip"}
                key={city}
                onClick={() => setSelectedCities(toggle(selectedCities, city))}
                type="button"
              >
                {city}<X size={12} />
              </button>
            ))}
          </div>
          <SectionTitle>Tables</SectionTitle>
          <div className="table-matrix">
            {tableSpecs.map((table) => (
              <label key={table.name}>
                <input
                  checked={activeTables.includes(table.name)}
                  disabled={table.required}
                  onChange={() => setSelectedTables(toggle(activeTables, table.name))}
                  type="checkbox"
                />
                <span>
                  <strong>{labelize(table.name)}</strong>
                  <em>{table.required ? "required" : table.include === false ? "excluded" : "optional"}</em>
                </span>
                <small>{(table.required_columns ?? []).length + (table.protected_columns ?? []).length} protected</small>
              </label>
            ))}
          </div>
          <div className="preview-grid">
            <MetricMini label="Rows Est." value={formatNumber(estimateExportRows(tableSpecs))} />
            <MetricMini label="Tables" value={`${activeTables.length}/${tableSpecs.length}`} />
            <MetricMini label="Cities" value={String(selectedCities.length)} />
            <MetricMini label="Schema" value="Valid" />
          </div>
          {operationMessage ? <p className="operation-message">{operationMessage}</p> : null}
          <div className="button-row">
            <button className="secondary" onClick={reduce} type="button"><SlidersHorizontal size={16} />Reduce Selected</button>
            <button className="primary" onClick={runExport} type="button"><Play size={16} />Start Export</button>
          </div>
        </Panel>
      </div>
    </PageFrame>
  );
}

function DataExplorer({
  api,
  exports,
  selectedExport,
  onSelectExport,
}: {
  api: ControlCenterApiClient;
  exports: Artifact[];
  selectedExport?: Artifact;
  onSelectExport: (id: string) => void;
}) {
  const [table, setTable] = useState("weather_snapshots");
  const [x, setX] = useState("snapshot_time_utc");
  const [y, setY] = useState("nws_anchor_high_f");
  const [city, setCity] = useState("nyc");
  const [aggregation, setAggregation] = useState("avg");
  const [hourBlocks, setHourBlocks] = useState("4");
  const [chartType, setChartType] = useState("heatmap");
  const [queryRows, setQueryRows] = useState<Record<string, unknown>[]>([]);
  const [queryMeta, setQueryMeta] = useState<Record<string, unknown>>({});
  const schema = selectedExport?.schemas?.[table];
  const columns = Object.keys(schema?.columns ?? {});

  useEffect(() => {
    if (columns.length) {
      setX((current) => columns.includes(current) ? current : columns[0]);
      setY((current) => columns.includes(current) ? current : columns.find((column) => schema?.columns?.[column]?.type === "number") ?? columns[0]);
    }
  }, [columns.join("|"), schema?.columns]);

  async function runQuery() {
    if (!selectedExport?.path) return;
    const result = await api.visualizationQuery({
      artifact_path: selectedExport.path,
      query: {
        table,
        x: chartType === "heatmap" ? "hour_block" : x,
        y,
        group: ["city"],
        filters: { cities: city ? [city] : undefined },
        aggregation: aggregation === "none"
          ? undefined
          : {
              op: aggregation as "count" | "sum" | "avg" | "min" | "max",
              field: y,
              as: `${aggregation}_${y}`,
            },
        hour_blocks: Number(hourBlocks),
        sample: { limit: 1500 },
        decimate_to: 900,
        density: chartType === "scatter" ? { bins: 40 } : false,
        page_size: 500,
      },
    });
    setQueryRows(result.rows as Record<string, unknown>[]);
    setQueryMeta(result.metadata as unknown as Record<string, unknown>);
  }

  const availableCities = selectedExport?.coverage?.cities ?? cityPoints.map((item) => item.id);
  const heatmapRows = makeHeatmapRows(queryRows.length ? queryRows : []);
  return (
    <PageFrame
      eyebrow="Data Explorer"
      title="Inspect exported tables, schemas, coverage, and trends"
      subtitle="Large data is queried through aggregation, sampling, decimation, pagination, and density metadata."
      inspector={
        <Inspector title="Query Inspector">
          <InfoRow label="Dataset" value={selectedExport?.id ?? "None"} />
          <InfoRow label="Table" value={table} />
          <InfoRow label="Rows" value={formatNumber(sumCounts(selectedExport?.table_counts))} />
          <InfoRow label="Filtered" value={String(queryMeta.filtered_rows ?? "-")} />
          <InfoRow label="Returned" value={String(queryMeta.returned_rows ?? "-")} />
          <InfoRow label="Sampling" value={metaApplied(queryMeta.sampling)} />
          <InfoRow label="Decimation" value={metaApplied(queryMeta.decimation)} />
          <SectionTitle>Schema</SectionTitle>
          <InfoRow label="Grain" value={(schema?.row_grain ?? []).join(", ") || "-"} />
          <InfoRow label="Time" value={schema?.primary_time_column ?? "-"} />
          <div className="schema-list">
            {columns.slice(0, 12).map((column) => <span key={column}>{column}<em>{schema?.columns?.[column]?.type}</em></span>)}
          </div>
        </Inspector>
      }
    >
      <div className="data-layout">
        <Panel title="Dataset And City Coverage">
          <div className="control-strip">
            <FormField label="Dataset">
              <select value={selectedExport?.id ?? ""} onChange={(event) => onSelectExport(event.target.value)}>
                {exports.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
              </select>
            </FormField>
            <FormField label="Table">
              <select value={table} onChange={(event) => setTable(event.target.value)}>
                {(selectedExport?.files ?? ["events", "weather_snapshots", "market_snapshots", "settlements"]).map((item) => <option key={item}>{item}</option>)}
              </select>
            </FormField>
          </div>
          <div className="coverage-map">
            <div className="map-grid" />
            {cityPoints.map((point) => {
              const available = availableCities.includes(point.id);
              return (
                <button
                  className={`city-pin ${city === point.id ? "active" : ""} ${available ? "" : "missing"}`}
                  key={point.id}
                  onClick={() => setCity(point.id)}
                  style={{ insetInlineStart: `${point.x}%`, insetBlockStart: `${point.y}%` }}
                  type="button"
                >
                  <span>{point.id.toUpperCase()}</span>
                  <strong>{available ? "OK" : "Gap"}</strong>
                </button>
              );
            })}
          </div>
        </Panel>
        <Panel title="Visualization Query Builder">
          <div className="query-builder">
            <FormField label="X Axis"><select value={x} onChange={(event) => setX(event.target.value)}>{columns.map((item) => <option key={item}>{item}</option>)}</select></FormField>
            <FormField label="Y Axis"><select value={y} onChange={(event) => setY(event.target.value)}>{columns.map((item) => <option key={item}>{item}</option>)}</select></FormField>
            <FormField label="Aggregation"><select value={aggregation} onChange={(event) => setAggregation(event.target.value)}>{["none", "count", "sum", "avg", "min", "max"].map((item) => <option key={item}>{item}</option>)}</select></FormField>
            <FormField label="Hour Blocks"><select value={hourBlocks} onChange={(event) => setHourBlocks(event.target.value)}>{["1", "2", "4", "6", "8", "12", "24"].map((item) => <option key={item} value={item}>{24 / Number(item)}h average</option>)}</select></FormField>
            <FormField label="Chart"><select value={chartType} onChange={(event) => setChartType(event.target.value)}>{["heatmap", "line", "scatter", "bar"].map((item) => <option key={item}>{item}</option>)}</select></FormField>
            <button className="primary" onClick={runQuery} type="button"><Play size={16} />Run Query</button>
          </div>
          <ChartPanel
            height={360}
            title={`${labelize(table)} / ${labelize(chartType)}`}
            subtitle={`Showing ${queryRows.length || "mock"} rows. Click chart elements to inspect details.`}
            option={
              chartType === "line"
                ? lineOption(["00", "06", "12", "18"], [{ name: city, color: "#1DD6B7", data: [82, 88, 91, 89] }])
                : chartType === "scatter"
                  ? scatterOption([[8, 1.2, "source disagreement"], [12, 2.1, "late snapshot"], [20, 0.7, "market divergence"]])
                  : chartType === "bar"
                    ? barOption(["events", "weather", "markets", "settlements"], [96, 2136, 12816, 96])
                    : heatmapOption(["00", "06", "12", "18"], ["nyc", "aus", "den", "mia"], heatmapRows)
            }
          />
          <div className="query-meta">
            <InfoRow label="Total Rows" value={String(queryMeta.total_rows ?? "query to load")} />
            <InfoRow label="Filtered Rows" value={String(queryMeta.filtered_rows ?? "-")} />
            <InfoRow label="Returned Rows" value={String(queryMeta.returned_rows ?? "-")} />
            <InfoRow label="Density" value={metaAvailable(queryMeta.density)} />
          </div>
        </Panel>
        <Panel title="Raw Table Preview" subtitle="Wide JSON/probability cells should expand into the inspector instead of being silently truncated.">
          <DataRows
            columns={["Column", "Type", "Role"]}
            rows={columns.slice(0, 12).map((column) => [
              column,
              schema?.columns?.[column]?.type ?? "unknown",
              schema?.columns?.[column]?.role ?? "attribute",
            ])}
          />
        </Panel>
      </div>
    </PageFrame>
  );
}

function ModelLab(props: {
  api: ControlCenterApiClient;
  exports: Artifact[];
  jobs: JobRecord[];
  models: RegistryEntry[];
  reports: Artifact[];
  selected?: RegistryEntry;
  selectedExport?: Artifact;
  onRefresh: () => Promise<void>;
  onSelect: (id: string) => void;
}) {
  return <ExperimentLab kind="model" {...props} />;
}

function StrategyLab(props: {
  api: ControlCenterApiClient;
  exports: Artifact[];
  jobs: JobRecord[];
  reports: Artifact[];
  selected?: RegistryEntry;
  selectedExport?: Artifact;
  strategies: RegistryEntry[];
  onRefresh: () => Promise<void>;
  onSelect: (id: string) => void;
}) {
  return (
    <ExperimentLab
      api={props.api}
      exports={props.exports}
      jobs={props.jobs}
      kind="strategy"
      models={props.strategies}
      reports={props.reports}
      selected={props.selected}
      selectedExport={props.selectedExport}
      onRefresh={props.onRefresh}
      onSelect={props.onSelect}
    />
  );
}

function ExperimentLab({
  api,
  exports,
  jobs,
  kind,
  models,
  reports,
  selected,
  selectedExport,
  onRefresh,
  onSelect,
}: {
  api: ControlCenterApiClient;
  exports: Artifact[];
  jobs: JobRecord[];
  kind: "model" | "strategy";
  models: RegistryEntry[];
  reports: Artifact[];
  selected?: RegistryEntry;
  selectedExport?: Artifact;
  onRefresh: () => Promise<void>;
  onSelect: (id: string) => void;
}) {
  const [search, setSearch] = useState("");
  const entrypoints = Object.entries(selected?.entrypoints ?? {});
  const [entrypoint, setEntrypoint] = useState(entrypoints[0]?.[0] ?? "");
  const [datasetId, setDatasetId] = useState(selectedExport?.id ?? "");
  const [modelReportPath, setModelReportPath] = useState(reports.find((item) => item.artifact_type === "model_report")?.path ?? "");
  const [params, setParams] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const activeEntrypoint = selected?.entrypoints?.[entrypoint] ?? entrypoints[0]?.[1];
  const paramSpecs = activeEntrypoint?.params_schema?.properties ?? {};
  const filtered = models.filter((item) => `${item.id} ${item.label ?? ""}`.toLowerCase().includes(search.toLowerCase()));
  const labTitle = kind === "model" ? "Model Lab" : "Strategy Lab";

  useEffect(() => {
    setEntrypoint(Object.keys(selected?.entrypoints ?? {})[0] ?? "");
    setParams({});
  }, [selected?.id]);

  async function submit() {
    const dataset = exports.find((item) => item.id === datasetId) ?? selectedExport;
    setMessage("Checking compatibility...");
    if (kind === "model" && dataset?.path && selected?.id) {
      try {
        const compatibility = await api.modelRunCompatibility({
          model_id: selected.id,
          dataset_path: dataset.path,
        });
        if (compatibility.compatible === false) {
          setMessage(`Blocked: ${compatibility.blocking?.join("; ") || "dataset is not compatible"}`);
          return;
        }
      } catch {
        setMessage("Compatibility endpoint unavailable; submitting registered job.");
      }
    }
    setMessage("Starting registered job...");
    const job = await api.createJob({
      kind,
      registry_id: selected?.id ?? "",
      entrypoint: entrypoint || Object.keys(selected?.entrypoints ?? {})[0] || "evaluate",
      params: {
        ...params,
        dataset_path: dataset?.path ?? "",
        model_report_path: modelReportPath,
        timeout_seconds: 3600,
      },
    });
    setMessage(`Started ${job.id}`);
    await onRefresh();
  }

  return (
    <PageFrame
      eyebrow={labTitle}
      title={kind === "model" ? "Design, train, and evaluate predictive models" : "Backtest, diagnose, and tune strategy gates"}
      subtitle="Registry-defined entrypoints drive forms, compatibility checks, jobs, and output artifacts."
      inspector={<LabInspector kind={kind} selected={selected} reports={reports} jobs={jobs} />}
    >
      <div className="lab-layout">
        <Panel title={kind === "model" ? "Model Registry" : "Strategy Registry"} className="registry-list-panel" action={<button type="button">+ New</button>}>
          <div className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={`Search ${kind}s...`} /></div>
          <div className="registry-list">
            {filtered.map((item) => (
              <button className={item.id === selected?.id ? "registry-row active" : "registry-row"} key={item.id} onClick={() => onSelect(item.id)} type="button">
                {kind === "model" ? <NetworkIcon /> : <Sparkles size={26} />}
                <span><strong>{item.label ?? item.id}</strong><em>v{item.version ?? "1"} / registered</em></span>
                <ChevronDown size={14} />
              </button>
            ))}
          </div>
        </Panel>
        <div className="lab-center">
          <Panel title="">
            <div className="model-hero-card">
              <div className="model-logo">{kind === "model" ? <NetworkIcon /> : <Sparkles size={42} />}</div>
              <div>
                <h2>{selected?.label ?? selected?.id ?? "Select an entry"}</h2>
                <p>{selected?.description ?? selected?.purpose ?? "Registry-driven task definition."}</p>
              </div>
              <StatusPill status="registered" label="Registered" />
            </div>
            <div className="purpose-grid">
              <InfoBlock title="Purpose">{selected?.description ?? "No description registered."}</InfoBlock>
              <InfoBlock title="Inputs">{describeInput(selected?.inputs)}</InfoBlock>
              <InfoBlock title="Outputs">{describeOutput(selected?.artifact_contract, activeEntrypoint)}</InfoBlock>
            </div>
            <div className="tab-row">
              {entrypoints.map(([key, spec]) => (
                <button className={key === entrypoint ? "tab active" : "tab"} key={key} onClick={() => setEntrypoint(key)} type="button">
                  {labelize(spec.label ?? key)}
                </button>
              ))}
            </div>
          </Panel>
          <div className="runner-grid">
            <Panel title="Dataset And Split">
              <FormField label="Dataset">
                <select value={datasetId} onChange={(event) => setDatasetId(event.target.value)}>
                  {exports.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
                </select>
              </FormField>
              {kind === "strategy" ? (
                <FormField label="Model Report">
                  <select value={modelReportPath} onChange={(event) => setModelReportPath(event.target.value)}>
                    {reports.filter((item) => item.artifact_type === "model_report").map((item) => <option key={item.id} value={item.path}>{item.id}</option>)}
                  </select>
                </FormField>
              ) : null}
              <div className="split-bar"><span style={{ width: "70%" }}>Train 70%</span><span style={{ width: "15%" }}>Validate</span><span style={{ width: "15%" }}>Test</span></div>
              <InfoRow label="Required Tables" value={requiredTables(selected).join(", ") || "-"} />
              <InfoRow label="Dataset Range" value={dateRange(exports.find((item) => item.id === datasetId) ?? selectedExport)} />
            </Panel>
            <Panel title="Parameters" action={<button type="button">Load Preset</button>}>
              <div className="param-table">
                {Object.entries(paramSpecs).map(([name, spec]) => (
                  <SchemaControl
                    key={name}
                    name={name}
                    spec={spec}
                    value={params[name] ?? String(spec.default ?? "")}
                    onChange={(value) => setParams((current) => ({ ...current, [name]: value }))}
                  />
                ))}
                {!Object.keys(paramSpecs).length ? <p className="muted">No entrypoint parameters are registered.</p> : null}
              </div>
            </Panel>
          </div>
          {message ? <p className="operation-message">{message}</p> : null}
          <button className="primary run-button" onClick={submit} type="button"><Play size={18} />Run Job</button>
          <Panel title="Job Queue">
            <JobRows jobs={jobs.filter((job) => job.kind === kind).slice(0, 6)} />
          </Panel>
        </div>
        <Panel title="Reports" className="reports-side">
          <div className="control-strip">
            <select><option>Latest Run</option></select>
            <StatusPill status="complete" label="Completed" />
          </div>
          <ChartPanel
            height={250}
            title="Calibration"
            option={lineOption(["0", "0.25", "0.5", "0.75", "1.0"], [
              { name: "p50", color: "#1DD6B7", data: [0, 0.18, 0.48, 0.71, 0.95] },
              { name: "p90", color: "#4F7DFF", data: [0, 0.22, 0.55, 0.82, 1] },
              { name: "baseline", color: "#FFB547", data: [0, 0.11, 0.33, 0.62, 0.91] },
            ])}
          />
          <ChartPanel
            height={210}
            title={kind === "strategy" ? "Daily PnL" : "Checkpoint Performance"}
            option={kind === "strategy" ? barOption(["Jul 15", "Jul 16", "Jul 17"], [14, -6, 22]) : lineOption(["0", "12", "24", "36", "48"], [{ name: "CRPS", color: "#1DD6B7", data: [1.4, 1.1, 0.8, 0.55, 0.38] }])}
          />
          <div className="metric-grid mini">
            <MetricMini label="MAE" value="1.42" delta="-12.3%" />
            <MetricMini label="RMSE" value="2.13" delta="-9.8%" />
            <MetricMini label="CRPS" value="0.387" delta="-11.1%" />
          </div>
          <button className="secondary wide" type="button">Open Full Report</button>
        </Panel>
      </div>
    </PageFrame>
  );
}

function ReportsPage({ artifacts, reports, selected, onSelect }: { artifacts: Artifact[]; reports: Artifact[]; selected?: Artifact; onSelect: (id: string) => void }) {
  const [filter, setFilter] = useState("all");
  const visible = reports.filter((report) => filter === "all" || report.artifact_type === filter);
  return (
    <PageFrame
      eyebrow="Reports"
      title="Open model, strategy, quality, and generic artifacts"
      subtitle="Known contracts get specialized renderers; unknown artifacts fall back to schema/table exploration."
      inspector={<ArtifactInspector artifact={selected} />}
    >
      <div className="reports-layout">
        <Panel title="Report Browser">
          <div className="chip-row">
            {["all", "model_report", "strategy_report", "quality_report"].map((item) => (
              <button className={filter === item ? "chip active" : "chip"} key={item} onClick={() => setFilter(item)} type="button">{labelize(item)}</button>
            ))}
          </div>
          <div className="artifact-list">
            {visible.map((report) => (
              <button className={report.id === selected?.id ? "artifact-row active" : "artifact-row"} key={report.id} onClick={() => onSelect(report.id)} type="button">
                <FileBarChart size={22} />
                <span><strong>{report.id}</strong><em>{report.artifact_type} / {report.contract ?? "legacy"}</em></span>
                <StatusPill status={report.status ?? "complete"} />
              </button>
            ))}
          </div>
        </Panel>
        <Panel title={selected?.id ?? "Select a report"} className="report-renderer">
          <ReportRenderer report={selected} />
        </Panel>
        <Panel title="All Artifacts">
          <DataRows
            columns={["Artifact", "Type", "Files", "Rows"]}
            rows={artifacts.slice(0, 10).map((item) => [
              item.id,
              item.artifact_type ?? "unknown",
              String(item.files?.length ?? 0),
              formatNumber(sumCounts(item.table_counts)),
            ])}
          />
        </Panel>
      </div>
    </PageFrame>
  );
}

function JobsPage({
  jobs,
  selected,
  onCancel,
  onSelect,
}: {
  jobs: JobRecord[];
  selected?: JobRecord;
  onCancel: (id: string) => Promise<void>;
  onSelect: (id: string) => void;
}) {
  const [status, setStatus] = useState("all");
  const visible = jobs.filter((job) => status === "all" || job.status === status);
  return (
    <PageFrame
      eyebrow="Jobs"
      title="Execution history, logs, cancellation, and artifacts"
      subtitle="Every long-running export, model, and strategy action is tracked here."
      inspector={
        <Inspector title="Job Detail">
          <InfoRow label="Job" value={selected?.id ?? "-"} />
          <InfoRow label="Kind" value={selected?.kind ?? "-"} />
          <InfoRow label="Entry" value={selected?.entrypoint ?? "-"} />
          <InfoRow label="Status" value={selected?.status ?? "-"} />
          <InfoRow label="Output" value={shortPath(selected?.output_path)} />
          <InfoRow label="Return" value={String(selected?.returncode ?? "-")} />
          {selected?.error ? <div className="kbc-alert critical"><TriangleAlert size={16} />{selected.error}</div> : null}
          <SectionTitle>Command</SectionTitle>
          <pre className="command-box">{selected?.command?.join(" ") || "No command selected."}</pre>
          {selected && ["queued", "running"].includes(selected.status) ? (
            <button className="secondary wide" onClick={() => onCancel(selected.id)} type="button"><Archive size={16} />Cancel Job</button>
          ) : null}
        </Inspector>
      }
    >
      <Panel title="Job Queue" action={<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All Statuses</option>{["queued", "running", "succeeded", "failed", "cancelled"].map((item) => <option key={item}>{item}</option>)}</select>}>
        <div className="responsive-table">
          <table>
            <thead><tr><th>Job ID</th><th>Kind</th><th>Registry</th><th>Entrypoint</th><th>Status</th><th>Started</th><th>Output</th></tr></thead>
            <tbody>
              {visible.map((job) => (
                <tr className={job.id === selected?.id ? "selected" : ""} key={job.id} onClick={() => onSelect(job.id)}>
                  <td><strong>{job.id}</strong><span>{shortPath(job.log_path)}</span></td>
                  <td>{job.kind}</td>
                  <td>{job.registry_id}</td>
                  <td>{job.entrypoint}</td>
                  <td><StatusPill status={job.status} /></td>
                  <td>{dateShort(job.created_utc)}</td>
                  <td>{shortPath(job.output_path)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Log Tail" subtitle="Line-limited logs avoid rendering lag. Live polling should pause when the tab is hidden.">
        <pre className="log-box">
          {selected ? `$ ${selected.command?.join(" ") ?? "command unavailable"}\n\nStatus: ${selected.status}\nOutput: ${selected.output_path ?? "-"}\nError: ${selected.error ?? "none"}` : "Select a job to inspect logs."}
        </pre>
      </Panel>
    </PageFrame>
  );
}

function BotMonitor({ state }: { state: ControlState }) {
  const runtime = state.registry.bot_runtime?.[0];
  return (
    <PageFrame
      eyebrow="Bot Monitor"
      title="Deployed weather bot telemetry"
      subtitle="The backend exposes a deferred runtime contract; live telemetry remains intentionally disabled."
      inspector={<Inspector title="Runtime Contract"><InfoRow label="Runtime" value={runtime?.label ?? "Not configured"} /><InfoRow label="Provider" value={runtime?.provider ?? "-"} /><InfoRow label="Status" value="Deferred" /></Inspector>}
    >
      <div className="deferred-grid">
        {["Runtime Health", "Positions", "Orders", "Decisions", "Risk Exposure", "Latency", "Collector Health"].map((item) => (
          <Panel key={item} title={item}>
            <div className="deferred-card"><Bot size={32} /><strong>Not configured</strong><span>Reserved for the deployed bot monitoring phase.</span></div>
          </Panel>
        ))}
      </div>
    </PageFrame>
  );
}

function SettingsPage({ apiMode, lastRefresh, registryRoot }: { apiMode: ApiMode; lastRefresh: string; registryRoot: string }) {
  return (
    <PageFrame eyebrow="Settings" title="Control plane configuration" subtitle="Local UI preferences and backend connection diagnostics.">
      <div className="settings-grid">
        <Panel title="API">
          <InfoRow label="Base URL" value={apiBaseUrl} />
          <InfoRow label="Mock Mode" value={configuredMockMode} />
          <InfoRow label="Current State" value={apiMode} />
          <InfoRow label="Last Refresh" value={lastRefresh || "-"} />
        </Panel>
        <Panel title="Registry">
          <InfoRow label="Active Root" value={registryRoot || "builtin"} />
          <InfoRow label="Overlay Root" value="Not configured" />
          <InfoRow label="Data Root" value="next-gen/data" />
          <InfoRow label="Reports Root" value="next-gen/reports" />
        </Panel>
        <Panel title="Visualization Defaults">
          <InfoRow label="Table Page Size" value="500" />
          <InfoRow label="Decimation Target" value="900" />
          <InfoRow label="Density Bins" value="40" />
          <InfoRow label="Reduced Motion" value="System" />
        </Panel>
      </div>
    </PageFrame>
  );
}

function PageFrame({ children, eyebrow, inspector, subtitle, title }: { children: ReactNode; eyebrow: string; inspector?: ReactNode; subtitle: string; title: string }) {
  return (
    <main className={inspector ? "kbc-page with-inspector" : "kbc-page"}>
      <section className="kbc-content">
        <div className="page-heading">
          <p>{eyebrow}</p>
          <h2>{title}</h2>
          <span>{subtitle}</span>
        </div>
        {children}
      </section>
      {inspector ? <aside className="kbc-inspector">{inspector}</aside> : null}
    </main>
  );
}

function Panel({ action, children, className = "", subtitle, title }: { action?: ReactNode; children: ReactNode; className?: string; subtitle?: string; title: string }) {
  return (
    <section className={`kbc-panel ${className}`}>
      {title || subtitle || action ? (
        <header className="panel-header">
          <div>
            {title ? <h3>{title}</h3> : null}
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          {action}
        </header>
      ) : null}
      {children}
    </section>
  );
}

function Inspector({ children, title }: { children: ReactNode; title: string }) {
  return (
    <div className="inspector-panel">
      <header><h3>{title}</h3><button type="button" aria-label="Close inspector"><X size={16} /></button></header>
      {children}
    </div>
  );
}

function ArtifactInspector({ artifact }: { artifact?: Artifact }) {
  return (
    <Inspector title="Artifact Metadata">
      <InfoRow label="ID" value={artifact?.id ?? "-"} />
      <InfoRow label="Type" value={artifact?.artifact_type ?? "-"} />
      <InfoRow label="Contract" value={artifact?.contract ?? "legacy"} />
      <InfoRow label="Source Export" value={artifact?.source_export_id ?? "-"} />
      <InfoRow label="Files" value={String(artifact?.files?.length ?? 0)} />
      <InfoRow label="Rows" value={formatNumber(sumCounts(artifact?.table_counts))} />
      <SectionTitle>Tables</SectionTitle>
      {(artifact?.files ?? []).slice(0, 12).map((file) => <MiniBar key={file} label={labelize(file)} value={artifact?.table_counts?.[file] ?? 0} max={largestCount(artifact)} />)}
    </Inspector>
  );
}

function LabInspector({ jobs, kind, reports, selected }: { jobs: JobRecord[]; kind: "model" | "strategy"; reports: Artifact[]; selected?: RegistryEntry }) {
  return (
    <Inspector title="Diagnostics">
      <InfoRow label="Selected" value={selected?.id ?? "-"} />
      <InfoRow label="Version" value={String(selected?.version ?? "-")} />
      <InfoRow label="Entrypoints" value={String(Object.keys(selected?.entrypoints ?? {}).length)} />
      <InfoRow label="Open Jobs" value={String(jobs.filter((job) => job.kind === kind && ["queued", "running"].includes(job.status)).length)} />
      <SectionTitle>Artifact Contract</SectionTitle>
      {(selected?.artifact_contract?.required_files ?? []).map((file) => <HealthCheck key={file} label={file} ok />)}
      <SectionTitle>Recent Reports</SectionTitle>
      {reports.filter((item) => item.artifact_type === `${kind}_report`).slice(0, 5).map((report) => <InfoRow key={report.id} label={report.id} value={dateShort(report.modified_utc)} />)}
    </Inspector>
  );
}

function MetricCard({ detail, icon: Icon, label, status, value }: { detail: string; icon: LucideIcon; label: string; status: "ready" | "warning"; value: string }) {
  return (
    <section className={`kbc-metric ${status}`}>
      <Icon size={32} />
      <span>{label}</span>
      <strong>{value}</strong>
      <em>{detail}</em>
    </section>
  );
}

function RegistryMini({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: number }) {
  return <div className="registry-mini"><Icon size={22} /><span>{label}</span><strong>{value}</strong></div>;
}

function MetricMini({ delta, label, value }: { delta?: string; label: string; value: string }) {
  return <div className="metric-mini"><span>{label}</span><strong>{value}</strong>{delta ? <em>{delta}</em> : null}</div>;
}

function InfoBlock({ children, title }: { children: ReactNode; title: string }) {
  return <div className="info-block"><strong>{title}</strong><p>{children}</p></div>;
}

function FormField({ children, label }: { children: ReactNode; label: string }) {
  return <label className="form-field"><span>{label}</span>{children}</label>;
}

function SchemaControl({ name, onChange, spec, value }: { name: string; onChange: (value: string) => void; spec: JsonSchemaProperty; value: string }) {
  return (
    <label className="schema-row">
      <span>{labelize(name)}<em>{spec.type ?? "value"}</em></span>
      {spec.enum?.length ? (
        <select value={value} onChange={(event) => onChange(event.target.value)}>
          {spec.enum.map((item) => <option key={String(item)}>{String(item)}</option>)}
        </select>
      ) : (
        <input value={value} onChange={(event) => onChange(event.target.value)} />
      )}
      <small title={spec.description}>i</small>
    </label>
  );
}

function DataRows({ columns, rows }: { columns: string[]; rows: ReactNode[][] }) {
  return (
    <div className="data-rows">
      <div className="data-row head">{columns.map((column) => <strong key={column}>{column}</strong>)}</div>
      {rows.map((row, index) => <div className="data-row" key={index}>{row.map((cell, cellIndex) => <span key={cellIndex}>{cell}</span>)}</div>)}
      {!rows.length ? <p className="muted">No rows available.</p> : null}
    </div>
  );
}

function JobRows({ jobs }: { jobs: JobRecord[] }) {
  return <DataRows columns={["Job ID", "Task", "Dataset", "Status", "Output"]} rows={jobs.map((job) => [job.id, `${job.registry_id} / ${job.entrypoint}`, shortPath(String(job.params?.dataset_path ?? "")), <StatusPill key={job.id} status={job.status} />, shortPath(job.output_path)])} />;
}

function StatusPill({ label, status }: { label?: string; status: string }) {
  const normalized = status.toLowerCase();
  const Icon = normalized.includes("fail") || normalized.includes("offline") ? TriangleAlert : normalized.includes("run") || normalized.includes("queue") ? Loader2 : CheckCircle2;
  return <span className={`status-pill ${normalized}`}><Icon className={Icon === Loader2 ? "spin" : ""} size={14} />{label ?? labelize(status)}</span>;
}

function InfoRow({ label, tone, value }: { label: string; tone?: "good" | "warning"; value: string }) {
  return <div className={`info-row ${tone ?? ""}`}><span>{label}</span><strong>{value}</strong></div>;
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h4 className="section-title">{children}</h4>;
}

function HealthCheck({ label, ok }: { label: string; ok: boolean }) {
  return <div className={ok ? "health-row ok" : "health-row warn"}>{ok ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}<span>{label}</span><strong>{ok ? "Ready" : "Missing"}</strong></div>;
}

function MiniBar({ label, max, value }: { label: string; max: number; value: number }) {
  return <div className="mini-bar"><span>{label}</span><strong>{formatNumber(value)}</strong><i style={{ inlineSize: `${Math.max(4, Math.min(100, max ? (value / max) * 100 : 0))}%` }} /></div>;
}

function ReportRenderer({ report }: { report?: Artifact }) {
  if (!report) return <div className="empty-state"><FileBarChart size={40} /><strong>Select a report</strong><span>Model, strategy, quality, and generic artifacts render here.</span></div>;
  const type = report.artifact_type ?? "";
  if (type === "strategy_report") {
    return (
      <div className="report-grid">
        <MetricMini label="Total PnL" value={money(Number(report.summary?.total_pnl ?? 0))} />
        <MetricMini label="ROI" value={`${Number(report.summary?.roi ?? 0).toFixed(2)}%`} />
        <MetricMini label="Trades" value={String(report.summary?.trades ?? report.table_counts?.trades ?? 0)} />
        <ChartPanel height={280} title="Strategy Equity" option={lineOption(["1", "2", "3", "4"], [{ name: "cumulative_pnl", color: "#1DD6B7", data: [0, 12, 8, 22] }])} />
        <ChartPanel height={280} title="Gate Sweep" option={heatmapOption(["0.01", "0.03", "0.05", "0.08"], ["train", "test"], [[4, 7, 9, 5], [2, 8, 6, 3]])} />
      </div>
    );
  }
  if (type === "quality_report") {
    return (
      <div className="report-grid">
        <MetricMini label="Tables" value={String(report.files?.length ?? 0)} />
        <MetricMini label="Rows" value={formatNumber(sumCounts(report.table_counts))} />
        <MetricMini label="Warnings" value={String((report.summary?.warnings as unknown[])?.length ?? 0)} />
        <ChartPanel height={280} title="Missing City-Hours" option={heatmapOption(["00", "06", "12", "18"], ["nyc", "aus", "den", "mia"], [[1, 0, 0, 1], [0, 0, 1, 0], [0, 0, 0, 0], [1, 1, 0, 0]])} />
      </div>
    );
  }
  return (
    <div className="report-grid">
      <MetricMini label="Files" value={String(report.files?.length ?? 0)} />
      <MetricMini label="Rows" value={formatNumber(sumCounts(report.table_counts))} />
      <MetricMini label="Contract" value={report.contract ?? "legacy"} />
      <ChartPanel height={300} title="Model Performance" option={lineOption(["0", "12", "24", "36", "48"], [{ name: "CRPS", color: "#1DD6B7", data: [1.4, 1.0, 0.75, 0.52, 0.38] }, { name: "baseline", color: "#FFB547", data: [1.5, 1.2, 0.98, 0.77, 0.62] }])} />
    </div>
  );
}

function NetworkIcon() {
  return <span className="network-icon"><i /><i /><i /><i /><b /><b /><b /></span>;
}

function findById<T extends { id: string }>(items: T[], id: string): T | undefined {
  return items.find((item) => item.id === id);
}

function routeFromUrl(): ViewKey {
  const value = new URL(window.location.href).searchParams.get("view") as ViewKey | null;
  return value && ["dashboard", "exports", "data", "models", "strategies", "reports", "jobs", "bot", "settings"].includes(value) ? value : "dashboard";
}

function exportTables(profile?: RegistryEntry): Array<ExportTableSpec & { name: string }> {
  const tables = profile?.tables;
  if (!tables) return ["events", "weather_snapshots", "market_snapshots", "settlements", "final_temperature_labels", "raw_payloads", "provider_errors"].map((name) => ({ name, required: ["events", "weather_snapshots", "market_snapshots"].includes(name) }));
  if (Array.isArray(tables)) return tables.map((item) => typeof item === "string" ? { name: item } : item);
  return Object.entries(tables).map(([name, spec]) => ({ name, ...spec }));
}

function requiredTables(entry?: RegistryEntry): string[] {
  const dataset = entry?.inputs?.dataset as { required_tables?: string[] } | undefined;
  return dataset?.required_tables ?? [];
}

function coverageBuckets(exportItem?: Artifact) {
  const range = exportItem?.coverage?.date_range;
  const start = range?.start ? new Date(range.start) : new Date("2026-07-07");
  return Array.from({ length: 14 }, (_, index) => {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + index);
    return {
      label: date.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }),
      value: index === 10 ? 75 : index === 11 ? 48 : 100,
    };
  });
}

function makeHeatmapRows(rows: Record<string, unknown>[]): number[][] {
  if (!rows.length) return [[92, 84, 79, 86], [88, 82, 74, 90], [72, 76, 80, 78], [95, 91, 87, 94]];
  return [[88, 82, 74, 90], [92, 84, 79, 86], [72, 76, 80, 78], [95, 91, 87, 94]];
}

function missingDataSummary(exportItem: Artifact) {
  const files = new Set(exportItem.files ?? []);
  const missing = ["events", "weather_snapshots", "market_snapshots"].filter((table) => !files.has(table));
  if (missing.length) return `Missing required table(s): ${missing.join(", ")}.`;
  const pendingSettlements = Math.max(0, Number(exportItem.coverage?.target_dates ?? 0) * Number(exportItem.coverage?.cities?.length ?? 0) - Number(exportItem.table_counts?.settlements ?? 0));
  return pendingSettlements > 0 ? `${pendingSettlements} possible settlement/final-label gaps need review.` : "Latest export has required coverage.";
}

function largestCount(artifact?: Artifact) {
  return Math.max(1, ...Object.values(artifact?.table_counts ?? {}));
}

function estimateExportRows(tables: Array<ExportTableSpec & { name: string }>) {
  return tables.filter((table) => table.include !== false).reduce((sum, table) => sum + (table.name.includes("raw") ? 18000 : table.required ? 6400 : 1200), 0);
}

function estimatedBytes(artifact?: Artifact) {
  return sumCounts(artifact?.table_counts) * 180;
}

function dateRange(item?: Artifact) {
  const range = item?.coverage?.date_range;
  return range?.start && range.end ? `${range.start} to ${range.end}` : "-";
}

function dateShort(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.slice(0, 16) : date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function labelize(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (match) => match.toUpperCase());
}

function shortPath(value?: string | null) {
  if (!value) return "-";
  const parts = value.split(/[\\/]/);
  return parts.slice(-2).join("/");
}

function sumCounts(counts?: Record<string, number>) {
  return Object.values(counts ?? {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

function formatNumber(value?: number) {
  return new Intl.NumberFormat("en-US").format(Number(value ?? 0));
}

function formatBytes(value: number) {
  if (!value) return "0 MB";
  if (value > 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)} GB`;
  return `${(value / 1_000_000).toFixed(1)} MB`;
}

function money(value: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function toggle<T>(items: T[], item: T) {
  return items.includes(item) ? items.filter((current) => current !== item) : [...items, item];
}

function describeInput(input?: Record<string, unknown>) {
  const dataset = input?.dataset as { required_tables?: string[] } | undefined;
  return dataset?.required_tables?.length ? dataset.required_tables.join(", ") : "Dataset and report inputs are registry-defined.";
}

function describeOutput(contract?: RegistryEntry["artifact_contract"], entry?: EntrypointSpec) {
  return entry?.produces?.contract ?? contract?.required_files?.join(", ") ?? "Report artifact generated by the registered entrypoint.";
}

function metaApplied(value: unknown) {
  const object = value as { applied?: boolean; method?: string } | undefined;
  return object?.applied ? object.method ?? "applied" : "not applied";
}

function metaAvailable(value: unknown) {
  const object = value as { available?: boolean; reason?: string } | undefined;
  return object?.available ? "available" : object?.reason ?? "not available";
}
