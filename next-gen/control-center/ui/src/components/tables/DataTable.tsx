import type { ReactNode } from "react";

export interface DataTableColumn<Row> {
  id: string;
  header: ReactNode;
  cell: (row: Row) => ReactNode;
  align?: "start" | "center" | "end";
  className?: string;
  title?: (row: Row) => string | undefined;
}

export interface DataTableProps<Row> {
  rows: Row[];
  columns: Array<DataTableColumn<Row>>;
  getRowKey: (row: Row, index: number) => string;
  emptyLabel?: string;
  className?: string;
  selectedRowKey?: string;
  onRowClick?: (row: Row) => void;
}

export function DataTable<Row>({
  rows,
  columns,
  getRowKey,
  emptyLabel = "No rows available",
  className,
  selectedRowKey,
  onRowClick,
}: DataTableProps<Row>) {
  return (
    <div className={`data-table-wrap ${className ?? ""}`.trim()}>
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                className={column.className}
                data-align={column.align ?? "start"}
                key={column.id}
                scope="col"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((row, index) => {
              const rowKey = getRowKey(row, index);
              return (
                <tr
                  className={rowKey === selectedRowKey ? "selected" : undefined}
                  key={rowKey}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                >
                  {columns.map((column) => (
                    <td
                      className={column.className}
                      data-align={column.align ?? "start"}
                      key={column.id}
                      title={column.title?.(row)}
                    >
                      {column.cell(row)}
                    </td>
                  ))}
                </tr>
              );
            })
          ) : (
            <tr>
              <td className="empty-cell" colSpan={columns.length}>
                {emptyLabel}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
