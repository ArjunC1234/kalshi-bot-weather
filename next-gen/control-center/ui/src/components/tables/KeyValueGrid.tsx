import type { ReactNode } from "react";

export interface KeyValueItem {
  key: string;
  label: ReactNode;
  value: ReactNode;
  title?: string;
}

export interface KeyValueGridProps {
  items: KeyValueItem[];
  emptyLabel?: string;
}

export function KeyValueGrid({
  items,
  emptyLabel = "No metadata available",
}: KeyValueGridProps) {
  if (!items.length) {
    return <p className="muted">{emptyLabel}</p>;
  }

  return (
    <dl className="key-value-grid">
      {items.map((item) => (
        <div className="key-value-grid__row" key={item.key} title={item.title}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
