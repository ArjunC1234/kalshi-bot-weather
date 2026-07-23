import type { ChartPanelProps } from "./types";
import { EChartsView } from "./EChartsView";
import "./visualization.css";

export function ChartPanel({
  title,
  subtitle,
  eyebrow,
  option,
  height = 360,
  loading,
  error,
  empty,
  emptyMessage = "No data for this selection.",
  densityLabel,
  warning,
  actions,
  legend,
  children,
  className,
}: ChartPanelProps) {
  return (
    <section className={["kbc-chart-panel", className].filter(Boolean).join(" ")}>
      <header className="kbc-chart-panel__header">
        <div className="kbc-chart-panel__title-block">
          {eyebrow ? <div className="kbc-chart-panel__eyebrow">{eyebrow}</div> : null}
          <h3 className="kbc-chart-panel__title">{title}</h3>
          {subtitle ? <p className="kbc-chart-panel__subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div className="kbc-chart-panel__actions">{actions}</div> : null}
      </header>

      {(legend?.length || densityLabel || warning) ? (
        <div className="kbc-chart-panel__meta">
          {legend?.length ? (
            <div className="kbc-chart-legend" aria-label="Chart legend">
              {legend.map((item) => (
                <span
                  key={item.id}
                  className={item.muted ? "kbc-chart-legend__item is-muted" : "kbc-chart-legend__item"}
                >
                  <span
                    className="kbc-chart-legend__swatch"
                    style={{ backgroundColor: item.color }}
                    aria-hidden="true"
                  />
                  {item.label}
                </span>
              ))}
            </div>
          ) : null}
          {densityLabel ? <span className="kbc-chart-panel__density">{densityLabel}</span> : null}
          {warning ? <span className="kbc-chart-panel__warning">{warning}</span> : null}
        </div>
      ) : null}

      <div className="kbc-chart-panel__body">
        {error ? <div className="kbc-chart-state is-error">{error}</div> : null}
        {!error && empty ? <div className="kbc-chart-state">{emptyMessage}</div> : null}
        {!error && !empty && children ? children : null}
        {!error && !empty && !children && option ? (
          <EChartsView option={option} height={height} loading={loading} ariaLabel={title} />
        ) : null}
      </div>
    </section>
  );
}
