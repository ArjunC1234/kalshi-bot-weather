import { Archive, Copy, GitCompareArrows, Rows3, Scissors, SquareArrowOutUpRight } from "lucide-react";
import type { ArtifactMetadata } from "../../types/index";
import { DataTable, type DataTableColumn } from "../../components/tables";
import { StatusBadge } from "../../components/status";
import { artifactDateRange, artifactRows, formatNumber } from "./exportUtils";

export interface ExportInventoryProps {
  exports: ArtifactMetadata[];
  selectedExportId?: string;
  onSelectExport?: (exportId: string) => void;
  onInspectExport?: (exportId: string) => void;
  onCloneExport?: (exportId: string) => void;
  onReduceExport?: (exportId: string) => void;
  onCompareExport?: (exportId: string) => void;
  onArchiveExport?: (exportId: string) => void;
}

export function ExportInventory({
  exports,
  selectedExportId,
  onSelectExport,
  onInspectExport,
  onCloneExport,
  onReduceExport,
  onCompareExport,
  onArchiveExport,
}: ExportInventoryProps) {
  const columns: Array<DataTableColumn<ArtifactMetadata>> = [
    {
      id: "export",
      header: "Export",
      cell: (artifact) => (
        <div className="stacked-cell">
          <strong>{artifact.id}</strong>
          <span>{artifact.path}</span>
        </div>
      ),
      title: (artifact) => artifact.path,
    },
    {
      id: "range",
      header: "Date Range",
      cell: (artifact) => artifactDateRange(artifact),
    },
    {
      id: "cities",
      header: "Cities",
      cell: (artifact) => formatNumber(artifact.coverage?.cities?.length ?? 0),
      align: "end",
    },
    {
      id: "tables",
      header: "Tables",
      cell: (artifact) => formatNumber(Object.keys(artifact.table_counts ?? {}).length),
      align: "end",
    },
    {
      id: "rows",
      header: "Rows",
      cell: (artifact) => formatNumber(artifactRows(artifact)),
      align: "end",
    },
    {
      id: "status",
      header: "Status",
      cell: (artifact) => <StatusBadge status={artifact.status} />,
    },
    {
      id: "modified",
      header: "Modified",
      cell: (artifact) => formatDate(artifact.modified_utc),
    },
    {
      id: "actions",
      header: "Actions",
      cell: (artifact) => (
        <div className="table-actions" onClick={(event) => event.stopPropagation()}>
          <button
            aria-label={`Inspect ${artifact.id}`}
            onClick={() => onInspectExport?.(artifact.id)}
            type="button"
          >
            <SquareArrowOutUpRight size={15} />
          </button>
          <button
            aria-label={`Clone ${artifact.id}`}
            onClick={() => onCloneExport?.(artifact.id)}
            type="button"
          >
            <Copy size={15} />
          </button>
          <button
            aria-label={`Reduce ${artifact.id}`}
            onClick={() => onReduceExport?.(artifact.id)}
            type="button"
          >
            <Scissors size={15} />
          </button>
          <button
            aria-label={`Compare from ${artifact.id}`}
            onClick={() => onCompareExport?.(artifact.id)}
            type="button"
          >
            <GitCompareArrows size={15} />
          </button>
          <button
            aria-label={`Archive ${artifact.id}`}
            onClick={() => onArchiveExport?.(artifact.id)}
            type="button"
          >
            <Archive size={15} />
          </button>
        </div>
      ),
      align: "end",
    },
  ];

  return (
    <section className="panel export-inventory">
      <header className="panel-title-row">
        <div>
          <h3>Existing Exports</h3>
          <p>{formatNumber(exports.length)} local exports discovered</p>
        </div>
        <Rows3 aria-hidden="true" size={20} />
      </header>
      <DataTable
        columns={columns}
        emptyLabel="No local exports were discovered"
        getRowKey={(artifact) => artifact.id}
        onRowClick={onSelectExport ? (artifact) => onSelectExport(artifact.id) : undefined}
        rows={exports}
        selectedRowKey={selectedExportId}
      />
    </section>
  );
}

function formatDate(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
