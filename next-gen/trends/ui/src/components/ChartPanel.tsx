import type { ReactNode } from "react";
import { useEffect, useId, useRef } from "react";
import { BarChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import {
  AxisPointerComponent,
  DataZoomComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { getInstanceByDom, init, use, type ECharts, type EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { Info } from "lucide-react";

use([
  BarChart,
  HeatmapChart,
  LineChart,
  ScatterChart,
  AxisPointerComponent,
  DataZoomComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

type EChartsOption = EChartsCoreOption;

type ChartPanelProps = {
  title: string;
  subtitle?: string;
  option: EChartsOption;
  height?: number;
  dense?: boolean;
  actions?: ReactNode;
  explanation?: string;
};

export function ChartPanel({
  title,
  subtitle,
  option,
  height = 360,
  dense = false,
  actions,
  explanation,
}: ChartPanelProps) {
  const helpId = useId();
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return undefined;
    const chart = init(ref.current, undefined, { renderer: "canvas", useDirtyRect: false });
    chartRef.current = chart;
    let resizeFrame: number | null = null;
    const observer = new ResizeObserver(() => {
      if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => chart.resize());
    });
    observer.observe(ref.current);
    return () => {
      if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
      observer.disconnect();
      chartRef.current = null;
      chart.dispose();
    };
  }, []);

  useEffect(() => {
    const instance = ref.current ? getInstanceByDom(ref.current) : chartRef.current;
    instance?.setOption(option, { notMerge: true, lazyUpdate: true });
  }, [option]);

  return (
    <section className={`chart-panel ${dense ? "chart-panel-dense" : ""}`}>
      <header className="panel-head">
        <div>
          <div className="panel-title-line">
            <h3>{title}</h3>
            {explanation ? (
              <span className="chart-help">
                <button
                  aria-describedby={helpId}
                  aria-label={`About ${title}`}
                  className="chart-help-button"
                  title={explanation}
                  type="button"
                >
                  <Info aria-hidden="true" size={14} />
                </button>
                <span className="chart-help-popover" id={helpId} role="tooltip">
                  {explanation}
                </span>
              </span>
            ) : null}
          </div>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {actions ? <div className="panel-actions">{actions}</div> : null}
      </header>
      <div
        ref={ref}
        aria-label={[title, subtitle].filter(Boolean).join(". ")}
        className="chart-canvas"
        role="img"
        style={{ minHeight: height }}
      />
    </section>
  );
}
