import { useEffect, useId, useRef, useState } from "react";

import type { EChartsViewProps } from "./types";
import "./visualization.css";

const loadECharts = async () => {
  return import("echarts") as Promise<{
    init: (
      element: HTMLDivElement,
      theme?: string | null,
      options?: { renderer?: "canvas" | "svg" },
    ) => EChartsInstance;
  }>;
};

interface EChartsInstance {
  setOption: (
    option: Record<string, unknown>,
    opts?: { notMerge?: boolean; lazyUpdate?: boolean },
  ) => void;
  resize: () => void;
  dispose: () => void;
  showLoading: (type?: string, opts?: Record<string, unknown>) => void;
  hideLoading: () => void;
  on: (eventName: string, handler: (params: unknown) => void) => void;
  off: (eventName: string, handler: (params: unknown) => void) => void;
}

const toCssSize = (value: number | string | undefined) => {
  if (value === undefined) {
    return "320px";
  }
  return typeof value === "number" ? `${value}px` : value;
};

export function EChartsView({
  option,
  height,
  className,
  loading,
  renderer = "canvas",
  notMerge = true,
  lazyUpdate = true,
  ariaLabel,
  onReady,
  onError,
  events,
}: EChartsViewProps) {
  const chartId = useId();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsInstance | null>(null);
  const eventsRef = useRef(events);
  const [error, setError] = useState<string | null>(null);

  eventsRef.current = events;

  useEffect(() => {
    let disposed = false;
    let cleanupEvents: Array<() => void> = [];
    let resizeObserver: ResizeObserver | null = null;

    const boot = async () => {
      if (!hostRef.current) {
        return;
      }

      try {
        const echarts = await loadECharts();
        if (disposed || !hostRef.current) {
          return;
        }

        const chart = echarts.init(hostRef.current, null, { renderer });
        chartRef.current = chart;
        onReady?.(chart);

        const currentEvents = eventsRef.current ?? {};
        cleanupEvents = Object.entries(currentEvents).map(([eventName, handler]) => {
          chart.on(eventName, handler);
          return () => chart.off(eventName, handler);
        });

        chart.setOption(option, { notMerge, lazyUpdate });
        if (loading) {
          chart.showLoading("default", {
            color: "#1DD6B7",
            textColor: "#8EA39D",
            maskColor: "rgba(7, 16, 18, 0.32)",
          });
        }

        resizeObserver = new ResizeObserver(() => {
          window.requestAnimationFrame(() => chart.resize());
        });
        resizeObserver.observe(hostRef.current);
      } catch (loadError) {
        const message =
          loadError instanceof Error ? loadError.message : "Unable to load chart renderer.";
        setError(message);
        onError?.(message);
      }
    };

    void boot();

    return () => {
      disposed = true;
      cleanupEvents.forEach((cleanup) => cleanup());
      resizeObserver?.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, [renderer]);

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge, lazyUpdate });
  }, [option, notMerge, lazyUpdate]);

  useEffect(() => {
    if (!chartRef.current) {
      return;
    }
    if (loading) {
      chartRef.current.showLoading("default", {
        color: "#1DD6B7",
        textColor: "#8EA39D",
        maskColor: "rgba(7, 16, 18, 0.32)",
      });
    } else {
      chartRef.current.hideLoading();
    }
  }, [loading]);

  return (
    <div
      className={["kbc-chart-host", className].filter(Boolean).join(" ")}
      style={{ minHeight: toCssSize(height), height: toCssSize(height) }}
    >
      <div
        ref={hostRef}
        id={chartId}
        aria-label={ariaLabel}
        className="kbc-chart-canvas"
        role="img"
      />
      {error ? <div className="kbc-chart-error">{error}</div> : null}
    </div>
  );
}
