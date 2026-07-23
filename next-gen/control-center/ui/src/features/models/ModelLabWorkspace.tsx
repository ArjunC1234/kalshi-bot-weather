import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Activity, BarChart3, Beaker, Box, Database, Play, Search } from "lucide-react";
import { CommandChart, commandPalette, createCommandBarOption, createCommandLineOption } from "../../components/charts";
import type { ArtifactMetadata, CreateJobRequest, JobRecord, RegistryEntry } from "../../types/index";
import type { JsonObject, JsonValue } from "../../types/index";

export type ModelLabWorkspaceProps = {
  models: RegistryEntry[];
  datasets: ArtifactMetadata[];
  reports: ArtifactMetadata[];
  jobs: JobRecord[];
  selectedModelId?: string;
  onRunJob?: (request: CreateJobRequest) => void;
  onOpenReport?: (report: ArtifactMetadata) => void;
  onSelectJob?: (job: JobRecord) => void;
};

type ParamValue = string | number | boolean;

export function ModelLabWorkspace({
  models,
  datasets,
  reports,
  jobs,
  selectedModelId,
  onRunJob,
  onOpenReport,
  onSelectJob,
}: ModelLabWorkspaceProps) {
  const [search, setSearch] = useState("");
  const [modelId, setModelId] = useState(selectedModelId ?? models[0]?.id ?? "");
  const selectedModel = models.find((model) => model.id === modelId) ?? models[0];
  const entrypoints = useMemo(() => getEntrypoints(selectedModel), [selectedModel]);
  const [entrypointName, setEntrypointName] = useState(entrypoints[0]?.name ?? "train");
  const activeEntrypoint = entrypoints.find((entrypoint) => entrypoint.name === entrypointName) ?? entrypoints[0];
  const params = useMemo(() => getParamSpecs(activeEntrypoint?.spec), [activeEntrypoint]);
  const [datasetPath, setDatasetPath] = useState(datasets[0]?.path ?? "");
  const [values, setValues] = useState<Record<string, ParamValue>>({});
  const filteredModels = models.filter((model) =>
    [model.id, model.label, model.description, model.purpose].filter(Boolean).join(" ").toLowerCase().includes(search.toLowerCase()),
  );
  const modelReports = reports.filter((report) =>
    [report.source_export_id, report.path, report.id, report.contract].filter(Boolean).join(" ").toLowerCase().includes(selectedModel?.id.toLowerCase() ?? ""),
  );
  const modelJobs = jobs.filter((job) => job.kind === "model" || job.registry_id === selectedModel?.id);
  const compatibility = buildCompatibility(selectedModel, datasets.find((dataset) => dataset.path === datasetPath));

  const handleRun = () => {
    if (!selectedModel || !activeEntrypoint || compatibility.blocking.length) return;
    onRunJob?.({
      kind: "model",
      registry_id: selectedModel.id,
      entrypoint: activeEntrypoint.name,
      params: {
        dataset_path: datasetPath,
        ...Object.fromEntries(params.map((param) => [param.name, values[param.name] ?? param.defaultValue]).filter(([, value]) => value !== undefined)),
      } as JsonObject,
    });
  };

  return (
    <div style={styles.workspace}>
      <aside style={styles.registry}>
        <div style={styles.sectionHead}>
          <div>
            <div style={styles.eyebrow}>Model Registry</div>
            <h2 style={styles.h2}>Models</h2>
          </div>
          <button type="button" style={styles.ghostButton}>+ New Model</button>
        </div>
        <label style={styles.search}>
          <Search size={16} />
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search models..." style={styles.inputBare} />
        </label>
        <div style={styles.list}>
          {filteredModels.map((model) => (
            <button
              key={model.id}
              type="button"
              onClick={() => {
                setModelId(model.id);
                setEntryPointSafely(model, setEntrypointName);
              }}
              style={{ ...styles.registryCard, ...(model.id === selectedModel?.id ? styles.selectedCard : undefined) }}
            >
              <Beaker size={24} color={commandPalette.teal} />
              <span style={styles.registryText}>
                <strong>{model.label ?? model.id}</strong>
                <small>{model.description ?? model.purpose ?? "Registry model"}</small>
              </span>
              <span style={styles.version}>v{String(model.version ?? "1")}</span>
            </button>
          ))}
        </div>
      </aside>

      <main style={styles.main}>
        {selectedModel ? (
          <>
            <section style={styles.hero}>
              <div style={styles.modelIcon}><Beaker size={40} /></div>
              <div style={styles.heroContent}>
                <div style={styles.modelTitleRow}>
                  <h1 style={styles.h1}>{selectedModel.label ?? selectedModel.id}</h1>
                  <span style={styles.version}>v{String(selectedModel.version ?? "1")}</span>
                  <span style={styles.ready}>Registered</span>
                </div>
                <p style={styles.description}>{selectedModel.purpose ?? selectedModel.description ?? "Registry-discoverable model for weather prediction workflows."}</p>
                <div style={styles.metaGrid}>
                  <Fact label="Inputs" value={summarizeJson(selectedModel.inputs)} />
                  <Fact label="Outputs" value={summarizeJson(selectedModel.outputs)} />
                  <Fact label="Contract" value={String(selectedModel.contract ?? selectedModel.kind)} />
                </div>
              </div>
            </section>

            <nav style={styles.tabs} aria-label="Model entrypoints">
              {entrypoints.map((entrypoint) => (
                <button
                  key={entrypoint.name}
                  type="button"
                  onClick={() => setEntrypointName(entrypoint.name)}
                  style={{ ...styles.tab, ...(entrypoint.name === activeEntrypoint?.name ? styles.activeTab : undefined) }}
                >
                  {entrypoint.name === "train" ? <Activity size={16} /> : entrypoint.name === "backtest" ? <BarChart3 size={16} /> : <Play size={16} />}
                  {titleCase(entrypoint.name)}
                </button>
              ))}
            </nav>

            <div style={styles.launchGrid}>
              <section style={styles.panel}>
                <Header eyebrow="Launch" title={`${titleCase(activeEntrypoint?.name ?? "run")} checklist`} />
                <Field label="Dataset">
                  <select value={datasetPath} onChange={(event) => setDatasetPath(event.target.value)} style={styles.input}>
                    {datasets.map((dataset) => <option key={dataset.id} value={dataset.path}>{dataset.id}</option>)}
                  </select>
                </Field>
                <div style={styles.splitBar} aria-label="Data split">
                  <span style={{ ...styles.splitSegment, flex: 7, background: commandPalette.teal }}>Train 70%</span>
                  <span style={{ ...styles.splitSegment, flex: 1.5, background: commandPalette.blue }}>Validate 15%</span>
                  <span style={{ ...styles.splitSegment, flex: 1.5, background: commandPalette.amber }}>Test 15%</span>
                </div>
                <CompatibilityPanel blocking={compatibility.blocking} warnings={compatibility.warnings} />
              </section>

              <section style={styles.panel}>
                <Header eyebrow="Parameters" title="Registry schema" action={<button type="button" style={styles.ghostButton}>Load Preset</button>} />
                <div style={styles.paramTable}>
                  {params.length ? params.map((param) => (
                    <label key={param.name} style={styles.paramRow}>
                      <span>{param.label}</span>
                      <small>{param.type}</small>
                      <ParamInput param={param} value={values[param.name] ?? param.defaultValue} onChange={(value) => setValues((current) => ({ ...current, [param.name]: value }))} />
                    </label>
                  )) : <p style={styles.muted}>No parameters declared for this entrypoint.</p>}
                </div>
              </section>
            </div>

            <section style={styles.panel}>
              <Header eyebrow="Command" title="Run preview" action={<button type="button" onClick={handleRun} disabled={Boolean(compatibility.blocking.length)} style={styles.primaryButton}><Play size={16} /> Run Job</button>} />
              <code style={styles.command}>{buildCommandPreview(activeEntrypoint?.spec)}</code>
            </section>

            <section style={styles.panel}>
              <Header eyebrow="Job Queue" title="Recent model work" />
              <JobTable jobs={modelJobs} onSelect={onSelectJob} />
            </section>
          </>
        ) : <EmptyState title="No models registered" />}
      </main>

      <aside style={styles.reports}>
        <Header eyebrow="Reports" title="Latest diagnostics" />
        <CommandChart
          title="Checkpoint Performance"
          subtitle="Compact renderer slot for selected model reports"
          height={220}
          option={createCommandLineOption(["T-48", "T-36", "T-24", "T-12", "T"], [
            { name: "MAE", data: [2.8, 2.4, 2.1, 1.8, 1.5], color: commandPalette.teal },
            { name: "RMSE", data: [3.5, 3.1, 2.8, 2.5, 2.2], color: commandPalette.blue },
          ], { yName: "Error", showZoom: false })}
          legend={[
            { id: "mae", label: "MAE", color: commandPalette.teal },
            { id: "rmse", label: "RMSE", color: commandPalette.blue },
          ]}
        />
        <CommandChart
          title="Report Tables"
          height={220}
          option={createCommandBarOption(["predictions", "errors", "calibration", "diagnostics"], [96, 72, 12, 24])}
        />
        <div style={styles.reportList}>
          {modelReports.slice(0, 5).map((report) => (
            <button key={report.id} type="button" onClick={() => onOpenReport?.(report)} style={styles.reportCard}>
              <Box size={16} />
              <span><strong>{report.id}</strong><small>{report.contract ?? report.artifact_type}</small></span>
            </button>
          ))}
          {!modelReports.length ? <p style={styles.muted}>No linked model reports found yet.</p> : null}
        </div>
      </aside>
    </div>
  );
}

function Header({ eyebrow, title, action }: { eyebrow: string; title: string; action?: ReactNode }) {
  return <header style={styles.localHeader}><div><div style={styles.eyebrow}>{eyebrow}</div><h3 style={styles.h3}>{title}</h3></div>{action}</header>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label style={styles.field}><span>{label}</span>{children}</label>;
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div style={styles.fact}><span>{label}</span><strong>{value}</strong></div>;
}

function EmptyState({ title }: { title: string }) {
  return <div style={styles.panel}><h3 style={styles.h3}>{title}</h3><p style={styles.muted}>Add a registry entry to populate this lab.</p></div>;
}

function CompatibilityPanel({ blocking, warnings }: { blocking: string[]; warnings: string[] }) {
  return <div style={styles.compatibility}>{blocking.length ? blocking : warnings.length ? warnings : ["Dataset and registry contract look compatible."] .map((message) => <span key={message} style={{ color: blocking.length ? commandPalette.coral : warnings.length ? commandPalette.amber : commandPalette.green }}>{message}</span>)}</div>;
}

type ParamSpec = { name: string; label: string; type: string; defaultValue?: ParamValue; options?: ParamValue[]; required?: boolean };

function ParamInput({ param, value, onChange }: { param: ParamSpec; value?: ParamValue; onChange: (value: ParamValue) => void }) {
  if (param.options?.length) {
    return <select value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} style={styles.input}>{param.options.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select>;
  }
  if (param.type === "boolean") {
    return <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />;
  }
  return <input type={param.type === "number" || param.type === "integer" ? "number" : "text"} value={String(value ?? "")} onChange={(event) => onChange(param.type === "number" || param.type === "integer" ? Number(event.target.value) : event.target.value)} style={styles.input} />;
}

function JobTable({ jobs, onSelect }: { jobs: JobRecord[]; onSelect?: (job: JobRecord) => void }) {
  return <div style={styles.table}>{jobs.map((job) => <button key={job.id} type="button" onClick={() => onSelect?.(job)} style={styles.tableRow}><span>{job.id}</span><span>{job.entrypoint}</span><span style={statusStyle(job.status)}>{job.status}</span><span>{job.output_path ?? "pending"}</span></button>)}</div>;
}

function getEntrypoints(entry?: RegistryEntry) {
  const raw = asRecord(entry?.entrypoints);
  return Object.entries(raw).map(([name, spec]) => ({ name, spec: asRecord(spec) }));
}

function setEntryPointSafely(model: RegistryEntry, setter: (value: string) => void) {
  setter(getEntrypoints(model)[0]?.name ?? "train");
}

function getParamSpecs(spec?: Record<string, unknown>): ParamSpec[] {
  const schema = asRecord(spec?.params_schema);
  const properties = asRecord(schema.properties);
  const source = Object.keys(properties).length ? properties : schema;
  const required = Array.isArray(schema.required) ? schema.required.map(String) : [];
  return Object.entries(source).map(([name, raw]) => {
    const record = asRecord(raw);
    return {
      name,
      label: String(record.title ?? record.label ?? name),
      type: String(record.type ?? "string"),
      defaultValue: toParamValue(record.default),
      options: Array.isArray(record.enum) ? record.enum.map(toParamValue).filter((value): value is ParamValue => value !== undefined) : undefined,
      required: required.includes(name) || Boolean(record.required),
    };
  });
}

function buildCompatibility(model?: RegistryEntry, dataset?: ArtifactMetadata) {
  const requiredTables = extractStrings(asRecord(model?.inputs).required_tables);
  const missing = requiredTables.filter((table) => !dataset?.files.includes(table));
  return {
    blocking: !dataset ? ["Select a dataset before launching."] : missing.map((table) => `Missing required table: ${table}`),
    warnings: dataset?.metadata_health?.status && dataset.metadata_health.status !== "complete" ? [`Dataset metadata is ${dataset.metadata_health.status}.`] : [],
  };
}

function buildCommandPreview(spec?: Record<string, unknown>) {
  const command = Array.isArray(spec?.command) ? spec.command.map(String) : ["python", "-m", "control.cli", "run-model"];
  return command.join(" ");
}

function summarizeJson(value: JsonValue | unknown) {
  if (!value) return "Not specified";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (typeof value === "object") return Object.keys(value).slice(0, 4).join(", ") || "Declared";
  return String(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function extractStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function toParamValue(value: unknown): ParamValue | undefined {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? value : undefined;
}

function titleCase(value: string) {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusStyle(status: string): CSSProperties {
  const color = status === "failed" ? commandPalette.coral : status === "running" ? commandPalette.teal : status === "queued" ? commandPalette.blue : commandPalette.green;
  return { color, fontWeight: 800 };
}

const styles: Record<string, CSSProperties> = {
  workspace: { display: "grid", gridTemplateColumns: "minmax(240px, 300px) minmax(520px, 1fr) minmax(320px, 420px)", gap: 16, color: commandPalette.text },
  registry: { borderRight: `1px solid ${commandPalette.border}`, paddingRight: 12 },
  main: { display: "grid", gap: 14 },
  reports: { display: "grid", gap: 14, alignContent: "start" },
  sectionHead: { display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", marginBottom: 12 },
  eyebrow: { color: commandPalette.teal, fontSize: 11, fontWeight: 900, letterSpacing: 1.7, textTransform: "uppercase" },
  h1: { margin: 0, fontSize: 26, fontWeight: 900 },
  h2: { margin: 0, fontSize: 18 },
  h3: { margin: 0, fontSize: 15 },
  description: { margin: "6px 0 0", color: commandPalette.muted, lineHeight: 1.45 },
  search: { display: "flex", alignItems: "center", gap: 8, border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: "8px 10px", marginBottom: 12 },
  inputBare: { flex: 1, background: "transparent", border: 0, color: commandPalette.text, outline: "none" },
  input: { width: "100%", background: "#0b1719", color: commandPalette.text, border: `1px solid ${commandPalette.border}`, borderRadius: 6, padding: "8px 10px" },
  list: { display: "grid", gap: 8 },
  registryCard: { display: "grid", gridTemplateColumns: "28px 1fr auto", gap: 10, alignItems: "center", background: commandPalette.panel, border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 10, color: commandPalette.text, textAlign: "left", cursor: "pointer" },
  selectedCard: { borderColor: commandPalette.borderStrong, boxShadow: "inset 3px 0 0 #1DD6B7" },
  registryText: { display: "grid", gap: 2 },
  version: { border: `1px solid ${commandPalette.borderStrong}`, color: commandPalette.teal, borderRadius: 6, padding: "2px 6px", fontSize: 11, fontWeight: 800 },
  ready: { background: "rgba(29,214,183,.12)", color: commandPalette.teal, borderRadius: 6, padding: "3px 7px", fontSize: 11, fontWeight: 900 },
  hero: { display: "grid", gridTemplateColumns: "84px 1fr", gap: 16, border: `1px solid ${commandPalette.border}`, borderRadius: 10, padding: 16, background: "linear-gradient(135deg, rgba(29,214,183,.10), rgba(79,125,255,.06))" },
  modelIcon: { display: "grid", placeItems: "center", border: `1px solid ${commandPalette.borderStrong}`, color: commandPalette.teal, borderRadius: 10, background: "rgba(29,214,183,.08)" },
  heroContent: { minWidth: 0 },
  modelTitleRow: { display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8 },
  metaGrid: { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10, marginTop: 12 },
  fact: { border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 10, display: "grid", gap: 3 },
  tabs: { display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 0, borderBottom: `1px solid ${commandPalette.border}` },
  tab: { display: "flex", justifyContent: "center", alignItems: "center", gap: 8, padding: "12px 8px", color: commandPalette.muted, background: "transparent", border: 0, borderBottom: "2px solid transparent", cursor: "pointer" },
  activeTab: { color: commandPalette.teal, borderBottomColor: commandPalette.teal },
  launchGrid: { display: "grid", gridTemplateColumns: "minmax(260px, .8fr) minmax(340px, 1fr)", gap: 14 },
  panel: { border: `1px solid ${commandPalette.border}`, borderRadius: 10, padding: 14, background: "rgba(17,24,25,.94)" },
  localHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 12 },
  field: { display: "grid", gap: 7, color: commandPalette.muted, fontSize: 12, marginBottom: 12 },
  splitBar: { display: "flex", borderRadius: 6, overflow: "hidden", marginBottom: 12 },
  splitSegment: { padding: "6px 8px", color: "#071012", fontSize: 11, fontWeight: 900, textAlign: "center" },
  compatibility: { display: "grid", gap: 6, fontSize: 12 },
  paramTable: { display: "grid", gap: 8 },
  paramRow: { display: "grid", gridTemplateColumns: "1fr 70px minmax(130px, .8fr)", gap: 8, alignItems: "center", color: commandPalette.text, fontSize: 12 },
  muted: { color: commandPalette.muted, margin: 0, fontSize: 12 },
  primaryButton: { display: "inline-flex", gap: 8, alignItems: "center", justifyContent: "center", background: commandPalette.teal, color: "#071012", border: 0, borderRadius: 8, padding: "9px 14px", fontWeight: 900, cursor: "pointer" },
  ghostButton: { background: "transparent", color: commandPalette.text, border: `1px solid ${commandPalette.border}`, borderRadius: 7, padding: "7px 10px", cursor: "pointer" },
  command: { display: "block", whiteSpace: "pre-wrap", color: commandPalette.teal, background: "#071012", border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 10 },
  table: { display: "grid", gap: 0, border: `1px solid ${commandPalette.border}`, borderRadius: 8, overflow: "hidden" },
  tableRow: { display: "grid", gridTemplateColumns: "1.1fr .7fr .6fr 1fr", gap: 12, padding: "10px 12px", color: commandPalette.text, background: "transparent", border: 0, borderBottom: `1px solid ${commandPalette.border}`, textAlign: "left", cursor: "pointer" },
  reportList: { display: "grid", gap: 8 },
  reportCard: { display: "grid", gridTemplateColumns: "18px 1fr", gap: 8, color: commandPalette.text, background: commandPalette.panel, border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 10, textAlign: "left", cursor: "pointer" },
};
