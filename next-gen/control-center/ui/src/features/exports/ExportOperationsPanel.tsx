import { Archive, Copy, GitCompareArrows, Scissors, StretchHorizontal, Wrench } from "lucide-react";
import { useState } from "react";
import type {
  ArchiveExportRequest,
  ArtifactMetadata,
  CloneExportRequest,
  CompareExportsRequest,
  ExtendExportRequest,
  ReduceExportRequest,
  ValidateExportRequest,
} from "../../types/index";
import { HealthChecklist, StatusBadge, type HealthItem } from "../../components/status";
import { KeyValueGrid } from "../../components/tables";
import { artifactDateRange, artifactRows, formatNumber } from "./exportUtils";

export interface ExportOperationsPanelProps {
  exports: ArtifactMetadata[];
  selectedExport?: ArtifactMetadata | null;
  validation?: { valid: boolean; blocking: string[]; warnings: string[] } | null;
  compareResult?: {
    table_deltas?: Record<string, number>;
    city_delta?: string[];
  } | null;
  busy?: boolean;
  message?: string;
  onValidate?: (request: ValidateExportRequest) => void | Promise<void>;
  onCompare?: (request: CompareExportsRequest) => void | Promise<void>;
  onClone?: (request: CloneExportRequest) => void | Promise<void>;
  onReduce?: (request: ReduceExportRequest) => void | Promise<void>;
  onExtend?: (request: ExtendExportRequest) => void | Promise<void>;
  onArchive?: (request: ArchiveExportRequest) => void | Promise<void>;
}

export function ExportOperationsPanel({
  exports,
  selectedExport,
  validation,
  compareResult,
  busy = false,
  message,
  onValidate,
  onCompare,
  onClone,
  onReduce,
  onExtend,
  onArchive,
}: ExportOperationsPanelProps) {
  const [destination, setDestination] = useState("");
  const [comparisonId, setComparisonId] = useState("");
  const [extensionId, setExtensionId] = useState("");
  const selectedId = selectedExport?.id ?? "";
  const otherExports = exports.filter((artifact) => artifact.id !== selectedId);

  return (
    <aside className="panel export-operations-panel">
      <header className="panel-title-row">
        <div>
          <h3>Export Controls</h3>
          <p>{selectedExport?.id ?? "Select an export to manage"}</p>
        </div>
        {selectedExport ? <StatusBadge status={selectedExport.status} /> : null}
      </header>

      {selectedExport ? (
        <>
          <KeyValueGrid
            items={[
              { key: "path", label: "Path", value: selectedExport.path },
              { key: "range", label: "Range", value: artifactDateRange(selectedExport) },
              {
                key: "cities",
                label: "Cities",
                value: formatNumber(selectedExport.coverage?.cities?.length ?? 0),
              },
              { key: "rows", label: "Rows", value: formatNumber(artifactRows(selectedExport)) },
              {
                key: "manifest",
                label: "Manifest",
                value: selectedExport.metadata_health?.has_manifest ? "Present" : "Missing",
              },
            ]}
          />

          <section className="validation-box">
            <h4>Health</h4>
            <HealthChecklist items={healthItems(selectedExport, validation)} />
          </section>

          <button
            className="secondary wide"
            disabled={busy}
            onClick={() => void onValidate?.({ export_id: selectedId })}
            type="button"
          >
            <Wrench size={15} />
            Validate Export
          </button>

          <label className="form-field">
            <span>Destination</span>
            <input
              onChange={(event) => setDestination(event.target.value)}
              placeholder={`data/${selectedId}_copy`}
              value={destination}
            />
          </label>

          <div className="button-grid">
            <button
              disabled={!destination || busy}
              onClick={() =>
                void onClone?.({ export_id: selectedId, destination })
              }
              type="button"
            >
              <Copy size={15} />
              Clone
            </button>
            <button
              disabled={!destination || busy}
              onClick={() =>
                void onReduce?.({
                  export_id: selectedId,
                  destination,
                  tables: Object.keys(selectedExport.table_counts ?? {}),
                  cities: selectedExport.coverage?.cities,
                  start: selectedExport.coverage?.date_range.start ?? undefined,
                  end: selectedExport.coverage?.date_range.end ?? undefined,
                })
              }
              type="button"
            >
              <Scissors size={15} />
              Reduce
            </button>
          </div>

          <label className="form-field">
            <span>Compare Against</span>
            <select value={comparisonId} onChange={(event) => setComparisonId(event.target.value)}>
              <option value="">Select export</option>
              {otherExports.map((artifact) => (
                <option key={artifact.id} value={artifact.id}>
                  {artifact.id}
                </option>
              ))}
            </select>
          </label>
          <button
            className="secondary wide"
            disabled={!comparisonId || busy}
            onClick={() =>
              void onCompare?.({
                left_export_id: selectedId,
                right_export_id: comparisonId,
              })
            }
            type="button"
          >
            <GitCompareArrows size={15} />
            Compare
          </button>

          {compareResult ? (
            <section className="validation-box">
              <h4>Compare Result</h4>
              <KeyValueGrid
                items={[
                  {
                    key: "tables",
                    label: "Changed tables",
                    value: formatNumber(Object.keys(compareResult.table_deltas ?? {}).length),
                  },
                  {
                    key: "cities",
                    label: "City delta",
                    value: formatNumber(compareResult.city_delta?.length ?? 0),
                  },
                ]}
              />
            </section>
          ) : null}

          <label className="form-field">
            <span>Extend With</span>
            <select value={extensionId} onChange={(event) => setExtensionId(event.target.value)}>
              <option value="">Select extension export</option>
              {otherExports.map((artifact) => (
                <option key={artifact.id} value={artifact.id}>
                  {artifact.id}
                </option>
              ))}
            </select>
          </label>
          <button
            className="secondary wide"
            disabled={!extensionId || !destination || busy}
            onClick={() =>
              void onExtend?.({
                export_id: selectedId,
                extension_export_id: extensionId,
                destination,
              })
            }
            type="button"
          >
            <StretchHorizontal size={15} />
            Extend
          </button>

          <button
            className="danger wide"
            disabled={busy}
            onClick={() => {
              if (window.confirm(`Archive ${selectedId}?`)) {
                void onArchive?.({ export_id: selectedId });
              }
            }}
            type="button"
          >
            <Archive size={15} />
            Archive
          </button>
        </>
      ) : (
        <p className="muted">Select an export to validate, compare, reduce, clone, extend, or archive.</p>
      )}

      {message ? <p className="form-message">{message}</p> : null}
    </aside>
  );
}

function healthItems(
  artifact: ArtifactMetadata,
  validation?: { valid: boolean; blocking: string[]; warnings: string[] } | null,
): HealthItem[] {
  const health = artifact.metadata_health;
  return [
    {
      id: "valid",
      label: "Validation",
      value: validation ? (validation.valid ? "Valid" : "Issues") : "Not run",
      level: validation ? (validation.valid ? "ok" : "critical") : "info",
    },
    {
      id: "manifest",
      label: "Manifest",
      value: health?.has_manifest ? "Present" : "Missing",
      level: health?.has_manifest ? "ok" : "warning",
    },
    {
      id: "schemas",
      label: "Inferred schemas",
      value: health?.has_schemas ? "Present" : "Missing",
      level: health?.has_schemas ? "ok" : "warning",
    },
    {
      id: "blocking",
      label: "Blocking issues",
      value: validation?.blocking.length ?? 0,
      level: validation?.blocking.length ? "critical" : "ok",
    },
    {
      id: "warnings",
      label: "Warnings",
      value: validation?.warnings.length ?? 0,
      level: validation?.warnings.length ? "warning" : "ok",
    },
  ];
}
