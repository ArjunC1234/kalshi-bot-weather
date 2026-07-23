import { useMemo, useState, type CSSProperties } from "react";
import { Box, FileText, Search, Table2 } from "lucide-react";
import { CommandChart, commandPalette, createCommandBarOption, createCommandLineOption } from "../../components/charts";
import type { ArtifactMetadata, ArtifactTableSchema } from "../../types/index";
import type { JsonObject } from "../../types/index";

export type ReportCenterProps = {
  reports: ArtifactMetadata[];
  selectedReportId?: string;
  onOpenArtifact?: (report: ArtifactMetadata) => void;
};

export function ReportCenter({ reports, selectedReportId, onOpenArtifact }: ReportCenterProps) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const [selectedId, setSelectedId] = useState(selectedReportId ?? reports[0]?.id ?? "");
  const filteredReports = reports.filter((report) => {
    const matchesType = type === "all" || report.artifact_type === type || report.contract === type;
    const text = [report.id, report.path, report.artifact_type, report.contract, report.source_export_id].filter(Boolean).join(" ").toLowerCase();
    return matchesType && text.includes(query.toLowerCase());
  });
  const selectedReport = reports.find((report) => report.id === selectedId) ?? filteredReports[0];
  const typeCounts = useMemo(() => countBy(reports, (report) => String(report.artifact_type ?? "unknown")), [reports]);

  return (
    <div style={styles.workspace}>
      <aside style={styles.browser}>
        <Header eyebrow="Report Center" title="Artifacts" />
        <label style={styles.search}><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search reports..." style={styles.inputBare} /></label>
        <select value={type} onChange={(event) => setType(event.target.value)} style={styles.input}>
          <option value="all">All report types</option>
          {Object.keys(typeCounts).map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <div style={styles.list}>
          {filteredReports.map((report) => (
            <button key={report.id} type="button" onClick={() => setSelectedId(report.id)} style={{ ...styles.reportRow, ...(report.id === selectedReport?.id ? styles.selected : undefined) }}>
              <FileText size={18} color={reportColor(report)} />
              <span><strong>{report.id}</strong><small>{report.contract ?? report.artifact_type}</small></span>
            </button>
          ))}
        </div>
      </aside>

      <main style={styles.main}>
        {selectedReport ? (
          <>
            <section style={styles.hero}>
              <Box size={34} color={reportColor(selectedReport)} />
              <div>
                <div style={styles.titleRow}>
                  <h1 style={styles.h1}>{selectedReport.id}</h1>
                  <span style={styles.badge}>{selectedReport.contract ?? selectedReport.artifact_type}</span>
                </div>
                <p style={styles.description}>{selectedReport.path}</p>
              </div>
            </section>
            <ReportRenderer report={selectedReport} />
          </>
        ) : <section style={styles.panel}>No reports found.</section>}
      </main>

      <aside style={styles.inspector}>
        {selectedReport ? (
          <>
            <Header eyebrow="Inspector" title="Artifact metadata" />
            <Fact label="Type" value={String(selectedReport.artifact_type)} />
            <Fact label="Status" value={selectedReport.status} />
            <Fact label="Source export" value={selectedReport.source_export_id ?? "unlinked"} />
            <Fact label="Modified" value={selectedReport.modified_utc ?? "unknown"} />
            <button type="button" onClick={() => onOpenArtifact?.(selectedReport)} style={styles.primaryButton}>Open artifact</button>
          </>
        ) : null}
      </aside>
    </div>
  );
}

function ReportRenderer({ report }: { report: ArtifactMetadata }) {
  const contract = String(report.contract ?? report.artifact_type ?? "");
  if (contract.includes("strategy") || report.artifact_type === "strategy_report") {
    return <StrategyReportRenderer report={report} />;
  }
  if (contract.includes("quality") || report.artifact_type === "quality_report") {
    return <QualityReportRenderer report={report} />;
  }
  if (contract.includes("model") || report.artifact_type === "model_report") {
    return <WeatherModelReportRenderer report={report} />;
  }
  return <GenericArtifactRenderer report={report} />;
}

function WeatherModelReportRenderer({ report }: { report: ArtifactMetadata }) {
  return (
    <div style={styles.rendererGrid}>
      <MetricStrip report={report} labels={["MAE", "RMSE", "CRPS", "Coverage"]} />
      <CommandChart title="Checkpoint Performance" height={280} option={createCommandLineOption(["T-48", "T-36", "T-24", "T-12", "T"], [{ name: "MAE", data: [2.6, 2.3, 2.0, 1.7, 1.42], color: commandPalette.teal }, { name: "RMSE", data: [3.4, 3.0, 2.7, 2.35, 2.13], color: commandPalette.blue }])} />
      <CommandChart title="Calibration Tables" height={260} option={createCommandBarOption(Object.keys(report.table_counts ?? {}).slice(0, 8), numericValues(report.table_counts).slice(0, 8))} />
      <TableInventory report={report} />
    </div>
  );
}

function StrategyReportRenderer({ report }: { report: ArtifactMetadata }) {
  return (
    <div style={styles.rendererGrid}>
      <MetricStrip report={report} labels={["PnL", "ROI", "Hit Rate", "CLV"]} />
      <CommandChart title="Equity Curve" height={280} option={createCommandLineOption(["D1", "D2", "D3", "D4", "D5"], [{ name: "PnL", data: [5, 12, 8, 28, 31], color: commandPalette.teal }], { referenceLines: [{ y: 0, label: "Break even", color: commandPalette.amber }] })} />
      <CommandChart title="Trade Tables" height={260} option={createCommandBarOption(Object.keys(report.table_counts ?? {}).slice(0, 8), numericValues(report.table_counts).slice(0, 8), { colors: [commandPalette.teal, commandPalette.blue, commandPalette.amber] })} />
      <TableInventory report={report} />
    </div>
  );
}

function QualityReportRenderer({ report }: { report: ArtifactMetadata }) {
  const counts = report.table_counts ?? {};
  return (
    <div style={styles.rendererGrid}>
      <MetricStrip report={report} labels={["Completeness", "Freshness", "Schema", "Anomalies"]} />
      <CommandChart title="Quality Signals" height={280} option={createCommandBarOption(Object.keys(counts).slice(0, 10), numericValues(counts).slice(0, 10), { colors: [commandPalette.teal, commandPalette.amber, commandPalette.coral] })} />
      <TableInventory report={report} />
    </div>
  );
}

function GenericArtifactRenderer({ report }: { report: ArtifactMetadata }) {
  return (
    <div style={styles.rendererGrid}>
      <MetricStrip report={report} labels={["Files", "Tables", "Rows", "Status"]} />
      <TableInventory report={report} />
      <SchemaInventory report={report} />
    </div>
  );
}

function MetricStrip({ report, labels }: { report: ArtifactMetadata; labels: string[] }) {
  const summary = report.summary ?? {};
  return <section style={styles.metricStrip}>{labels.map((label) => <Fact key={label} label={label} value={metricValue(summary, label, report)} />)}</section>;
}

function TableInventory({ report }: { report: ArtifactMetadata }) {
  const counts = report.table_counts ?? {};
  return <section style={styles.panel}><Header eyebrow="Tables" title="Report contents" /><div style={styles.table}>{Object.entries(counts).map(([table, count]) => <div key={table} style={styles.tableRow}><Table2 size={15} /><span>{table}</span><strong>{Number(count).toLocaleString()}</strong></div>)}</div></section>;
}

function SchemaInventory({ report }: { report: ArtifactMetadata }) {
  const schemas = (report.schemas ?? {}) as Record<string, ArtifactTableSchema>;
  return <section style={styles.panel}><Header eyebrow="Schemas" title="Generic contract" />{Object.entries(schemas).map(([table, schema]) => <details key={table} style={styles.details}><summary>{table}</summary><p style={styles.description}>{Object.keys(schema.columns ?? {}).join(", ")}</p></details>)}</section>;
}

function Header({ eyebrow, title }: { eyebrow: string; title: string }) {
  return <header style={styles.localHeader}><div style={styles.eyebrow}>{eyebrow}</div><h3 style={styles.h3}>{title}</h3></header>;
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div style={styles.fact}><span>{label}</span><strong>{value}</strong></div>;
}

function metricValue(summary: JsonObject, label: string, report: ArtifactMetadata) {
  const key = Object.keys(summary).find((item) => item.toLowerCase().replace(/[_\s-]/g, "") === label.toLowerCase().replace(/[_\s-]/g, ""));
  if (key) return String(summary[key]);
  if (label === "Rows") return numericValues(report.table_counts).reduce((sum, count) => sum + count, 0).toLocaleString();
  if (label === "Files") return String(report.files?.length ?? 0);
  if (label === "Tables") return String(Object.keys(report.table_counts ?? {}).length);
  if (label === "Status") return report.status;
  return "n/a";
}

function numericValues(record: Record<string, number> | undefined) {
  return Object.values(record ?? {}).map((value) => Number(value)).filter(Number.isFinite);
}

function countBy<T>(items: T[], accessor: (item: T) => string) {
  return items.reduce<Record<string, number>>((acc, item) => {
    const key = accessor(item);
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
}

function reportColor(report: ArtifactMetadata) {
  if (report.artifact_type === "strategy_report") return commandPalette.amber;
  if (report.artifact_type === "quality_report") return commandPalette.coral;
  if (report.artifact_type === "model_report") return commandPalette.teal;
  return commandPalette.blue;
}

const styles: Record<string, CSSProperties> = {
  workspace: { display: "grid", gridTemplateColumns: "minmax(260px, 320px) minmax(520px, 1fr) minmax(260px, 340px)", gap: 16, color: commandPalette.text },
  browser: { borderRight: `1px solid ${commandPalette.border}`, paddingRight: 12 },
  main: { display: "grid", gap: 14, alignContent: "start" },
  inspector: { display: "grid", gap: 10, alignContent: "start", borderLeft: `1px solid ${commandPalette.border}`, paddingLeft: 12 },
  localHeader: { display: "grid", gap: 3, marginBottom: 12 },
  eyebrow: { color: commandPalette.teal, fontSize: 11, fontWeight: 900, letterSpacing: 1.7, textTransform: "uppercase" },
  h1: { margin: 0, fontSize: 24 },
  h3: { margin: 0, fontSize: 15 },
  description: { margin: "5px 0 0", color: commandPalette.muted, lineHeight: 1.45 },
  search: { display: "flex", gap: 8, alignItems: "center", border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: "8px 10px", marginBottom: 10 },
  inputBare: { flex: 1, background: "transparent", border: 0, color: commandPalette.text, outline: "none" },
  input: { width: "100%", background: "#0b1719", color: commandPalette.text, border: `1px solid ${commandPalette.border}`, borderRadius: 6, padding: "8px 10px", marginBottom: 12 },
  list: { display: "grid", gap: 8 },
  reportRow: { display: "grid", gridTemplateColumns: "20px 1fr", gap: 8, color: commandPalette.text, background: commandPalette.panel, border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 10, textAlign: "left", cursor: "pointer" },
  selected: { borderColor: commandPalette.borderStrong, boxShadow: "inset 3px 0 0 #1DD6B7" },
  hero: { display: "grid", gridTemplateColumns: "42px 1fr", gap: 12, border: `1px solid ${commandPalette.border}`, borderRadius: 10, padding: 16, background: "linear-gradient(135deg, rgba(29,214,183,.10), rgba(79,125,255,.05))" },
  titleRow: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  badge: { color: commandPalette.teal, border: `1px solid ${commandPalette.borderStrong}`, borderRadius: 999, padding: "3px 8px", fontSize: 11, fontWeight: 900 },
  rendererGrid: { display: "grid", gap: 14 },
  metricStrip: { display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10 },
  fact: { border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 10, display: "grid", gap: 3, color: commandPalette.muted, background: "rgba(17,24,25,.94)" },
  panel: { border: `1px solid ${commandPalette.border}`, borderRadius: 10, padding: 14, background: "rgba(17,24,25,.94)" },
  table: { display: "grid", gap: 0, border: `1px solid ${commandPalette.border}`, borderRadius: 8, overflow: "hidden" },
  tableRow: { display: "grid", gridTemplateColumns: "18px 1fr auto", gap: 8, alignItems: "center", padding: "9px 10px", color: commandPalette.muted, borderBottom: `1px solid ${commandPalette.border}` },
  details: { border: `1px solid ${commandPalette.border}`, borderRadius: 8, padding: 10, marginTop: 8 },
  primaryButton: { background: commandPalette.teal, color: "#071012", border: 0, borderRadius: 8, padding: "9px 12px", fontWeight: 900, cursor: "pointer" },
};
