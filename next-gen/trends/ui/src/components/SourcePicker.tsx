import { Database, LineChart, RefreshCcw, ShieldCheck, TrendingUp } from "lucide-react";
import type { ReactNode } from "react";
import type { SourceInfo, SourcesResponse } from "../types";
import { formatCurrency, formatDateTime, formatNumber, formatPercent, sourceLabel } from "../utils";

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

      <div className="source-picker-grid">
        <SourceColumn
          title="Dataset"
          icon={<Database aria-hidden="true" size={18} />}
          items={sources?.exports ?? []}
          selectedId={value.exportId}
          empty={sourcePending ? "Scanning export folders..." : "No exports found."}
          onSelect={(exportId) =>
            onChange({
              exportId,
              reportId: "",
              qualityId: linkedToExport(sources?.quality_reports ?? [], exportId)[0]?.id ?? "",
              strategyId: "",
            })
          }
          renderMeta={(item) => (
            <>
              <span>{item.date_start ?? "?"} to {item.date_end ?? "?"}</span>
              <span>{formatNumber(item.file_count)} files</span>
            </>
          )}
        />
        <SourceColumn
          title="Model"
          icon={<LineChart aria-hidden="true" size={18} />}
          items={[emptyReport("No model report"), ...linkedReports]}
          selectedId={value.reportId}
          empty="No linked model reports."
          onSelect={(reportId) => onChange({ ...value, reportId })}
          renderMeta={(item) =>
            item.id ? (
              <>
                <span>{item.model_name ?? item.mode ?? item.name}</span>
                <span>{formatDateTime(item.created_utc || item.modified_utc)}</span>
              </>
            ) : (
              <span>Weather, market, and quality only</span>
            )
          }
        />
        <SourceColumn
          title="Quality"
          icon={<ShieldCheck aria-hidden="true" size={18} />}
          items={[emptyReport("No quality report"), ...linkedQuality]}
          selectedId={value.qualityId}
          empty="No linked quality reports."
          onSelect={(qualityId) => onChange({ ...value, qualityId })}
          renderMeta={(item) =>
            item.id ? (
              <>
                <span>{formatNumber(item.file_count)} files</span>
                <span>{formatDateTime(item.created_utc || item.modified_utc)}</span>
              </>
            ) : (
              <span>Skip quality overlays</span>
            )
          }
        />
        <SourceColumn
          title="Strategy"
          icon={<TrendingUp aria-hidden="true" size={18} />}
          items={[emptyReport("No strategy report"), ...linkedStrategies]}
          selectedId={value.strategyId}
          empty="No linked strategy reports."
          onSelect={(strategyId) => onChange({ ...value, strategyId })}
          renderMeta={(item) =>
            item.id ? (
              <>
                <span>{formatCurrency(item.total_pnl)}</span>
                <span>{formatPercent(item.roi)} ROI</span>
              </>
            ) : (
              <span>Skip strategy lab</span>
            )
          }
        />
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
  items: SourceInfo[];
  selectedId: string;
  empty: string;
  onSelect: (id: string) => void;
  renderMeta: (item: SourceInfo) => ReactNode;
};

function SourceColumn({
  title,
  icon,
  items,
  selectedId,
  empty,
  onSelect,
  renderMeta,
}: SourceColumnProps) {
  return (
    <section className="source-column">
      <h3>
        {icon}
        {title}
      </h3>
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
                <strong>{sourceLabel(item)}</strong>
                <small>{item.id || item.name}</small>
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

function linkedToExport(items: SourceInfo[], exportId: string): SourceInfo[] {
  if (!exportId) return items.slice(0, 20);
  const linked = items.filter((item) => item.source_export_id === exportId);
  return linked.length ? linked : items.slice(0, 12);
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
