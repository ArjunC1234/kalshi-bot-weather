import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Database, Filter, Play, Rows3, Table2 } from "lucide-react";
import {
  CommandChart,
  commandPalette,
  createCommandBarOption,
  createCommandHeatmapOption,
  createCommandLineOption,
  createCommandScatterOption,
  type CommandHeatCell,
  type CommandPoint,
} from "../../components/charts";
import type { ArtifactMetadata, ArtifactSchemaColumn, VisualizationQueryRequest, VisualizationQueryResponse } from "../../types/index";
import type { JsonObject } from "../../types/index";

export type DataExplorerWorkspaceProps = {
  datasets: ArtifactMetadata[];
  result?: VisualizationQueryResponse;
  selectedDatasetId?: string;
  onRunQuery?: (request: VisualizationQueryRequest) => void;
};

type ChartMode = "line" | "bar" | "scatter" | "heatmap";

export function DataExplorerWorkspace({ datasets, result, selectedDatasetId, onRunQuery }: DataExplorerWorkspaceProps) {
  const [datasetId, setDatasetId] = useState(selectedDatasetId ?? datasets[0]?.id ?? "");
  const dataset = datasets.find((item) => item.id === datasetId) ?? datasets[0];
  const tables = Object.keys(dataset?.schemas ?? dataset?.table_counts ?? {});
  const [table, setTable] = useState(tables[0] ?? "weather_snapshots");
  const columns = Object.keys(dataset?.schemas?.[table]?.columns ?? result?.schema.columns ?? {});
  const [x, setX] = useState(columns[0] ?? "snapshot_time_utc");
  const [y, setY] = useState(columns[1] ?? "value");
  const [group, setGroup] = useState("city");
  const [aggregation, setAggregation] = useState("avg");
  const [hourBlocks, setHourBlocks] = useState(4);
  const [chartMode, setChartMode] = useState<ChartMode>("line");
  const [pageSize, setPageSize] = useState(500);
  const option = useMemo(() => buildOption(chartMode, result, x, y, group), [chartMode, result, x, y, group]);
  const metadata = result?.metadata;

  const run = () => {
    if (!dataset) return;
    onRunQuery?.({
      artifact_path: dataset.path,
      query: {
        table,
        x,
        y,
        groups: group ? [group] : [],
        aggregation: aggregation === "none" ? undefined : { op: aggregation as "count" | "sum" | "avg" | "min" | "max", field: y, as: `${aggregation}_${y}` },
        hour_blocks: hourBlocks,
        page_size: pageSize,
        decimate_to: chartMode === "scatter" ? 2500 : undefined,
        density: chartMode === "scatter" ? { bins: 48 } : undefined,
      },
    });
  };

  return (
    <div style={styles.workspace}>
      <aside style={styles.controls}>
        <Header eyebrow="Data Explorer" title="Query builder" />
        <Field label="Dataset"><select value={dataset?.id ?? ""} onChange={(event) => setDatasetId(event.target.value)} style={styles.input}>{datasets.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select></Field>
        <Field label="Table"><select value={table} onChange={(event) => setTable(event.target.value)} style={styles.input}>{tables.map((item) => <option key={item} value={item}>{item}</option>)}</select></Field>
        <div style={styles.twoCols}>
          <Field label="X axis"><select value={x} onChange={(event) => setX(event.target.value)} style={styles.input}>{columns.map((item) => <option key={item} value={item}>{item}</option>)}</select></Field>
          <Field label="Y axis"><select value={y} onChange={(event) => setY(event.target.value)} style={styles.input}>{columns.map((item) => <option key={item} value={item}>{item}</option>)}</select></Field>
        </div>
        <Field label="Group"><select value={group} onChange={(event) => setGroup(event.target.value)} style={styles.input}><option value="">None</option>{columns.map((item) => <option key={item} value={item}>{item}</option>)}</select></Field>
        <div style={styles.twoCols}>
          <Field label="Aggregation"><select value={aggregation} onChange={(event) => setAggregation(event.target.value)} style={styles.input}>{["none", "count", "sum", "avg", "min", "max"].map((item) => <option key={item} value={item}>{item}</option>)}</select></Field>
          <Field label="Chart"><select value={chartMode} onChange={(event) => setChartMode(event.target.value as ChartMode)} style={styles.input}>{["line", "bar", "scatter", "heatmap"].map((item) => <option key={item} value={item}>{item}</option>)}</select></Field>
        </div>
        <Range label="24h blocks" value={hourBlocks} min={1} max={24} step={1} onChange={setHourBlocks} />
        <Range label="Page size" value={pageSize} min={100} max={5000} step={100} onChange={setPageSize} />
        <button type="button" onClick={run} style={styles.primaryButton}><Play size={16} /> Run Query</button>
      </aside>

      <main style={styles.main}>
        <section style={styles.coverage}>
          <DatasetSummary dataset={dataset} />
          <MetadataStrip metadata={metadata} />
        </section>
        <CommandChart
          eyebrow="Visualization"
          title={`${table}: ${x} vs ${y}`}
          subtitle="Large results use backend aggregation, hour blocks, sampling, decimation, or density before rendering."
          height={420}
          option={option}
          status={result ? "ready" : "empty"}
          densityLabel={densityLabel(metadata)}
          inspector={<QueryInspector metadata={metadata} x={x} y={y} group={group} />}
        />
        <RawRows rows={result?.rows ?? []} />
      </main>

      <aside style={styles.inspector}>
        <Header eyebrow="Schema" title="Selected table" />
        <SchemaList dataset={dataset} table={table} />
      </aside>
    </div>
  );
}

function Header({ eyebrow, title }: { eyebrow: string; title: string }) {
  return <header style={styles.localHeader}><div style={styles.eyebrow}>{eyebrow}</div><h3 style={styles.h3}>{title}</h3></header>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label style={styles.field}><span>{label}</span>{children}</label>;
}

function Range({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max: number; step: number; onChange: (value: number) => void }) {
  return <label style={styles.field}><span>{label}: <strong style={{ color: commandPalette.text }}>{value}</strong></span><input type="range" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}

function DatasetSummary({ dataset }: { dataset?: ArtifactMetadata }) {
  const coverage = dataset?.coverage;
  return <section style={styles.panel}><Header eyebrow="Dataset" title={dataset?.id ?? "No dataset"} /><div style={styles.factGrid}><Fact icon={<Database size={16} />} label="Cities" value={String(coverage?.cities.length ?? 0)} /><Fact icon={<Rows3 size={16} />} label="Rows" value={numericValues(dataset?.table_counts).reduce((sum, count) => sum + count, 0).toLocaleString()} /><Fact icon={<Table2 size={16} />} label="Tables" value={String(Object.keys(dataset?.table_counts ?? {}).length)} /><Fact icon={<Filter size={16} />} label="Range" value={`${coverage?.date_range.start ?? "?"} - ${coverage?.date_range.end ?? "?"}`} /></div></section>;
}

function MetadataStrip({ metadata }: { metadata?: VisualizationQueryResponse["metadata"] }) {
  return <section style={styles.panel}><Header eyebrow="Result Size" title="Backend safeguards" /><div style={styles.factGrid}>{["total_rows", "filtered_rows", "result_rows", "returned_rows"].map((key) => <Fact key={key} label={key.replace(/_/g, " ")} value={String(metadata?.[key as keyof typeof metadata] ?? "n/a")} />)}</div></section>;
}

function Fact({ label, value, icon }: { label: string; value: string; icon?: React.ReactNode }) {
  return <div style={styles.fact}>{icon}<span>{label}</span><strong>{value}</strong></div>;
}

function QueryInspector({ metadata, x, y, group }: { metadata?: VisualizationQueryResponse["metadata"]; x: string; y: string; group: string }) {
  return <div style={styles.inspectorGrid}><Fact label="X" value={x} /><Fact label="Y" value={y} /><Fact label="Group" value={group || "none"} /><Fact label="Returned" value={String(metadata?.returned_rows ?? "n/a")} /></div>;
}

function SchemaList({ dataset, table }: { dataset?: ArtifactMetadata; table: string }) {
  const columns = (dataset?.schemas?.[table]?.columns ?? {}) as Record<string, ArtifactSchemaColumn>;
  return <div style={styles.schemaList}>{Object.entries(columns).map(([name, spec]) => <div key={name} style={styles.schemaRow}><strong>{name}</strong><span>{spec.type ?? "unknown"}</span></div>)}</div>;
}

function RawRows({ rows }: { rows: JsonObject[] }) {
  const columns = Object.keys(rows[0] ?? {}).slice(0, 8);
  return <section style={styles.panel}><Header eyebrow="Raw Rows" title={`Preview (${rows.length.toLocaleString()} returned)`} />{rows.length ? <div style={styles.rawWrap}><table style={styles.table}><thead><tr>{columns.map((column) => <th key={column} style={styles.th}>{column}</th>)}</tr></thead><tbody>{rows.slice(0, 20).map((row, index) => <tr key={index}>{columns.map((column) => <td key={column} style={styles.td}>{formatCell(row[column])}</td>)}</tr>)}</tbody></table></div> : <p style={styles.muted}>Run a query to inspect rows.</p>}</section>;
}

function buildOption(mode: ChartMode, result: VisualizationQueryResponse | undefined, x: string, y: string, group: string) {
  const rows: JsonObject[] = result?.rows ?? [];
  if (!rows.length) return undefined;
  if (mode === "scatter") {
    const points: CommandPoint[] = rows.map((row, index) => ({ x: numberish(row[x], index), y: numberish(row[y], 0), group: String(row[group] ?? "series"), label: String(row[x] ?? index) }));
    return createCommandScatterOption(points, { xName: x, yName: y, large: points.length > 1200 });
  }
  if (mode === "heatmap") {
    const cells: CommandHeatCell[] = rows.map((row, index) => ({ x: String(row[x] ?? index), y: String(row[group] ?? "series"), value: numberish(row[y], 0) }));
    return createCommandHeatmapOption(cells, { xName: x, yName: group, valueName: y });
  }
  const labels = rows.map((row, index) => String(row[x] ?? index));
  const values = rows.map((row) => numberish(row[y], 0));
  if (mode === "bar") return createCommandBarOption(labels, values, { yName: y });
  return createCommandLineOption(labels, [{ name: y, data: values, color: commandPalette.teal }], { xName: x, yName: y, showZoom: labels.length > 40 });
}

function numericValues(record: Record<string, number> | undefined) {
  return Object.values(record ?? {}).map((value) => Number(value)).filter(Number.isFinite);
}

function densityLabel(metadata?: VisualizationQueryResponse["metadata"]) {
  if (!metadata) return "No query yet";
  const parts = [
    `${metadata.returned_rows.toLocaleString()} returned`,
    metadata.rows_after_decimation !== metadata.result_rows ? `decimated to ${metadata.rows_after_decimation.toLocaleString()}` : "",
    metadata.rows_after_sampling !== metadata.filtered_rows ? `sampled to ${metadata.rows_after_sampling.toLocaleString()}` : "",
  ].filter(Boolean);
  return parts.join(" / ");
}

function numberish(value: unknown, fallback: number) {
  const num = typeof value === "number" ? value : Number(value);
  return Number.isFinite(num) ? num : fallback;
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "n/a";
  if (typeof value === "object") return JSON.stringify(value).slice(0, 120);
  return String(value);
}

const styles: Record<string, CSSProperties> = {
  workspace: { display: "grid", gridTemplateColumns: "minmax(260px, 330px) minmax(560px, 1fr) minmax(260px, 340px)", gap: 16, color: commandPalette.text },
  controls: { borderRight: `1px solid ${commandPalette.border}`, paddingRight: 12 },
  main: { display: "grid", gap: 14, alignContent: "start" },
  inspector: { borderLeft: `1px solid ${commandPalette.border}`, paddingLeft: 12 },
  coverage: { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 14 },
  panel: { border: `1px solid ${commandPalette.border}`, borderRadius: 10, padding: 14, background: "rgba(17,24,25,.94)" },
  localHeader: { display: "grid", gap: 3, marginBottom: 12 },
  eyebrow: { color: commandPalette.teal, fontSize: 11, fontWeight: 900, letterSpacing: 1.7, textTransform: "uppercase" },
  h3: { margin: 0, fontSize: 15 },
  field: { display: "grid", gap: 7, color: commandPalette.muted, fontSize: 12, marginBottom: 12 },
  input: { width: "100%", background: "#0b1719", color: commandPalette.text, border: `1px solid ${commandPalette.border}`, borderRadius: 6, padding: "8px 10px" },
  twoCols: { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 },
  primaryButton: { display: "inline-flex", gap: 8, alignItems: "center", justifyContent: "center", width: "100%", background: commandPalette.teal, color: "#071012", border: 0, borderRadius: 8, padding: "10px 14px", fontWeight: 900, cursor: "pointer" },
  factGrid: { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 },
  fact: { border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 10, display: "grid", gap: 3, color: commandPalette.muted },
  inspectorGrid: { display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8 },
  schemaList: { display: "grid", gap: 8 },
  schemaRow: { display: "flex", justifyContent: "space-between", gap: 12, border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 9, color: commandPalette.muted },
  rawWrap: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", minWidth: 720 },
  th: { textAlign: "left", color: commandPalette.muted, borderBottom: `1px solid ${commandPalette.border}`, padding: 8, fontSize: 12 },
  td: { color: commandPalette.text, borderBottom: `1px solid ${commandPalette.border}`, padding: 8, fontSize: 12, maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  muted: { color: commandPalette.muted, margin: 0 },
};
