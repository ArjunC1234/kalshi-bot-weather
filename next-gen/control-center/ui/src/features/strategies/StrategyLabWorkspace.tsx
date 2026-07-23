import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Gauge, Play, Radar, ShieldCheck, SlidersHorizontal } from "lucide-react";
import {
  CommandChart,
  commandPalette,
  createCommandBarOption,
  createCommandHeatmapOption,
  createCommandLineOption,
  type CommandHeatCell,
} from "../../components/charts";
import type { ArtifactMetadata, CreateJobRequest, JobRecord, RegistryEntry } from "../../types/index";
import type { JsonObject } from "../../types/index";

export type StrategyLabWorkspaceProps = {
  strategies: RegistryEntry[];
  datasets: ArtifactMetadata[];
  modelReports: ArtifactMetadata[];
  strategyReports: ArtifactMetadata[];
  jobs: JobRecord[];
  selectedStrategyId?: string;
  onRunJob?: (request: CreateJobRequest) => void;
  onOpenReport?: (report: ArtifactMetadata) => void;
};

export function StrategyLabWorkspace({
  strategies,
  datasets,
  modelReports,
  strategyReports,
  jobs,
  selectedStrategyId,
  onRunJob,
  onOpenReport,
}: StrategyLabWorkspaceProps) {
  const [strategyId, setStrategyId] = useState(selectedStrategyId ?? strategies[0]?.id ?? "");
  const selectedStrategy = strategies.find((strategy) => strategy.id === strategyId) ?? strategies[0];
  const entrypoints = useMemo(() => getEntrypoints(selectedStrategy), [selectedStrategy]);
  const [entrypoint, setEntrypoint] = useState(entrypoints[0]?.name ?? "backtest");
  const [datasetPath, setDatasetPath] = useState(datasets[0]?.path ?? "");
  const [reportPath, setReportPath] = useState(modelReports[0]?.path ?? "");
  const [edgeFloor, setEdgeFloor] = useState(0.05);
  const [maxRisk, setMaxRisk] = useState(250);
  const activeReport = strategyReports[0];
  const gateCells = buildGateCells();
  const activeJobs = jobs.filter((job) => job.kind === "strategy" || job.registry_id === selectedStrategy?.id);
  const canRun = Boolean(selectedStrategy && datasetPath && reportPath);

  const handleRun = () => {
    if (!selectedStrategy || !canRun) return;
    onRunJob?.({
      kind: "strategy",
      registry_id: selectedStrategy.id,
      entrypoint,
      params: {
        dataset_path: datasetPath,
        model_report_path: reportPath,
        edge_floor: edgeFloor,
        max_risk_dollars: maxRisk,
      } as JsonObject,
    });
  };

  return (
    <div style={styles.workspace}>
      <aside style={styles.registry}>
        <Header eyebrow="Strategy Registry" title="Strategies" />
        <div style={styles.list}>
          {strategies.map((strategy) => (
            <button
              key={strategy.id}
              type="button"
              onClick={() => {
                setStrategyId(strategy.id);
                setEntryPointSafely(strategy, setEntrypoint);
              }}
              style={{ ...styles.registryCard, ...(strategy.id === selectedStrategy?.id ? styles.selected : undefined) }}
            >
              <Radar size={24} color={commandPalette.teal} />
              <span style={styles.registryText}>
                <strong>{strategy.label ?? strategy.id}</strong>
                <small>{strategy.purpose ?? strategy.description ?? "Registered strategy"}</small>
              </span>
              <span style={styles.version}>v{String(strategy.version ?? "1")}</span>
            </button>
          ))}
        </div>
      </aside>

      <main style={styles.main}>
        {selectedStrategy ? (
          <>
            <section style={styles.hero}>
              <div style={styles.icon}><Gauge size={42} /></div>
              <div>
                <div style={styles.titleRow}>
                  <h1 style={styles.h1}>{selectedStrategy.label ?? selectedStrategy.id}</h1>
                  <span style={styles.ready}>Registered</span>
                </div>
                <p style={styles.description}>{selectedStrategy.purpose ?? selectedStrategy.description ?? "Evaluate model edges against Kalshi market prices with registry-defined controls."}</p>
                <div style={styles.metaGrid}>
                  <Fact label="Inputs" value="Dataset + model report" />
                  <Fact label="Outputs" value="Strategy report, trades, sweeps" />
                  <Fact label="Risk Contract" value="Budget, gate, threshold" />
                </div>
              </div>
            </section>

            <nav style={styles.tabs}>
              {entrypoints.map((item) => (
                <button key={item.name} type="button" onClick={() => setEntrypoint(item.name)} style={{ ...styles.tab, ...(item.name === entrypoint ? styles.activeTab : undefined) }}>
                  <SlidersHorizontal size={16} /> {titleCase(item.name)}
                </button>
              ))}
            </nav>

            <div style={styles.launchGrid}>
              <section style={styles.panel}>
                <Header eyebrow="Launch" title="Experiment inputs" />
                <Field label="Dataset">
                  <select value={datasetPath} onChange={(event) => setDatasetPath(event.target.value)} style={styles.input}>
                    {datasets.map((dataset) => <option key={dataset.id} value={dataset.path}>{dataset.id}</option>)}
                  </select>
                </Field>
                <Field label="Model report">
                  <select value={reportPath} onChange={(event) => setReportPath(event.target.value)} style={styles.input}>
                    {modelReports.map((report) => <option key={report.id} value={report.path}>{report.id}</option>)}
                  </select>
                </Field>
                <div style={styles.validation}>{canRun ? <><ShieldCheck size={16} /> Dataset and model report selected.</> : "Select both a dataset and model report before launch."}</div>
              </section>
              <section style={styles.panel}>
                <Header eyebrow="Gates" title="Risk and selection controls" />
                <Range label="Edge floor" value={edgeFloor} min={0} max={0.25} step={0.01} suffix="" onChange={setEdgeFloor} />
                <Range label="Max risk" value={maxRisk} min={25} max={1000} step={25} suffix=" USD" onChange={setMaxRisk} />
                <button type="button" onClick={handleRun} disabled={!canRun} style={styles.primaryButton}><Play size={16} /> Run Strategy Job</button>
              </section>
            </div>

            <CommandChart
              eyebrow="Gate Sweep"
              title="Threshold response"
              subtitle="Reference line and click-to-inspector pattern for dense gates."
              height={300}
              option={createCommandHeatmapOption(gateCells, { xName: "Edge floor", yName: "Risk cap", valueName: "PnL" })}
              inspector={<SelectedGateSummary edgeFloor={edgeFloor} maxRisk={maxRisk} />}
              densityLabel="Inspector replaces massive hover text"
            />

            <div style={styles.chartGrid}>
              <CommandChart
                title="Daily PnL"
                height={240}
                option={createCommandBarOption(["D1", "D2", "D3", "D4", "D5", "D6"], [12, -4, 18, 7, -9, 21], { colors: [commandPalette.teal, commandPalette.coral, commandPalette.teal, commandPalette.teal, commandPalette.coral, commandPalette.teal] })}
              />
              <CommandChart
                title="Equity Curve"
                height={240}
                option={createCommandLineOption(["D1", "D2", "D3", "D4", "D5", "D6"], [{ name: "PnL", data: [12, 8, 26, 33, 24, 45], color: commandPalette.teal }], { referenceLines: [{ y: 0, label: "Break even", color: commandPalette.amber }] })}
              />
            </div>
          </>
        ) : <section style={styles.panel}>No strategies registered.</section>}
      </main>

      <aside style={styles.reports}>
        <Header eyebrow="Reports" title="Strategy diagnostics" />
        {activeReport ? (
          <button type="button" onClick={() => onOpenReport?.(activeReport)} style={styles.reportCard}>
            <strong>{activeReport.id}</strong>
            <span>{activeReport.contract ?? activeReport.artifact_type}</span>
          </button>
        ) : <p style={styles.muted}>Run a strategy job to produce reports.</p>}
        <section style={styles.panel}>
          <Header eyebrow="Queue" title="Strategy jobs" />
          {activeJobs.map((job) => <div key={job.id} style={styles.jobRow}><span>{job.id}</span><strong>{job.status}</strong></div>)}
        </section>
      </aside>
    </div>
  );
}

function Header({ eyebrow, title }: { eyebrow: string; title: string }) {
  return <header style={styles.localHeader}><div style={styles.eyebrow}>{eyebrow}</div><h3 style={styles.h3}>{title}</h3></header>;
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div style={styles.fact}><span>{label}</span><strong>{value}</strong></div>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label style={styles.field}><span>{label}</span>{children}</label>;
}

function Range({ label, value, min, max, step, suffix, onChange }: { label: string; value: number; min: number; max: number; step: number; suffix: string; onChange: (value: number) => void }) {
  return <label style={styles.field}><span>{label}: <strong style={{ color: commandPalette.text }}>{value}{suffix}</strong></span><input type="range" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}

function SelectedGateSummary({ edgeFloor, maxRisk }: { edgeFloor: number; maxRisk: number }) {
  return <div style={styles.inspectorGrid}><Fact label="Pinned Edge" value={edgeFloor.toFixed(2)} /><Fact label="Risk Cap" value={`$${maxRisk}`} /><Fact label="Hover Policy" value="Compact; click expands here" /></div>;
}

function getEntrypoints(entry?: RegistryEntry) {
  const raw = entry?.entrypoints && typeof entry.entrypoints === "object" && !Array.isArray(entry.entrypoints) ? entry.entrypoints as Record<string, unknown> : {};
  const entries = Object.keys(raw).length ? Object.keys(raw) : ["backtest", "evaluate"];
  return entries.map((name) => ({ name }));
}

function setEntryPointSafely(strategy: RegistryEntry, setter: (value: string) => void) {
  setter(getEntrypoints(strategy)[0]?.name ?? "backtest");
}

function buildGateCells(): CommandHeatCell[] {
  const edges = ["0.02", "0.04", "0.06", "0.08", "0.10"];
  const risks = ["$50", "$100", "$250", "$500"];
  return risks.flatMap((risk, y) => edges.map((edge, x) => ({ x: edge, y: risk, value: Math.round((x + 1) * 11 - y * 4 + (x === 2 ? 9 : 0)) })));
}

function titleCase(value: string) {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

const styles: Record<string, CSSProperties> = {
  workspace: { display: "grid", gridTemplateColumns: "minmax(240px, 300px) minmax(520px, 1fr) minmax(300px, 380px)", gap: 16, color: commandPalette.text },
  registry: { borderRight: `1px solid ${commandPalette.border}`, paddingRight: 12 },
  main: { display: "grid", gap: 14 },
  reports: { display: "grid", gap: 14, alignContent: "start" },
  list: { display: "grid", gap: 8 },
  localHeader: { display: "grid", gap: 3, marginBottom: 12 },
  eyebrow: { color: commandPalette.teal, fontSize: 11, fontWeight: 900, letterSpacing: 1.7, textTransform: "uppercase" },
  h1: { margin: 0, fontSize: 26, fontWeight: 900 },
  h3: { margin: 0, fontSize: 15 },
  description: { margin: "6px 0 0", color: commandPalette.muted, lineHeight: 1.45 },
  registryCard: { display: "grid", gridTemplateColumns: "28px 1fr auto", gap: 10, alignItems: "center", color: commandPalette.text, background: commandPalette.panel, border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 10, textAlign: "left", cursor: "pointer" },
  selected: { borderColor: commandPalette.borderStrong, boxShadow: "inset 3px 0 0 #1DD6B7" },
  registryText: { display: "grid", gap: 2 },
  version: { color: commandPalette.teal, border: `1px solid ${commandPalette.borderStrong}`, borderRadius: 6, padding: "2px 6px", fontSize: 11 },
  ready: { background: "rgba(29,214,183,.12)", color: commandPalette.teal, borderRadius: 6, padding: "3px 7px", fontSize: 11, fontWeight: 900 },
  hero: { display: "grid", gridTemplateColumns: "84px 1fr", gap: 16, border: `1px solid ${commandPalette.border}`, borderRadius: 10, padding: 16, background: "linear-gradient(135deg, rgba(29,214,183,.10), rgba(79,125,255,.06))" },
  icon: { display: "grid", placeItems: "center", color: commandPalette.teal, border: `1px solid ${commandPalette.borderStrong}`, borderRadius: 10 },
  titleRow: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  metaGrid: { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10, marginTop: 12 },
  fact: { border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 10, display: "grid", gap: 3, color: commandPalette.muted },
  tabs: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", borderBottom: `1px solid ${commandPalette.border}` },
  tab: { display: "flex", gap: 8, justifyContent: "center", alignItems: "center", padding: 12, color: commandPalette.muted, background: "transparent", border: 0, borderBottom: "2px solid transparent", cursor: "pointer" },
  activeTab: { color: commandPalette.teal, borderBottomColor: commandPalette.teal },
  launchGrid: { display: "grid", gridTemplateColumns: "minmax(280px, 1fr) minmax(280px, 1fr)", gap: 14 },
  panel: { border: `1px solid ${commandPalette.border}`, borderRadius: 10, padding: 14, background: "rgba(17,24,25,.94)" },
  field: { display: "grid", gap: 7, color: commandPalette.muted, fontSize: 12, marginBottom: 12 },
  input: { width: "100%", background: "#0b1719", color: commandPalette.text, border: `1px solid ${commandPalette.border}`, borderRadius: 6, padding: "8px 10px" },
  validation: { display: "flex", gap: 8, color: commandPalette.green, alignItems: "center", fontSize: 12 },
  primaryButton: { display: "inline-flex", gap: 8, alignItems: "center", justifyContent: "center", background: commandPalette.teal, color: "#071012", border: 0, borderRadius: 8, padding: "9px 14px", fontWeight: 900, cursor: "pointer" },
  chartGrid: { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 14 },
  inspectorGrid: { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8 },
  reportCard: { display: "grid", gap: 4, color: commandPalette.text, background: commandPalette.panel, border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 12, textAlign: "left", cursor: "pointer" },
  jobRow: { display: "flex", justifyContent: "space-between", gap: 10, color: commandPalette.muted, padding: "8px 0", borderBottom: `1px solid ${commandPalette.border}` },
  muted: { color: commandPalette.muted, margin: 0 },
};
