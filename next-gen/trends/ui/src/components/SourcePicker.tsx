import { Database, LineChart, RefreshCcw, ShieldCheck, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { SourceInfo, SourcesResponse } from "../types";
import { asText, cleanLabel, formatCurrency, formatDateTime, formatNumber, formatPercent, sourceLabel, titleCase } from "../utils";

type PickerState = {
  exportId: string;
  reportId: string;
  qualityId: string;
  strategyId: string;
};

type SourcePickerProps = {
  sources: SourcesResponse | null;
  value: PickerState;
  loading?: boolean;
  loadingSources?: boolean;
  onChange: (value: PickerState) => void;
  onLoad: () => void;
  onRefresh: () => void;
};

type ParsedModelReport = {
  entrypoint?: string;
  model?: string;
  neuralMode?: string;
  testEnd?: string;
  testStart?: string;
  trainDays?: number;
  trainEnd?: string;
  trainStart?: string;
  testDays?: number;
};

type PickerStep = "dataset" | "model" | "strategy" | "quality";

export function SourcePicker({
  sources,
  value,
  loading,
  loadingSources,
  onChange,
  onLoad,
  onRefresh,
}: SourcePickerProps) {
  const sourcePending = loadingSources || sources === null;
  const exportSource = sources?.exports.find((item) => item.id === value.exportId);
  const linkedReports = linkedToExport(sources?.reports ?? [], value.exportId);
  const linkedQuality = linkedToExport(sources?.quality_reports ?? [], value.exportId);
  const linkedStrategies = linkedToExport(sources?.strategy_reports ?? [], value.exportId);
  const selectedReport = linkedReports.find((item) => item.id === value.reportId);
  const modelStrategies = strategiesForModel(linkedStrategies, selectedReport, linkedReports);
  const selectedQuality = linkedQuality.find((item) => item.id === value.qualityId);
  const selectedStrategy = modelStrategies.find((item) => item.id === value.strategyId);
  const selectedStrategyModel = inferredModelReport(selectedStrategy, linkedReports);
  const [activeStep, setActiveStep] = useState<PickerStep>("dataset");

  useEffect(() => {
    if (!value.exportId && activeStep !== "dataset") setActiveStep("dataset");
  }, [activeStep, value.exportId]);

  const steps: Array<{
    key: PickerStep;
    label: string;
    detail: string;
    icon: ReactNode;
    count: number;
    selected: string;
    disabled?: boolean;
  }> = [
    {
      key: "dataset",
      label: "Dataset",
      detail: "Frozen export",
      icon: <Database aria-hidden="true" size={17} />,
      count: sources?.exports.length ?? 0,
      selected: sourceLabel(exportSource),
    },
    {
      key: "model",
      label: "Model",
      detail: "Probability report",
      icon: <LineChart aria-hidden="true" size={17} />,
      count: linkedReports.length,
      selected: sourceLabel(selectedReport) || "No model report",
      disabled: !value.exportId,
    },
    {
      key: "strategy",
      label: "Strategy",
      detail: "Backtest report",
      icon: <TrendingUp aria-hidden="true" size={17} />,
      count: modelStrategies.length,
      selected: sourceLabel(selectedStrategy) || "No strategy report",
      disabled: !value.exportId || !selectedReport,
    },
    {
      key: "quality",
      label: "Quality",
      detail: "Optional checks",
      icon: <ShieldCheck aria-hidden="true" size={17} />,
      count: linkedQuality.length,
      selected: sourceLabel(selectedQuality) || "No quality report",
      disabled: !value.exportId,
    },
  ];

  return (
    <section className="source-picker">
      <header className="source-picker-head">
        <div>
          <p className="eyebrow">Source Hub</p>
          <h2>Load a workbench</h2>
          <p>
            Choose one frozen export, then attach model, quality, and strategy reports when
            available.
          </p>
        </div>
        <button
          className="icon-button ghost"
          disabled={loadingSources}
          type="button"
          onClick={onRefresh}
          title={loadingSources ? "Scanning sources" : "Refresh sources"}
        >
          <RefreshCcw aria-hidden="true" size={18} />
        </button>
      </header>

      <div className="source-stepper" role="tablist" aria-label="Source selection steps">
        {steps.map((step, index) => (
          <button
            className={`source-step ${activeStep === step.key ? "active" : ""} ${step.selected && step.selected !== "No model report" && step.selected !== "No strategy report" && step.selected !== "No quality report" ? "complete" : ""}`}
            disabled={step.disabled}
            key={step.key}
            type="button"
            onClick={() => setActiveStep(step.key)}
          >
            <span className="source-step-index">{index + 1}</span>
            <span className="source-step-copy">
              <strong>{step.label}</strong>
              <small>{step.detail}</small>
            </span>
            <span className="source-step-count">{formatNumber(step.count)}</span>
          </button>
        ))}
      </div>

      <div className="source-picker-flow">
        <aside className="source-selection-summary">
          {steps.map((step) => (
            <button
              className={`source-summary-row ${activeStep === step.key ? "active" : ""}`}
              disabled={step.disabled}
              key={step.key}
              type="button"
              onClick={() => setActiveStep(step.key)}
            >
              {step.icon}
              <span>
                <strong>{step.label}</strong>
                <small>{step.selected || "None selected"}</small>
              </span>
            </button>
          ))}
          {selectedStrategy ? (
            <div className="source-linkage-card">
              <span>Strategy model</span>
              <strong>{strategyModelLabel(selectedStrategy, linkedReports)}</strong>
            </div>
          ) : null}
        </aside>

        <div className="source-step-panel">
          {activeStep === "dataset" ? (
            <SourceColumn
              title="Dataset"
              icon={<Database aria-hidden="true" size={18} />}
              items={sources?.exports ?? []}
              selectedId={value.exportId}
              empty={sourcePending ? "Scanning export folders..." : "No exports found."}
              onSelect={(exportId) => {
                const nextReports = linkedToExport(sources?.reports ?? [], exportId);
                const nextStrategies = linkedToExport(sources?.strategy_reports ?? [], exportId);
                const strategy = nextStrategies[0];
                const reportId = matchingModelReportId(strategy, nextReports) || nextReports[0]?.id || "";
                const report = nextReports.find((item) => item.id === reportId);
                const strategyId = strategiesForModel(nextStrategies, report, nextReports)[0]?.id ?? "";
                onChange({
                  exportId,
                  reportId,
                  qualityId: linkedToExport(sources?.quality_reports ?? [], exportId)[0]?.id ?? "",
                  strategyId,
                });
                setActiveStep("model");
              }}
              renderMeta={(item) => (
                <>
                  <span>{item.date_start ?? "?"} to {item.date_end ?? "?"}</span>
                  <span>{formatNumber(item.file_count)} files</span>
                </>
              )}
            />
          ) : null}
          {activeStep === "model" ? (
            <SourceColumn
              title="Model"
              icon={<LineChart aria-hidden="true" size={18} />}
              summary={associationSummary(linkedReports, value.exportId)}
              items={[emptyReport("No model report"), ...selectedFirst(linkedReports, value.reportId)]}
              selectedId={value.reportId}
              empty="No linked model reports."
              onSelect={(reportId) => {
                const report = linkedReports.find((item) => item.id === reportId);
                const nextStrategyId = strategiesForModel(linkedStrategies, report, linkedReports)[0]?.id ?? "";
                onChange({ ...value, reportId, strategyId: nextStrategyId });
                setActiveStep("strategy");
              }}
              renderTitle={modelReportTitle}
              renderSubtitle={modelReportSubtitle}
              renderMeta={(item) =>
                item.id ? (
                  <ModelReportMeta item={item} selectedStrategyModelId={selectedStrategyModel?.id} />
                ) : (
                  <span>Weather, market, and quality only</span>
                )
              }
            />
          ) : null}
          {activeStep === "strategy" ? (
            <SourceColumn
              title="Strategy"
              icon={<TrendingUp aria-hidden="true" size={18} />}
              summary={strategyAssociationSummary(modelStrategies, selectedReport)}
              items={selectedReport ? [emptyReport("No strategy report"), ...modelStrategies] : []}
              selectedId={value.strategyId}
              empty={selectedReport ? "No strategy reports use this model." : "Select a model report first."}
              onSelect={(strategyId) => {
                const strategy = modelStrategies.find((item) => item.id === strategyId);
                const reportId = matchingModelReportId(strategy, linkedReports);
                onChange({ ...value, strategyId, reportId: reportId || value.reportId });
                setActiveStep("quality");
              }}
              renderTitle={strategyReportTitle}
              renderSubtitle={(item) => strategyReportSubtitle(item, linkedReports)}
              renderMeta={(item) =>
                item.id ? (
                  <>
                    <span>{formatCurrency(item.total_pnl)}</span>
                    <span>{formatPercent(item.roi)} ROI</span>
                    <span>Model: {strategyModelLabel(item, linkedReports)}</span>
                    <span>Export: {associationLabel(item)}</span>
                  </>
                ) : (
                  <span>Skip strategy lab</span>
                )
              }
            />
          ) : null}
          {activeStep === "quality" ? (
            <SourceColumn
              title="Quality"
              icon={<ShieldCheck aria-hidden="true" size={18} />}
              summary={associationSummary(linkedQuality, value.exportId)}
              items={[emptyReport("No quality report"), ...linkedQuality]}
              selectedId={value.qualityId}
              empty="No linked quality reports."
              onSelect={(qualityId) => onChange({ ...value, qualityId })}
              renderMeta={(item) =>
                item.id ? (
                  <>
                    <span>{formatNumber(item.file_count)} files</span>
                    <span>{formatDateTime(item.created_utc || item.modified_utc)}</span>
                    <span>{associationLabel(item)}</span>
                  </>
                ) : (
                  <span>Skip quality overlays</span>
                )
              }
            />
          ) : null}
        </div>
      </div>

      <footer className="source-picker-footer">
        <div>
          <span>Selected dataset</span>
          <strong>{sourceLabel(exportSource)}</strong>
        </div>
        <button type="button" disabled={!value.exportId || loading} onClick={onLoad}>
          {loading ? "Loading..." : "Load Workbench"}
        </button>
      </footer>
    </section>
  );
}

type SourceColumnProps = {
  title: string;
  icon: ReactNode;
  summary?: string;
  items: SourceInfo[];
  selectedId: string;
  empty: string;
  onSelect: (id: string) => void;
  renderSubtitle?: (item: SourceInfo) => ReactNode;
  renderTitle?: (item: SourceInfo) => ReactNode;
  renderMeta: (item: SourceInfo) => ReactNode;
};

function SourceColumn({
  title,
  icon,
  summary,
  items,
  selectedId,
  empty,
  onSelect,
  renderSubtitle,
  renderTitle,
  renderMeta,
}: SourceColumnProps) {
  return (
    <section className="source-column">
      <h3>
        {icon}
        {title}
      </h3>
      {summary ? <p className="source-column-summary">{summary}</p> : null}
      <div className="source-list">
        {items.length ? (
          items.map((item) => (
            <button
              type="button"
              key={item.id || `empty-${title}`}
              className={`source-row ${selectedId === item.id ? "active" : ""}`}
              onClick={() => onSelect(item.id)}
            >
              <span>
                <strong>{renderTitle ? renderTitle(item) : sourceLabel(item)}</strong>
                <small>{renderSubtitle ? renderSubtitle(item) : item.id || item.name}</small>
              </span>
              <em>{renderMeta(item)}</em>
            </button>
          ))
        ) : (
          <div className="empty-panel">{empty}</div>
        )}
      </div>
    </section>
  );
}

function ModelReportMeta({
  item,
  selectedStrategyModelId,
}: {
  item: SourceInfo;
  selectedStrategyModelId?: string;
}) {
  const info = modelReportInfo(item);
  const predictionCount = item.bracket_prediction_count ?? item.temperature_prediction_count;
  const hasMetrics = item.mae !== undefined || item.log_loss !== undefined || item.top_one_accuracy !== undefined;
  const usedBySelectedStrategy = selectedStrategyModelId === item.id;
  return (
    <>
      <span className="source-chip-row">
        {usedBySelectedStrategy ? <b className="linked">Used by selected strategy</b> : null}
        <b className={info.awarenessClass}>{info.awareness}</b>
        {info.entrypoint ? <b>{titleCase(info.entrypoint)}</b> : null}
        {info.window ? <b>{info.window}</b> : null}
      </span>
      <span>Test: {dateRange(info.testStart, info.testEnd)}</span>
      <span>Train: {dateRange(info.trainStart, info.trainEnd)}</span>
      <span>Created: {formatDateTime(item.created_utc || item.modified_utc)}</span>
      <span>
        Predictions: {predictionCount === undefined ? "n/a" : formatNumber(predictionCount)}
        {item.independent_city_days ? ` | city-days ${formatNumber(item.independent_city_days)}` : ""}
      </span>
      {hasMetrics ? (
        <span>
          MAE {formatNumber(item.mae)} | log loss {formatNumber(item.log_loss)} | top-1 {formatPercent(item.top_one_accuracy)}
        </span>
      ) : (
        <span className="source-warning">Metadata incomplete: refresh/restart Source Hub scan.</span>
      )}
      <span>Export: {associationLabel(item)}</span>
    </>
  );
}

function strategyReportTitle(item: SourceInfo): ReactNode {
  if (!item.id) return item.name;
  return cleanLabel(item.id);
}

function strategyReportSubtitle(item: SourceInfo, reports: SourceInfo[] = []): ReactNode {
  if (!item.id) return item.name;
  return (
    <>
      <span>{item.id}</span>
      <span className="source-subline">Uses model: {strategyModelLabel(item, reports)}</span>
    </>
  );
}

function modelReportTitle(item: SourceInfo): ReactNode {
  if (!item.id) return item.name;
  const info = modelReportInfo(item);
  return [info.model, info.awareness, info.entrypoint ? titleCase(info.entrypoint) : "", info.window].filter(Boolean).join(" - ");
}

function modelReportSubtitle(item: SourceInfo): ReactNode {
  if (!item.id) return item.name;
  const info = modelReportInfo(item);
  return (
    <>
      <span>{item.id}</span>
      <span className="source-subline">
        {info.mode ? cleanLabel(info.mode) : "mode unknown"}
        {item.market_probability_blend !== undefined ? ` | market blend ${formatNumber(item.market_probability_blend)}` : ""}
        {item.seed !== undefined ? ` | seed ${item.seed}` : ""}
      </span>
    </>
  );
}

function linkedToExport(items: SourceInfo[], exportId: string): SourceInfo[] {
  if (!exportId) return items.slice(0, 20);
  return items.filter((item) => item.source_export_id === exportId);
}

function strategiesForModel(strategies: SourceInfo[], report: SourceInfo | undefined, reports: SourceInfo[]): SourceInfo[] {
  if (!report?.id) return [];
  return strategies.filter((strategy) => inferredModelReport(strategy, reports)?.id === report.id);
}

function strategyAssociationSummary(items: SourceInfo[], report: SourceInfo | undefined): string {
  if (!report?.id) return "Choose a model first";
  if (!items.length) return "0 use selected model";
  return `${items.length} use selected model`;
}

function selectedFirst(items: SourceInfo[], selectedId: string): SourceInfo[] {
  if (!selectedId) return items;
  const selected = items.find((item) => item.id === selectedId);
  if (!selected) return items;
  return [selected, ...items.filter((item) => item.id !== selectedId)];
}

function matchingModelReportId(strategy: SourceInfo | undefined, reports: SourceInfo[]): string {
  return inferredModelReport(strategy, reports)?.id ?? "";
}

function strategyModelLabel(item: SourceInfo, reports: SourceInfo[] = []): string {
  const inferred = inferredModelReport(item, reports);
  return item.model_report_name || inferred?.name || item.model_report_id || inferred?.id || "No model report recorded";
}

function inferredModelReport(strategy: SourceInfo | undefined, reports: SourceInfo[]): SourceInfo | undefined {
  if (!strategy?.id) return undefined;
  if (strategy.model_report_id) {
    const direct = reports.find((report) => report.id === strategy.model_report_id);
    if (direct) return direct;
  }
  const modelReportPathId = pathLeaf(strategy.model_report_path);
  if (modelReportPathId) {
    const direct = reports.find((report) => report.id === modelReportPathId || pathLeaf(report.path) === modelReportPathId);
    if (direct) return direct;
  }
  const prefix = inferredModelPrefix(strategy.id);
  if (!prefix) return undefined;
  const sameExport = reports.filter((report) => !strategy.source_export_id || report.source_export_id === strategy.source_export_id);
  return sameExport.find((report) => report.id.startsWith(`${prefix}_`) || report.id === prefix);
}

function inferredModelPrefix(strategyId: string): string {
  const lowered = strategyId.toLowerCase();
  for (const marker of ["_ev_validation_train_", "_ev_train_", "_fixed_train_", "_validation_train_"]) {
    const index = lowered.indexOf(marker);
    if (index > 0) return strategyId.slice(0, index);
  }
  return "";
}

function pathLeaf(value?: string): string {
  if (!value) return "";
  return value.replaceAll("\\", "/").replace(/\/+$/, "").split("/").pop() ?? "";
}

function associationSummary(items: SourceInfo[], exportId: string): string {
  if (!exportId) return "Choose a dataset first";
  if (!items.length) return "0 associated";
  const inferred = items.filter((item) => item.source_export_inferred).length;
  return inferred ? `${items.length} associated, ${inferred} inferred` : `${items.length} associated`;
}

function associationLabel(item: SourceInfo): string {
  if (!item.source_export_id) return "Not linked";
  return item.source_export_inferred ? "Linked by folder name" : "Linked";
}

function emptyReport(name: string): SourceInfo {
  return {
    id: "",
    name,
    kind: "optional",
    modified_utc: "",
    files: [],
    file_count: 0,
  };
}

function modelReportInfo(item: SourceInfo) {
  const mode = asText(item.mode || item.name || item.id);
  const parsed = parseModelReportText(`${item.id} ${item.name} ${mode}`);
  const neuralMode = asText(item.neural_mode || parsed.neuralMode).toLowerCase();
  const awareness = modelAwarenessLabel(item, neuralMode, mode);
  const trainDays = item.train_days ?? parsed.trainDays;
  const testDays = item.test_days ?? parsed.testDays;
  return {
    awareness,
    awarenessClass: modelAwarenessClass(awareness),
    entrypoint: item.entrypoint || parsed.entrypoint || reportKindFromMode(mode),
    mode,
    model: titleCase(item.registry_id || item.model_name || parsed.model || "model"),
    testEnd: item.test_end_date || parsed.testEnd,
    testStart: item.test_start_date || parsed.testStart,
    trainEnd: item.train_end_date || parsed.trainEnd,
    trainStart: item.train_start_date || parsed.trainStart,
    window: formatWindowDays(trainDays, testDays),
  };
}

function parseModelReportText(value: string): ParsedModelReport {
  const lower = value.toLowerCase();
  const output: ParsedModelReport = {};
  const model = lower.match(/\b(neuralcaster_v\d+|raycaster_v\d+|edgecaster_v\d+)\b/);
  if (model) output.model = model[1];
  if (lower.includes("market")) output.neuralMode = "market";
  else if (lower.includes("weather")) output.neuralMode = "weather";
  if (lower.includes("rolling")) output.entrypoint = "rolling_eval";
  else if (lower.includes("fixed")) output.entrypoint = "fixed_window";
  const dayWindow = lower.match(/(\d+)d[\s_]*train[\s_]*(\d+)d[\s_]*test/);
  if (dayWindow) {
    output.trainDays = Number(dayWindow[1]);
    output.testDays = Number(dayWindow[2]);
  }
  const fixedWindow = lower.match(/train[_\s-]*(\d{4}-\d{2}-\d{2})[_\s-]*(\d{4}-\d{2}-\d{2})[_\s-]*test[_\s-]*(\d{4}-\d{2}-\d{2})[_\s-]*(\d{4}-\d{2}-\d{2})/);
  if (fixedWindow) {
    output.trainStart = fixedWindow[1];
    output.trainEnd = fixedWindow[2];
    output.testStart = fixedWindow[3];
    output.testEnd = fixedWindow[4];
  }
  return output;
}

function modelAwarenessLabel(item: SourceInfo, neuralMode = asText(item.neural_mode).toLowerCase(), mode = asText(item.mode).toLowerCase()): string {
  const blend = Number(item.market_probability_blend ?? 0);
  const normalizedMode = mode.toLowerCase();
  if (neuralMode === "market" || normalizedMode.includes("market") || blend > 0) return "MARKET AWARE";
  if (neuralMode === "weather" || normalizedMode.includes("weather")) return "WEATHER ONLY";
  return "MODE UNKNOWN";
}

function modelAwarenessClass(label: string): string {
  if (label.includes("MARKET")) return "market-aware";
  if (label.includes("WEATHER")) return "weather-only";
  return "unknown";
}

function formatWindowDays(trainDays?: number, testDays?: number): string {
  if (trainDays && testDays) return `${trainDays}d train / ${testDays}d test`;
  if (trainDays) return `${trainDays}d train`;
  if (testDays) return `${testDays}d test`;
  return "";
}

function reportKindFromMode(value: unknown): string {
  const mode = asText(value).toLowerCase();
  if (mode.includes("rolling")) return "Rolling Evaluation";
  if (mode.includes("fixed")) return "Fixed Window";
  return "";
}

function dateRange(start?: string, end?: string): string {
  if (!start && !end) return "unknown";
  return `${start ?? "?"} to ${end ?? "?"}`;
}
