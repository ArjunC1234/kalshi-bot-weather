import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDownUp, Download, Search } from "lucide-react";
import { useDeferredValue, useMemo, useState, type ReactNode } from "react";
import type { DataRow } from "../types";
import { asText, formatNumber, titleCase } from "../utils";

type DataTableProps = {
  title: string;
  rows: DataRow[];
  preferredColumns?: string[];
  maxRows?: number;
  onRowSelect?: (row: DataRow) => void;
  selectedRowKey?: string;
  selectedRowValue?: unknown;
};

const MAX_RENDER_ROWS = 5_000;
const SEARCH_SCAN_LIMIT = 50_000;

export function DataTable({
  title,
  rows,
  preferredColumns,
  maxRows = 300,
  onRowSelect,
  selectedRowKey,
  selectedRowValue,
}: DataTableProps) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [query, setQuery] = useState("");
  const [rowLimit, setRowLimit] = useState<number | "all">(maxRows);
  const deferredQuery = useDeferredValue(query);
  const canRenderAll = rows.length <= MAX_RENDER_ROWS;
  const displayLimit = rowLimit === "all" ? (canRenderAll ? rows.length : MAX_RENDER_ROWS) : Math.min(rowLimit, MAX_RENDER_ROWS);
  const searchRowLimit = Math.min(rows.length, SEARCH_SCAN_LIMIT);
  const columnIds = useMemo(() => columnKeys(rows, preferredColumns), [preferredColumns, rows]);
  const columns = useMemo<ColumnDef<DataRow>[]>(() => {
    return columnIds.map((key) => ({
      accessorKey: key,
      header: titleCase(key),
      cell: (info) => <CellValue value={info.getValue()} />,
    }));
  }, [columnIds]);
  const exportRows = useMemo(() => {
    const normalized = deferredQuery.trim().toLowerCase();
    const base = normalized
      ? rows.slice(0, searchRowLimit).filter((row) =>
          Object.values(row).some((value) => searchableText(value).toLowerCase().includes(normalized)),
        )
      : rows;
    return sortRows(base, sorting);
  }, [deferredQuery, rows, searchRowLimit, sorting]);
  const filteredRows = useMemo(() => exportRows.slice(0, displayLimit), [displayLimit, exportRows]);
  const searchLimited = Boolean(deferredQuery.trim()) && rows.length > searchRowLimit;
  const renderLimited = rows.length > displayLimit || filteredRows.length === displayLimit;
  const selectValue = rowLimit === "all" && !canRenderAll ? MAX_RENDER_ROWS : rowLimit;
  const table = useReactTable({
    data: filteredRows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <section className="table-panel">
      <header className="panel-head table-head">
        <div>
          <h3>{title}</h3>
          <p>
            {filteredRows.length.toLocaleString()} of {rows.length.toLocaleString()} rows
            {searchLimited ? `, searched first ${searchRowLimit.toLocaleString()}` : ""}
            {renderLimited && rows.length > filteredRows.length ? ", capped for responsiveness" : ""}
          </p>
        </div>
        <div className="table-actions">
          <label className="search-control">
            <Search aria-hidden="true" size={16} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label={`Search ${title} rows`}
              placeholder="Search rows"
              spellCheck={false}
            />
          </label>
          <label className="row-limit-control">
            Rows
            <select
              value={selectValue}
              onChange={(event) => {
                const value = event.target.value;
                setRowLimit(value === "all" ? "all" : Number(value));
              }}
            >
              {[300, 1_000, MAX_RENDER_ROWS].map((limit) => (
                <option key={limit} value={limit}>
                  {limit.toLocaleString()}
                </option>
              ))}
              {canRenderAll ? <option value="all">All</option> : null}
            </select>
          </label>
          <button
            className="ghost icon-text-button"
            disabled={!exportRows.length}
            type="button"
            onClick={() => downloadCsv(title, exportRows, columnIds)}
          >
            <Download aria-hidden="true" size={15} />
            CSV
          </button>
        </div>
      </header>
      {rows.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id}>
                  {headerGroup.headers.map((header) => (
                    <th key={header.id}>
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="th-button"
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <ArrowDownUp aria-hidden="true" size={12} />
                      </button>
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => {
                const selected = selectedRowKey ? asText(row.original[selectedRowKey]) === asText(selectedRowValue) : false;
                return (
                <tr
                  className={`${onRowSelect ? "selectable-row" : ""} ${selected ? "selected-row" : ""}`}
                  key={row.id}
                  onClick={onRowSelect ? () => onRowSelect(row.original) : undefined}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-panel">No rows available for this table.</div>
      )}
    </section>
  );
}

function downloadCsv(title: string, rows: DataRow[], keys: string[]): void {
  if (!rows.length) return;
  const resolvedKeys = keys.length ? keys : columnKeys(rows);
  const csv = [
    resolvedKeys.map(csvCell).join(","),
    ...rows.map((row) => resolvedKeys.map((key) => csvCell(row[key])).join(",")),
  ].join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${fileSlug(title)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function fileSlug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "table";
}

function columnKeys(rows: DataRow[], preferredColumns?: string[]): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const key of preferredColumns ?? []) {
    if (!seen.has(key)) {
      ordered.push(key);
      seen.add(key);
    }
  }
  for (const row of rows.slice(0, 40)) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        ordered.push(key);
        seen.add(key);
      }
    }
  }
  return ordered.slice(0, 18);
}

function sortRows(rows: DataRow[], sorting: SortingState): DataRow[] {
  const [sort] = sorting;
  if (!sort) return rows;
  return [...rows].sort((left, right) => compareValues(left[sort.id], right[sort.id]) * (sort.desc ? -1 : 1));
}

function compareValues(left: unknown, right: unknown): number {
  const leftNumber = typeof left === "number" ? left : Number(left);
  const rightNumber = typeof right === "number" ? right : Number(right);
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber;
  return asText(left).localeCompare(asText(right), undefined, { numeric: true, sensitivity: "base" });
}

function CellValue({ value }: { value: unknown }) {
  const formatted = formatCell(value);
  if (formatted.kind === "empty") return null;
  if (formatted.kind === "simple") return <span title={formatted.title}>{formatted.preview}</span>;
  return (
    <details className="cell-details">
      <summary title={formatted.title}>{formatted.preview}</summary>
      <pre>{formatted.full}</pre>
    </details>
  );
}

function formatCell(value: unknown): { kind: "empty" } | { kind: "simple"; preview: string; title: string } | { kind: "details"; preview: ReactNode; title: string; full: string } {
  if (value === null || value === undefined || value === "") return { kind: "empty" };
  if (typeof value === "number") {
    const text = formatNumber(value);
    return { kind: "simple", preview: text, title: String(value) };
  }
  if (typeof value === "boolean") {
    const text = value ? "yes" : "no";
    return { kind: "simple", preview: text, title: text };
  }
  const text = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  const isStructured = looksStructured(text);
  if (!isStructured && text.length <= 140) return { kind: "simple", preview: text, title: text };
  const preview = isStructured ? structuredPreview(text) : compactLongText(text);
  return { kind: "details", preview, title: "Expand full value", full: text };
}

function searchableText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return asText(value);
}

function looksStructured(text: string): boolean {
  const trimmed = text.trim();
  return (
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith("[") && trimmed.endsWith("]")) ||
    /['"]?[A-Z0-9-]+['"]?\s*:/.test(trimmed)
  );
}

function structuredPreview(text: string): ReactNode {
  const matches = [...text.matchAll(/['"]?([^'",:{}[\]]+)['"]?\s*:\s*([^,{}[\]]+)/g)]
    .slice(0, 3)
    .map((match) => ({
      key: match[1].trim(),
      value: match[2].trim().replace(/^['"]|['"]$/g, ""),
    }));
  if (!matches.length) return `${text.slice(0, 137)}...`;
  return (
    <span className="cell-kv-preview">
      {matches.map((item) => (
        <span key={`${item.key}-${item.value}`}>
          <b>{item.key}</b> {item.value}
        </span>
      ))}
      <i>more</i>
    </span>
  );
}

function compactLongText(text: string): string {
  if (text.length <= 140) return text;
  const pathParts = text.split(/[\\/]/).filter(Boolean);
  if (pathParts.length >= 3) return `.../${pathParts.slice(-3).join("/")}`;
  return `${text.slice(0, 137)}...`;
}
