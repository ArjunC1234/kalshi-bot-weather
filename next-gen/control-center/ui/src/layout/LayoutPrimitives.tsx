import type { CSSProperties, ReactNode } from "react";

export type Density = "comfortable" | "compact";

export type StackProps = {
  children: ReactNode;
  className?: string;
  density?: Density;
};

export type GridProps = StackProps & {
  minColumnWidth?: string;
};

export type PanelProps = StackProps & {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  tone?: "default" | "accent" | "warning" | "danger";
};

export function PageStack({ children, className = "", density = "comfortable" }: StackProps) {
  return (
    <div className={`kbcc-page-stack ${className}`} data-density={density}>
      {children}
    </div>
  );
}

export function ResponsiveGrid({
  children,
  className = "",
  density = "comfortable",
  minColumnWidth = "18rem",
}: GridProps) {
  return (
    <div
      className={`kbcc-responsive-grid ${className}`}
      data-density={density}
      style={{ "--kbcc-grid-min": minColumnWidth } as CSSProperties}
    >
      {children}
    </div>
  );
}

export function SplitPane({ children, className = "", density = "comfortable" }: StackProps) {
  return (
    <div className={`kbcc-split-pane ${className}`} data-density={density}>
      {children}
    </div>
  );
}

export function Panel({
  children,
  className = "",
  density = "comfortable",
  title,
  subtitle,
  actions,
  tone = "default",
}: PanelProps) {
  return (
    <section className={`kbcc-panel ${className}`} data-density={density} data-tone={tone}>
      {title || subtitle || actions ? (
        <header className="kbcc-panel-header">
          <div>
            {title ? <h2>{title}</h2> : null}
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          {actions ? <div className="kbcc-panel-actions">{actions}</div> : null}
        </header>
      ) : null}
      <div className="kbcc-panel-body">{children}</div>
    </section>
  );
}

export function CommandBar({ children, className = "" }: StackProps) {
  return <div className={`kbcc-command-bar ${className}`}>{children}</div>;
}

export function MetricStrip({ children, className = "" }: StackProps) {
  return <div className={`kbcc-metric-strip ${className}`}>{children}</div>;
}
