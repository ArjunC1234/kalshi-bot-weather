const COLORS = ["#176c64", "#cc6a3b", "#253b64", "#936719", "#74558b", "#337e4d", "#b94a3a", "#69736a"];

const MODE_GROUPS = [
  { label: "Dataset", modes: ["overview", "quality"] },
  { label: "Weather", modes: ["trends", "replay", "disagreement", "settlements"] },
  { label: "Model", modes: ["performance", "features", "calibration"] },
  { label: "Market", modes: ["market-model"] },
];

const VIEW_DEFINITIONS = {
  overview: {
    group: "Dataset",
    label: "Overview",
    description: "Check loaded dataset coverage, final-high completion, settlement completion, and replay availability.",
    controls: [],
    render: renderOverview,
    defaults: () => ({}),
  },
  quality: {
    group: "Dataset",
    label: "Data Quality",
    description: "Inspect missing city-hours, provider errors, table counts, and quality report status.",
    controls: ["provider", "qualityStatus"],
    render: renderQuality,
    defaults: () => ({ provider: "all", status: "all" }),
  },
  trends: {
    group: "Weather",
    label: "Trend Explorer",
    description: "Graph a collected source, market, settlement, or model value across time.",
    controls: ["metric", "dateRange", "cities", "smoothing"],
    render: renderTrendExplorer,
    defaults: () => ({ metric: firstMetric(), start: dateStart(), end: dateEnd(), cities: allCities(), cityMode: "multi", smoothing: "0" }),
  },
  replay: {
    group: "Weather",
    label: "Event Replay",
    description: "Replay one city-day timeline from snapshots to final result.",
    controls: ["singleCity", "singleDate", "checkpoint"],
    render: renderReplay,
    defaults: () => ({ city: allCities()[0] || "", date: dateStart(), checkpoint: "" }),
  },
  disagreement: {
    group: "Weather",
    label: "Source Disagreement",
    description: "Find when NWS, observations, HRRR, NBM, and ensembles materially diverged.",
    controls: ["disagreementMetric", "threshold", "dateRange", "cities"],
    render: renderDisagreement,
    defaults: () => ({ metric: "source_range_f", threshold: "3", start: dateStart(), end: dateEnd(), cities: allCities(), cityMode: "multi" }),
  },
  settlements: {
    group: "Weather",
    label: "Settlement Grid",
    description: "Scan final highs, settled brackets, and model misses by city and target date.",
    controls: ["dateRange", "cities", "cellValue", "heatmapValues"],
    render: renderSettlementGrid,
    defaults: () => ({ start: dateStart(), end: dateEnd(), cities: allCities(), cityMode: "multi", cellValue: "final_high_f", heatmapValues: "auto" }),
  },
  performance: {
    group: "Model",
    label: "Checkpoint Performance",
    description: "Compare model quality by city, checkpoint, and metric. Rows are derived from model error records.",
    requiresReport: true,
    controls: ["metricPreset", "metricMulti", "cities", "checkpointMulti", "heatmapValues"],
    render: renderPerformance,
    defaults: () => ({
      metricPreset: "core",
      metrics: ["mae", "rmse", "bias", "log_loss", "rps", "winner_probability"],
      cities: allCities(),
      cityMode: "multi",
      checkpoints: allCheckpoints(),
      heatmapValues: "auto",
    }),
  },
  features: {
    group: "Model",
    label: "Feature vs Error",
    description: "Scatter a collected feature against model error to identify miss patterns.",
    requiresReport: true,
    controls: ["featureX", "errorY", "dateRange", "cities", "checkpointMulti"],
    render: renderFeatureError,
    defaults: () => ({ xFeature: defaultFeature(), yError: "absolute_error_f", start: dateStart(), end: dateEnd(), cities: allCities(), cityMode: "multi", checkpoints: allCheckpoints() }),
  },
  calibration: {
    group: "Model",
    label: "Calibration",
    description: "Compare predicted bracket probabilities with realized frequencies by city/checkpoint buckets.",
    requiresReport: true,
    controls: ["bucketMode", "cities", "checkpointMulti"],
    render: renderCalibration,
    defaults: () => ({ bucketMode: "all", cities: allCities(), cityMode: "multi", checkpoints: allCheckpoints() }),
  },
  "market-model": {
    group: "Market",
    label: "Market vs Model",
    description: "Compare model probabilities with archived Kalshi ask/midpoint values.",
    requiresReport: true,
    controls: ["dateRange", "cities", "checkpoint", "candidateScope"],
    render: renderMarketModel,
    defaults: () => ({ start: dateStart(), end: dateEnd(), cities: allCities(), cityMode: "multi", checkpoint: "", candidateScope: "all" }),
  },
};

const PERFORMANCE_METRIC_PRESETS = {
  core: ["mae", "rmse", "bias", "log_loss", "rps", "winner_probability"],
  temperature: ["mae", "rmse", "bias", "within_1f", "within_2f"],
  bracket: ["log_loss", "brier", "rps", "winner_probability", "top_one_accuracy"],
  all: ["mae", "rmse", "bias", "within_1f", "within_2f", "log_loss", "brier", "rps", "winner_probability", "top_one_accuracy"],
};

const state = {
  sources: null,
  metadata: null,
  catalog: null,
  overview: null,
  selection: null,
  mode: "overview",
  dateBounds: { start: "", end: "" },
  params: {},
  activeTabs: {},
  tableSort: {},
  tableCollapsed: {},
  focusChart: "",
  selectedEventKey: "",
  cache: { series: {}, analysis: {}, tables: {}, events: {}, derived: {} },
};

async function init() {
  const hashMode = window.location.hash.replace("#", "");
  if (hashMode && VIEW_DEFINITIONS[hashMode]) state.mode = hashMode;
  await loadSources();
  wireStaticEvents();
  renderSourcePicker();
  renderUnloadedState();
}

function wireStaticEvents() {
  document.getElementById("loadSourceButton").addEventListener("click", () => void loadWorkbench(false));
  document.getElementById("reloadSourcesButton").addEventListener("click", () => void reloadSources());
  document.getElementById("railLoadSourceButton").addEventListener("click", () => void loadWorkbench(true));
  document.getElementById("railReloadSourcesButton").addEventListener("click", () => void reloadSources());
  document.getElementById("resetFiltersButton").addEventListener("click", () => {
    resetViewParams(state.mode);
    void render();
  });
  document.getElementById("modeControls").addEventListener("change", (event) => handleControlChange(event));
  document.getElementById("modeControls").addEventListener("click", (event) => handleControlClick(event));
  document.getElementById("contentTabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-tab]");
    if (!button) return;
    state.activeTabs[state.mode] = button.dataset.tab;
    void render();
  });
  document.getElementById("content").addEventListener("click", (event) => {
    const focus = event.target.closest("[data-focus-chart]");
    if (focus) {
      const isFocused = state.focusChart === "" || state.focusChart === focus.dataset.focusChart;
      state.focusChart = isFocused ? "__compact__" : focus.dataset.focusChart;
      void render();
      return;
    }
    const toggle = event.target.closest("[data-table-toggle]");
    if (toggle) {
      state.tableCollapsed[toggle.dataset.tableToggle] = !isTableCollapsed(toggle.dataset.tableToggle, false);
      void render();
      return;
    }
    const sort = event.target.closest("[data-sort-table]");
    if (sort) {
      cycleTableSort(sort.dataset.sortTable, sort.dataset.sortColumn);
      void render();
      return;
    }
    const eventRow = event.target.closest("[data-event-key]");
    if (eventRow) openEventReplay(eventRow.dataset.eventKey);
  });
  document.getElementById("content").addEventListener("mouseover", (event) => {
    const target = event.target.closest("[data-hover-readout]");
    if (target) document.getElementById("hoverReadout").textContent = target.dataset.hoverReadout;
  });
}

async function reloadSources() {
  await loadSources();
  renderSourcePicker();
}

async function loadSources() {
  state.sources = await apiGet("/api/sources");
}

function renderSourcePicker() {
  const exports = state.sources?.exports || [];
  const reports = state.sources?.reports || [];
  const qualityReports = state.sources?.quality_reports || [];
  fillSelectPair("exportSourceSelect", "railExportSourceSelect", exports, { optional: false });
  fillSelectPair("reportSourceSelect", "railReportSourceSelect", reports, { optional: true, emptyLabel: "No model report" });
  fillSelectPair("qualitySourceSelect", "railQualitySourceSelect", qualityReports, { optional: true, emptyLabel: "No quality report" });
  document.getElementById("loadSourceButton").disabled = exports.length === 0;
  document.getElementById("railLoadSourceButton").disabled = exports.length === 0;
  if (!exports.length) setSourceStatus("No valid exports found under the configured data root.", true);
  else hideSourceStatus();
}

function fillSelectPair(startId, railId, rows, options) {
  const html = sourceOptionsHtml(rows, options);
  for (const id of [startId, railId]) {
    const select = document.getElementById(id);
    select.innerHTML = html;
  }
}

function sourceOptionsHtml(rows, options) {
  const empty = options.optional ? `<option value="">${escapeHtml(options.emptyLabel || "None")}</option>` : "";
  return (
    empty +
    rows
      .map((row) => `<option value="${escapeHtml(row.id)}">${escapeHtml(row.name)} (${row.file_count})</option>`)
      .join("")
  );
}

async function loadWorkbench(fromRail) {
  const exportId = document.getElementById(fromRail ? "railExportSourceSelect" : "exportSourceSelect").value;
  const reportId = document.getElementById(fromRail ? "railReportSourceSelect" : "reportSourceSelect").value;
  const qualityId = document.getElementById(fromRail ? "railQualitySourceSelect" : "qualitySourceSelect").value;
  if (!exportId) {
    setSourceStatus("Choose a Supabase export before loading.", true);
    return;
  }
  setSourceStatus("Loading selected workbench...", false);
  try {
    const result = await apiPost("/api/load", { export_id: exportId, report_id: reportId || null, quality_id: qualityId || null });
    state.metadata = result.metadata;
    state.catalog = result.catalog;
    state.overview = result.overview;
    state.selection = result.selection;
    state.dateBounds = datasetDateBounds(state.catalog.events || []);
    state.params = {};
    state.activeTabs = {};
    state.tableSort = {};
    state.tableCollapsed = {};
    state.focusChart = "";
    state.selectedEventKey = "";
    state.cache = { series: {}, analysis: {}, tables: {}, events: {}, derived: {} };
    ensureAllParams();
    document.getElementById("unloadedPanel").hidden = true;
    document.getElementById("workspacePanel").hidden = false;
    document.body.classList.add("loaded");
    syncRailSourceSelects(exportId, reportId, qualityId);
    hideSourceStatus();
    renderNav();
    renderSourceSummary();
    await render();
  } catch (error) {
    setSourceStatus(error.message, true);
  }
}

function syncRailSourceSelects(exportId, reportId, qualityId) {
  for (const [id, value] of [
    ["exportSourceSelect", exportId],
    ["railExportSourceSelect", exportId],
    ["reportSourceSelect", reportId || ""],
    ["railReportSourceSelect", reportId || ""],
    ["qualitySourceSelect", qualityId || ""],
    ["railQualitySourceSelect", qualityId || ""],
  ]) {
    document.getElementById(id).value = value;
  }
}

function renderUnloadedState() {
  document.body.classList.remove("loaded");
  document.getElementById("workspacePanel").hidden = true;
  document.getElementById("unloadedPanel").hidden = false;
}

function renderNav() {
  const modes = state.catalog?.modes || [];
  const available = new Map(modes.map((mode) => [mode.key, mode]));
  document.getElementById("modeNav").innerHTML = MODE_GROUPS.map((group) => {
    const buttons = group.modes
      .filter((key) => available.has(key))
      .map((key) => {
        const def = VIEW_DEFINITIONS[key];
        const disabled = Boolean((def.requiresReport || available.get(key)?.requires_report) && !state.metadata.has_model_reports);
        const classes = ["mode-button", state.mode === key ? "active" : "", disabled ? "disabled" : ""].filter(Boolean).join(" ");
        return `<button class="${classes}" type="button" data-mode="${key}" ${disabled ? "disabled" : ""}>${escapeHtml(def.label)}</button>`;
      })
      .join("");
    return `<div class="nav-group"><div class="nav-group-label">${escapeHtml(group.label)}</div>${buttons}</div>`;
  }).join("");
  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.mode = button.dataset.mode;
      window.location.hash = state.mode;
      state.focusChart = "";
      renderNav();
      void render();
    });
  });
}

function renderSourceSummary() {
  const dateText = `${dateStart() || "n/a"} to ${dateEnd() || "n/a"}`;
  document.getElementById("sourceSummary").innerHTML = [
    ["Export", state.selection?.export_id || "n/a"],
    ["Report", state.selection?.report_id || "none"],
    ["Quality", state.selection?.quality_id || "none"],
    ["Dates", dateText],
  ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
}

async function render() {
  const def = VIEW_DEFINITIONS[state.mode] || VIEW_DEFINITIONS.overview;
  ensureParams(state.mode);
  document.getElementById("modeGroup").textContent = def.group;
  document.getElementById("modeTitle").textContent = def.label;
  document.getElementById("modeSubtitle").textContent = def.description;
  renderMetricPills();
  renderControls(def);
  renderDetails("");

  if (def.requiresReport && !state.metadata.has_model_reports) {
    renderTabs([]);
    document.getElementById("content").innerHTML = emptyState("Select a model report in Sources to use this mode.");
    return;
  }

  const tabs = await def.render();
  renderTabs(tabs);
  const active = activeTab(tabs);
  document.getElementById("content").innerHTML = active?.html || emptyState("No data for this mode.");
}

function renderMetricPills() {
  const overview = state.overview || {};
  const pills = [
    ["Cities", state.metadata.cities.length],
    ["Events", overview.event_count || 0],
    ["Snapshots", overview.snapshot_count || 0],
    ["Final Highs", overview.final_high_count || 0],
    ["Settlements", overview.settlement_count || 0],
  ];
  document.getElementById("metricPills").innerHTML = pills
    .map(([label, value]) => `<div><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`)
    .join("");
}

function renderControls(def) {
  const controls = def.controls || [];
  const wrap = document.getElementById("modeControls");
  wrap.innerHTML = controls.length
    ? controls.map((control) => controlHtml(control)).filter(Boolean).join("")
    : `<div class="muted-note">No parameters for this view.</div>`;
  document.getElementById("resetFiltersButton").hidden = isDefaultParams();
}

function renderTabs(tabs) {
  if (tabs.length <= 1) {
    document.getElementById("contentTabs").innerHTML = "";
    return;
  }
  const active = activeTab(tabs)?.id;
  document.getElementById("contentTabs").innerHTML = tabs
    .map((tab) => `<button class="${tab.id === active ? "active" : ""}" type="button" data-tab="${escapeHtml(tab.id)}">${escapeHtml(tab.label)}</button>`)
    .join("");
}

function activeTab(tabs) {
  if (!tabs.length) return null;
  const selected = state.activeTabs[state.mode];
  return tabs.find((tab) => tab.id === selected) || tabs[0];
}

function renderDetails(html) {
  document.getElementById("detailsPanel").innerHTML = html || `<div class="muted-note">Select chart marks or table rows for details.</div>`;
}

async function renderOverview() {
  const finalHighSeries = await getSeries("final_high_by_day");
  const eventReplays = await getAnalysis("event_replays");
  const completeness = [
    ["Events", state.overview.event_count || 0],
    ["Snapshots", state.overview.snapshot_count || 0],
    ["Market Rows", state.overview.market_row_count || 0],
    ["Pending Settlements", state.overview.pending_settlements || 0],
    ["Pending Final Highs", state.overview.pending_final_highs || 0],
  ];
  return [
    {
      id: "final-highs",
      label: "Final Highs",
      html: chartCard({
        id: "overview-final-highs",
        title: "Final Highs By Day",
        chart: lineChart(finalHighSeries, { x: "x", y: "value", group: "group", xLabel: "Date", yLabel: "Final high (F)" }, null, "overview-final-highs"),
        legendGroups: legendForRows(finalHighSeries, "group"),
      }),
    },
    {
      id: "coverage",
      label: "Coverage",
      html: chartCard({
        id: "overview-coverage",
        title: "Snapshot Coverage",
        chart: groupedBars(eventReplays.map((event) => ({ group: event.city, label: event.target_date || event.event_ticker, value: event.timeline.length, event_key: event.event_key })), "label", "group", "value", { xLabel: "City-day", yLabel: "Snapshots", chartId: "overview-coverage" }),
        legendGroups: legendForRows(eventReplays, "city"),
      }),
    },
    {
      id: "completeness",
      label: "Completeness",
      html: `<div class="summary-grid">${completeness.map(summaryCardHtml).join("")}</div>${tableCard("overview-events", "Events", state.catalog.events, ["city", "target_date", "event_ticker", "station_id", "city_timezone"], true, true)}`,
    },
  ];
}

async function renderTrendExplorer() {
  const params = currentParams();
  const metric = state.metadata.metrics.find((item) => item.key === params.metric) || state.metadata.metrics[0];
  let rows = filterRows(await getSeries(metric?.key || params.metric), params, { groupKey: "group", timeKey: "x" });
  if (Number(params.smoothing || 0)) rows = smoothRows(rows, Number(params.smoothing));
  return [{
    id: "trend",
    label: "Trend",
    html: chartCard({
      id: "trend-main",
      title: metric?.label || "Trend",
      chart: lineChart(rows, { x: "x", y: "value", group: "group", xLabel: "Time", yLabel: metric?.label || "Value" }, null, "trend-main"),
      legendGroups: legendForRows(rows, "group"),
    }),
  }];
}

async function renderReplay() {
  const params = currentParams();
  const eventKey = selectedEventKey(params);
  const event = eventKey ? await getEvent(eventKey) : null;
  if (!event) return [{ id: "empty", label: "Replay", html: emptyState("No city-day matches the selected city and date.") }];
  const timeline = params.checkpoint ? (event.timeline || []).filter((row) => row.checkpoint === params.checkpoint) : event.timeline || [];
  const lineRows = eventLineRows(timeline);
  const latestSnapshot = timeline.at(-1)?.snapshot_time_utc;
  const latestProbs = (event.bracket_probabilities || []).filter((row) => row.snapshot_time_utc === latestSnapshot);
  renderDetails(eventDetailsHtml(event, timeline.length));
  return [
    {
      id: "timeline",
      label: "Timeline",
      html: chartCard({
        id: "replay-timeline",
        title: `${event.city?.toUpperCase()} ${event.target_date || ""}`,
        chart: lineChart(lineRows, { x: "x", y: "value", group: "group", xLabel: "Snapshot time", yLabel: "Forecast (F)" }, event.final_high_f, "replay-timeline"),
        legendGroups: legendForRows(lineRows, "group"),
      }),
    },
    {
      id: "brackets",
      label: "Brackets",
      html: chartCard({
        id: "replay-brackets",
        title: "Latest Bracket Probabilities",
        chart: stackedBars(latestProbs, "market_ticker", "model_probability", { xLabel: "Bracket", yLabel: "Probability", chartId: "replay-brackets" }),
        legendGroups: legendForRows(latestProbs, "market_ticker"),
      }),
    },
    {
      id: "tables",
      label: "Details",
      html: `${tableCard("replay-timeline-table", "Replay Timeline", timeline, ["snapshot_time_utc", "checkpoint", "nws_anchor_high_f", "observed_high_so_far_f", "hrrr_projected_high_f", "nbm_projected_high_f", "market_top_ticker", "market_top_probability"], false, false)}${tableCard("replay-matches", "Matching City-Days", matchingEvents(params), ["city", "target_date", "event_ticker", "station_id"], true, true)}`,
    },
  ];
}

async function renderPerformance() {
  const params = currentParams();
  const selectedMetrics = new Set(params.metrics || PERFORMANCE_METRIC_PRESETS.core);
  const rows = filterRows(await derivedPerformanceRows(), params, { groupKey: "city" })
    .filter((row) => selectedMetrics.has(row.metric))
    .filter((row) => !params.checkpoints?.length || params.checkpoints.includes(row.checkpoint));
  const selectedCities = params.cities?.length ? params.cities : allCities();
  const heatmaps = selectedCities
    .map((city) => {
      const cityRows = rows
        .filter((row) => row.city === city)
        .map((row) => ({ ...row, y_label: row.metric }));
      if (!cityRows.length) return "";
      return chartCard({
        id: `performance-heatmap-${city}`,
        title: `${city.toUpperCase()} Checkpoint Performance`,
        chart: heatmap(cityRows, "checkpoint", "y_label", "value", {
          xLabel: "Checkpoint",
          yLabel: "Metric",
          valueMode: params.heatmapValues,
          chartId: `performance-heatmap-${city}`,
        }),
      });
    })
    .filter(Boolean)
    .join("");
  return [
    {
      id: "heatmap",
      label: "Heatmap",
      html: heatmaps || emptyState("No checkpoint metrics match the selected cities, checkpoints, and metrics."),
    },
    {
      id: "bars",
      label: "Bars",
      html: selectedCities
        .map((city) => {
          const cityRows = rows.filter((row) => row.city === city);
          if (!cityRows.length) return "";
          return chartCard({
            id: `performance-bars-${city}`,
            title: `${city.toUpperCase()} Grouped Metrics`,
            chart: groupedBars(cityRows, "checkpoint", "metric", "value", { xLabel: "Checkpoint", yLabel: "Metric value", chartId: `performance-bars-${city}` }),
            legendGroups: legendForRows(cityRows, "metric"),
          });
        })
        .filter(Boolean)
        .join("") || emptyState("No grouped metrics match the selected filters."),
    },
    {
      id: "table",
      label: "Rows",
      html: tableCard("performance-table", "Derived Checkpoint Metrics", rows, ["city", "checkpoint", "metric_type", "metric", "value", "count"], false, false),
    },
  ];
}

async function renderFeatureError() {
  const params = currentParams();
  const rows = filterRows(await getAnalysis("feature_error_points"), params, { groupKey: "city", timeKey: "snapshot_time_utc" })
    .filter((row) => !params.checkpoints?.length || params.checkpoints.includes(row.checkpoint));
  return [
    {
      id: "scatter",
      label: "Scatter",
      html: chartCard({
        id: "feature-scatter",
        title: "Feature vs Error",
        chart: scatterPlot(rows, params.xFeature, params.yError, "city", { xLabel: params.xFeature, yLabel: params.yError, chartId: "feature-scatter" }),
        legendGroups: legendForRows(rows, "city"),
      }),
    },
    {
      id: "table",
      label: "Errors",
      html: tableCard("feature-errors", "Largest Errors", [...rows].sort((a, b) => Number(b.absolute_error_f || 0) - Number(a.absolute_error_f || 0)).slice(0, 120), ["city", "event_ticker", "snapshot_time_utc", "checkpoint", "predicted_high_f", "actual_high_f", "error_f", params.xFeature], true, false),
    },
  ];
}

async function renderDisagreement() {
  const params = currentParams();
  const rows = filterRows(await getAnalysis("source_disagreement"), params, { groupKey: "city", timeKey: "snapshot_time_utc" });
  const lineRows = rows.filter((row) => isNumber(row[params.metric])).map((row) => ({ x: row.snapshot_time_utc, group: row.city, value: row[params.metric], event_key: row.event_key }));
  const threshold = Number(params.threshold || 3);
  const outliers = [...rows].filter((row) => Math.abs(Number(row[params.metric] || 0)) >= threshold).sort((a, b) => Math.abs(Number(b[params.metric] || 0)) - Math.abs(Number(a[params.metric] || 0)));
  return [
    {
      id: "timeline",
      label: "Timeline",
      html: chartCard({
        id: "disagreement-line",
        title: "Source Disagreement",
        chart: lineChart(lineRows, { x: "x", y: "value", group: "group", xLabel: "Snapshot time", yLabel: params.metric }, null, "disagreement-line"),
        legendGroups: legendForRows(lineRows, "group"),
      }),
    },
    {
      id: "scatter",
      label: "HRRR vs NWS",
      html: chartCard({
        id: "disagreement-scatter",
        title: "HRRR vs NWS",
        chart: scatterPlot(rows, "nws_anchor_high_f", "hrrr_projected_high_f", "city", { xLabel: "NWS anchor (F)", yLabel: "HRRR projected high (F)", chartId: "disagreement-scatter" }),
        legendGroups: legendForRows(rows, "city"),
      }),
    },
    {
      id: "outliers",
      label: "Outliers",
      html: tableCard("disagreement-outliers", "Disagreement Outliers", outliers.slice(0, 150), ["city", "event_ticker", "snapshot_time_utc", "checkpoint", "source_range_f", "hrrr_minus_nws", "nbm_minus_nws", "ensemble_minus_nws", "observed_minus_nws"], true, false),
    },
  ];
}

async function renderMarketModel() {
  const params = currentParams();
  let rows = filterRows(await getAnalysis("market_model_points"), params, { groupKey: "city", timeKey: "snapshot_time_utc" });
  if (params.checkpoint) rows = rows.filter((row) => row.checkpoint === params.checkpoint);
  if (params.candidateScope === "winners") rows = rows.filter((row) => row.is_winner);
  if (params.candidateScope === "model_edge") rows = rows.filter((row) => Number(row.model_minus_ask || 0) > 0);
  const winners = rows.filter((row) => row.is_winner);
  return [
    {
      id: "scatter",
      label: "Ask vs Model",
      html: chartCard({
        id: "market-model-scatter",
        title: "Model Probability vs Ask",
        chart: scatterPlot(rows, "yes_ask_dollars", "model_probability", "city", { xLabel: "YES ask ($)", yLabel: "Model probability", chartId: "market-model-scatter" }),
        legendGroups: legendForRows(rows, "city"),
      }),
    },
    {
      id: "winner-path",
      label: "Winner Path",
      html: chartCard({
        id: "market-model-winners",
        title: "Winner Probability Path",
        chart: lineChart(winners.map((row) => ({ x: row.snapshot_time_utc, group: row.city, value: row.model_probability, event_key: row.event_key })), { x: "x", y: "value", group: "group", xLabel: "Snapshot time", yLabel: "Winner probability" }, null, "market-model-winners"),
        legendGroups: legendForRows(winners, "city"),
      }),
    },
    {
      id: "table",
      label: "Candidates",
      html: tableCard("market-model-candidates", "Model-Market Candidates", [...rows].sort((a, b) => Number(b.model_minus_ask || -9) - Number(a.model_minus_ask || -9)).slice(0, 180), ["city", "event_ticker", "snapshot_time_utc", "checkpoint", "market_ticker", "bracket_label", "model_probability", "yes_ask_dollars", "model_minus_ask", "is_winner"], true, false),
    },
  ];
}

async function renderCalibration() {
  const params = currentParams();
  let rows = filterRows(await derivedCalibrationRows(), params, { groupKey: "city" })
    .filter((row) => !params.checkpoints?.length || params.checkpoints.includes(row.checkpoint));
  if (params.bucketMode === "nonempty") rows = rows.filter((row) => Number(row.count || 0) > 0);
  return [
    {
      id: "reliability",
      label: "Reliability",
      html: chartCard({
        id: "calibration-reliability",
        title: "Calibration By City",
        chart: groupedBars(rows, "probability_bin", "city", "observed_frequency", { xLabel: "Probability bucket", yLabel: "Observed frequency", chartId: "calibration-reliability" }),
        legendGroups: legendForRows(rows, "city"),
      }),
    },
    {
      id: "table",
      label: "Buckets",
      html: tableCard("calibration-table", "Calibration Buckets", rows, ["city", "checkpoint", "model_name", "probability_bin", "count", "mean_probability", "observed_frequency"], false, false),
    },
  ];
}

async function renderSettlementGrid() {
  const params = currentParams();
  const rows = filterRows(await getAnalysis("settlement_grid"), params, { groupKey: "city" });
  return [
    {
      id: "matrix",
      label: "Matrix",
      html: chartCard({
        id: "settlement-grid",
        title: "Settlement Matrix",
        chart: heatmap(rows, "target_date", "city", params.cellValue, { xLabel: "Target date", yLabel: "City", valueMode: params.heatmapValues, chartId: "settlement-grid" }),
      }),
    },
    {
      id: "table",
      label: "Rows",
      html: tableCard("settlements-table", "Settlements", rows, ["city", "target_date", "event_ticker", "final_high_f", "winner_label", "model_top_ticker", "model_top_probability", "winner_probability", "absolute_error_f"], true, false),
    },
  ];
}

async function renderQuality() {
  const params = currentParams();
  const quality = await getAnalysis("quality");
  let providerErrors = quality.provider_errors || (await getTable("provider_errors"));
  if (params.provider && params.provider !== "all") providerErrors = providerErrors.filter((row) => String(row.provider || "").includes(params.provider));
  const missingRows = quality.missing_city_hours || [];
  const cards = [
    ["Quality Loaded", quality.available ? "yes" : "no"],
    ["Pending Settlements", state.overview.pending_settlements || 0],
    ["Pending Final Highs", state.overview.pending_final_highs || 0],
    ["Tables", Object.keys(state.metadata.table_counts || {}).length],
  ];
  const tableCounts = Object.entries(state.metadata.table_counts || {}).map(([table, count]) => ({ table, count }));
  const showErrors = params.status === "all" || params.status === "errors";
  const showMissing = params.status === "all" || params.status === "missing";
  return [
    {
      id: "summary",
      label: "Summary",
      html: `<div class="summary-grid">${cards.map(summaryCardHtml).join("")}</div>${tableCard("quality-counts", "Loaded Table Counts", tableCounts, ["table", "count"], false, false)}`,
    },
    ...(showErrors ? [{ id: "errors", label: "Errors", html: tableCard("quality-errors", "Provider Errors", providerErrors || [], ["provider", "endpoint", "city", "event_ticker", "error_message"], true, false) }] : []),
    ...(showMissing ? [{ id: "missing", label: "Missing Hours", html: tableCard("quality-missing", "Missing City-Hours", missingRows, ["snapshot_hour_utc", "city"], false, false) }] : []),
  ];
}

function controlHtml(control) {
  const params = currentParams();
  if (control === "metric") {
    return fieldHtml("Metric", `<select data-param="metric">${(state.metadata.metrics || [])
      .map((metric) => `<option value="${escapeHtml(metric.key)}" ${params.metric === metric.key ? "selected" : ""}>${escapeHtml(metric.label || metric.key)}</option>`)
      .join("")}</select>`);
  }
  if (control === "dateRange") {
    return `<div class="control-field double"><label>Dates</label><div class="inline-controls">
      <input type="date" data-param="start" min="${escapeHtml(dateStart())}" max="${escapeHtml(dateEnd())}" value="${escapeHtml(params.start || dateStart())}">
      <span>to</span>
      <input type="date" data-param="end" min="${escapeHtml(dateStart())}" max="${escapeHtml(dateEnd())}" value="${escapeHtml(params.end || dateEnd())}">
    </div></div>`;
  }
  if (control === "cities") return chipGroupHtml("Cities", "city", allCities(), params.cities || allCities(), true);
  if (control === "smoothing") {
    return fieldHtml("Smoothing", `<select data-param="smoothing">
      ${[0, 2, 3, 5].map((value) => `<option value="${value}" ${String(params.smoothing) === String(value) ? "selected" : ""}>${value ? `${value}-point` : "None"}</option>`).join("")}
    </select>`);
  }
  if (control === "singleCity") return chipGroupHtml("City", "single-city", allCities(), [params.city || allCities()[0]], false);
  if (control === "singleDate") {
    return fieldHtml("Target date", `<input type="date" data-param="date" min="${escapeHtml(dateStart())}" max="${escapeHtml(dateEnd())}" value="${escapeHtml(params.date || dateStart())}">`);
  }
  if (control === "checkpoint") {
    return fieldHtml("Checkpoint", `<select data-param="checkpoint"><option value="">All checkpoints</option>${allCheckpoints()
      .map((checkpoint) => `<option value="${escapeHtml(checkpoint)}" ${params.checkpoint === checkpoint ? "selected" : ""}>${escapeHtml(shortLabel(checkpoint))}</option>`)
      .join("")}</select>`);
  }
  if (control === "checkpointMulti") {
    return checkboxMenuHtml("Checkpoints", "checkpoint-multi", allCheckpoints(), params.checkpoints || allCheckpoints());
  }
  if (control === "metricPreset") {
    const presets = [...Object.keys(PERFORMANCE_METRIC_PRESETS), "custom"];
    return fieldHtml("Metric preset", `<select data-param="metricPreset">${presets
      .map((preset) => `<option value="${preset}" ${params.metricPreset === preset ? "selected" : ""}>${escapeHtml(titleCase(preset))}</option>`)
      .join("")}</select>`);
  }
  if (control === "metricMulti") {
    return checkboxMenuHtml("Metrics", "metric-multi", PERFORMANCE_METRIC_PRESETS.all, params.metrics || PERFORMANCE_METRIC_PRESETS.core);
  }
  if (control === "heatmapValues") {
    return fieldHtml("Cell values", `<select data-param="heatmapValues">
      <option value="auto" ${params.heatmapValues === "auto" ? "selected" : ""}>Auto (fit)</option>
      <option value="always" ${params.heatmapValues === "always" ? "selected" : ""}>Always</option>
      <option value="hover" ${params.heatmapValues === "hover" ? "selected" : ""}>Hover only</option>
    </select>`);
  }
  if (control === "featureX") {
    const options = state.catalog.feature_columns || [];
    return fieldHtml("X feature", `<select data-param="xFeature">${options
      .map((feature) => `<option value="${escapeHtml(feature)}" ${params.xFeature === feature ? "selected" : ""}>${escapeHtml(feature)}</option>`)
      .join("")}</select>`);
  }
  if (control === "errorY") {
    const options = ["absolute_error_f", "error_f", "temperature_error_f", "absolute_temperature_error_f"];
    return fieldHtml("Y error", `<select data-param="yError">${options
      .map((feature) => `<option value="${escapeHtml(feature)}" ${params.yError === feature ? "selected" : ""}>${escapeHtml(feature)}</option>`)
      .join("")}</select>`);
  }
  if (control === "disagreementMetric") {
    const options = ["source_range_f", "hrrr_minus_nws", "nbm_minus_nws", "ensemble_minus_nws", "observed_minus_nws"];
    return fieldHtml("Disagreement", `<select data-param="metric">${options
      .map((metric) => `<option value="${escapeHtml(metric)}" ${params.metric === metric ? "selected" : ""}>${escapeHtml(metric)}</option>`)
      .join("")}</select>`);
  }
  if (control === "threshold") return fieldHtml("Outlier threshold", `<input type="number" step="0.5" data-param="threshold" value="${escapeHtml(params.threshold || 3)}">`);
  if (control === "candidateScope") {
    const options = [
      ["all", "All candidates"],
      ["winners", "Settled winners"],
      ["model_edge", "Model edge only"],
    ];
    return fieldHtml("Candidate scope", `<select data-param="candidateScope">${options
      .map(([value, label]) => `<option value="${value}" ${params.candidateScope === value ? "selected" : ""}>${escapeHtml(label)}</option>`)
      .join("")}</select>`);
  }
  if (control === "bucketMode") {
    return fieldHtml("Buckets", `<select data-param="bucketMode">
      <option value="all" ${params.bucketMode === "all" ? "selected" : ""}>All buckets</option>
      <option value="nonempty" ${params.bucketMode === "nonempty" ? "selected" : ""}>Non-empty only</option>
    </select>`);
  }
  if (control === "cellValue") {
    const options = ["final_high_f", "winner_probability", "absolute_error_f", "bracket_miss_distance", "model_top_probability"];
    return fieldHtml("Cell value", `<select data-param="cellValue">${options
      .map((value) => `<option value="${value}" ${params.cellValue === value ? "selected" : ""}>${escapeHtml(value)}</option>`)
      .join("")}</select>`);
  }
  if (control === "provider") {
    const providers = ["all", "kalshi", "nws", "open_meteo", "hrrr", "nbm"];
    return fieldHtml("Provider", `<select data-param="provider">${providers
      .map((value) => `<option value="${value}" ${params.provider === value ? "selected" : ""}>${escapeHtml(titleCase(value))}</option>`)
      .join("")}</select>`);
  }
  if (control === "qualityStatus") {
    return fieldHtml("Quality section", `<select data-param="status">
      ${["all", "errors", "missing"].map((value) => `<option value="${value}" ${params.status === value ? "selected" : ""}>${escapeHtml(titleCase(value))}</option>`).join("")}
    </select>`);
  }
  return "";
}

function fieldHtml(label, input) {
  return `<div class="control-field"><label>${escapeHtml(label)}</label>${input}</div>`;
}

function chipGroupHtml(label, kind, values, selected, multiple) {
  const selectedSet = new Set(selected || []);
  const params = currentParams();
  const modeToggle = kind === "city"
    ? `<div class="segmented">
        <button type="button" class="${params.cityMode !== "single" ? "active" : ""}" data-city-mode="multi">Multi</button>
        <button type="button" class="${params.cityMode === "single" ? "active" : ""}" data-city-mode="single">Single</button>
      </div>`
    : "";
  const chips = values
    .map((value) => {
      const active = selectedSet.has(value);
      return `<button class="chip ${active ? "active" : ""}" type="button" data-chip-kind="${kind}" data-chip-value="${escapeHtml(value)}" data-multiple="${multiple ? "1" : "0"}">${escapeHtml(value.toUpperCase())}</button>`;
    })
    .join("");
  return `<div class="control-field"><div class="control-title"><label>${escapeHtml(label)}</label>${modeToggle}</div><div class="chip-row">${chips}</div></div>`;
}

function checkboxMenuHtml(label, kind, values, selected) {
  const selectedSet = new Set(selected || []);
  const count = selectedSet.size;
  const items = values
    .map((value) => `<label class="check-row"><input type="checkbox" data-check-kind="${kind}" value="${escapeHtml(value)}" ${selectedSet.has(value) ? "checked" : ""}>${escapeHtml(shortLabel(value))}</label>`)
    .join("");
  return `<details class="control-field dropdown-control"><summary><span>${escapeHtml(label)}</span><strong>${count}/${values.length}</strong></summary><div class="check-menu">${items}</div></details>`;
}

function handleControlChange(event) {
  const target = event.target;
  const params = currentParams();
  if (target.matches("[data-param]")) {
    params[target.dataset.param] = target.value;
    if (target.dataset.param === "metricPreset") {
      params.metrics = [...(PERFORMANCE_METRIC_PRESETS[target.value] || PERFORMANCE_METRIC_PRESETS.core)];
    }
    void render();
    return;
  }
  if (target.matches("[data-check-kind='checkpoint-multi']")) {
    const checked = [...document.querySelectorAll("[data-check-kind='checkpoint-multi']:checked")].map((input) => input.value);
    params.checkpoints = checked;
    void render();
    return;
  }
  if (target.matches("[data-check-kind='metric-multi']")) {
    const checked = [...document.querySelectorAll("[data-check-kind='metric-multi']:checked")].map((input) => input.value);
    params.metrics = checked.length ? checked : [target.value];
    params.metricPreset = presetForMetrics(params.metrics);
    void render();
  }
}

function handleControlClick(event) {
  const cityMode = event.target.closest("[data-city-mode]");
  if (cityMode) {
    const params = currentParams();
    params.cityMode = cityMode.dataset.cityMode;
    if (params.cityMode === "single") params.cities = [params.cities?.[0] || allCities()[0]].filter(Boolean);
    void render();
    return;
  }
  const chip = event.target.closest("[data-chip-kind]");
  if (!chip) return;
  const params = currentParams();
  const value = chip.dataset.chipValue;
  if (chip.dataset.chipKind === "single-city") {
    params.city = value;
  } else if (chip.dataset.chipKind === "city") {
    if (params.cityMode === "single") {
      params.cities = [value];
    } else {
      const current = new Set(params.cities || []);
      if (current.has(value)) current.delete(value);
      else current.add(value);
      params.cities = current.size ? [...current] : [value];
    }
  }
  void render();
}

function presetForMetrics(metrics) {
  const key = stableStringify([...metrics].sort());
  for (const [preset, values] of Object.entries(PERFORMANCE_METRIC_PRESETS)) {
    if (stableStringify([...values].sort()) === key) return preset;
  }
  return "custom";
}

async function apiGet(path) {
  return readApiResponse(await fetch(path));
}

async function apiPost(path, body) {
  return readApiResponse(await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

async function readApiResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed: ${response.status}`);
  return payload;
}

async function getSeries(metric) {
  if (!state.cache.series[metric]) state.cache.series[metric] = (await apiGet(`/api/series/${encodeURIComponent(metric)}`)).rows || [];
  return state.cache.series[metric];
}

async function getAnalysis(section) {
  if (!state.cache.analysis[section]) state.cache.analysis[section] = (await apiGet(`/api/analysis/${encodeURIComponent(section)}`)).data || {};
  return state.cache.analysis[section];
}

async function getTable(name) {
  if (!state.cache.tables[name]) state.cache.tables[name] = (await apiGet(`/api/table/${encodeURIComponent(name)}`)).rows || [];
  return state.cache.tables[name];
}

async function getEvent(key) {
  if (!state.cache.events[key]) state.cache.events[key] = await apiGet(`/api/event/${encodeURIComponent(key)}`);
  return state.cache.events[key];
}

function datasetDateBounds(events) {
  const dates = (events || []).map((row) => eventDate(row)).filter(Boolean).sort();
  return { start: dates[0] || "", end: dates[dates.length - 1] || "" };
}

function dateStart() {
  return state.dateBounds.start || state.metadata?.date_range?.start || "";
}

function dateEnd() {
  return state.dateBounds.end || state.metadata?.date_range?.end || "";
}

function eventDate(row) {
  return row?.target_date || row?.target_date_local || String(row?.snapshot_time_utc || row?.x || "").slice(0, 10);
}

function allCities() {
  return state.metadata?.cities || state.catalog?.cities || [];
}

function allCheckpoints() {
  return state.catalog?.checkpoints || [];
}

function firstMetric() {
  return state.metadata?.metrics?.[0]?.key || "final_high_by_day";
}

function defaultFeature() {
  return state.catalog?.feature_columns?.[0] || "nws_anchor_high_f";
}

function ensureAllParams() {
  Object.keys(VIEW_DEFINITIONS).forEach((mode) => ensureParams(mode));
}

function ensureParams(mode) {
  if (!state.params[mode]) state.params[mode] = VIEW_DEFINITIONS[mode].defaults();
}

function resetViewParams(mode) {
  state.params[mode] = VIEW_DEFINITIONS[mode].defaults();
}

function currentParams() {
  ensureParams(state.mode);
  return state.params[state.mode];
}

function isDefaultParams() {
  return stableStringify(currentParams()) === stableStringify(VIEW_DEFINITIONS[state.mode].defaults());
}

function stableStringify(value) {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${key}:${stableStringify(value[key])}`).join(",")}}`;
  }
  return String(value);
}

function filterRows(rows, params, options = {}) {
  const groupKey = options.groupKey || "city";
  const timeKey = options.timeKey || "snapshot_time_utc";
  const cities = new Set(params.cities || []);
  return (rows || []).filter((row) => {
    if (cities.size && row[groupKey] && !cities.has(row[groupKey])) return false;
    const date = eventDate({ ...row, target_date: row.target_date, snapshot_time_utc: row[timeKey] });
    if (params.start && date && date < params.start) return false;
    if (params.end && date && date > params.end) return false;
    return true;
  });
}

function matchingEvents(params) {
  return (state.catalog?.events || []).filter((event) => {
    if (params.city && event.city !== params.city) return false;
    if (params.date && eventDate(event) !== params.date) return false;
    return true;
  });
}

function selectedEventKey(params) {
  return matchingEvents(params)[0]?.event_key || "";
}

function openEventReplay(eventKey) {
  const event = (state.catalog?.events || []).find((row) => row.event_key === eventKey);
  if (!event) return;
  state.mode = "replay";
  ensureParams("replay");
  state.params.replay.city = event.city;
  state.params.replay.date = eventDate(event);
  state.params.replay.checkpoint = "";
  state.activeTabs.replay = "timeline";
  renderNav();
  void render();
}

function eventLineRows(timeline) {
  const mappings = [
    ["nws_anchor_high_f", "NWS"],
    ["observed_high_so_far_f", "Observed"],
    ["hrrr_projected_high_f", "HRRR"],
    ["nbm_projected_high_f", "NBM"],
    ["ensemble_raw_median_high_f", "Ensemble"],
    ["model_expected_high_f", "Model"],
  ];
  const rows = [];
  for (const point of timeline || []) {
    for (const [key, label] of mappings) {
      if (isNumber(point[key])) rows.push({ x: point.snapshot_time_utc, value: Number(point[key]), group: label, event_key: point.event_key });
    }
  }
  return rows;
}

function eventDetailsHtml(event, snapshotCount) {
  const items = [
    ["City", event.city?.toUpperCase()],
    ["Date", event.target_date],
    ["Final high", event.final_high_f],
    ["Winner", event.winner_label],
    ["Snapshots", snapshotCount],
  ];
  return `<div class="detail-list">${items.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "n/a")}</strong></div>`).join("")}</div>`;
}

async function derivedPerformanceRows() {
  if (state.cache.derived.performance) return state.cache.derived.performance;
  const rows = await getAnalysis("model_error_rows");
  const groups = new Map();
  for (const row of rows || []) {
    const city = row.city || "pooled";
    const checkpoint = row.checkpoint || "unknown";
    const type = row.metric_type || (isNumber(row.error_f) ? "temperature" : "bracket");
    const key = `${city}|${checkpoint}|${type}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  const output = [];
  for (const [key, groupRows] of groups) {
    const [city, checkpoint, metricType] = key.split("|");
    if (metricType === "temperature") {
      const errors = groupRows.map((row) => Number(row.error_f)).filter(Number.isFinite);
      if (errors.length) {
        output.push({ city, checkpoint, metric_type: metricType, metric: "mae", value: avg(errors.map(Math.abs)), count: errors.length });
        output.push({ city, checkpoint, metric_type: metricType, metric: "rmse", value: Math.sqrt(avg(errors.map((value) => value * value))), count: errors.length });
        output.push({ city, checkpoint, metric_type: metricType, metric: "bias", value: avg(errors), count: errors.length });
      }
    }
    for (const metric of ["log_loss", "brier", "rps", "winner_probability", "top_one_accuracy", "within_1f", "within_2f"]) {
      const values = groupRows.map((row) => Number(row[metric])).filter(Number.isFinite);
      if (values.length) output.push({ city, checkpoint, metric_type: "bracket", metric, value: avg(values), count: values.length });
    }
  }
  state.cache.derived.performance = output;
  return output;
}

async function derivedCalibrationRows() {
  if (state.cache.derived.calibration) return state.cache.derived.calibration;
  const rows = (await getAnalysis("market_model_points")).filter((row) => isNumber(row.model_probability));
  const groups = new Map();
  for (const row of rows) {
    const probability = Math.max(0, Math.min(0.999999, Number(row.model_probability)));
    const bin = (Math.floor(probability * 10) / 10).toFixed(1);
    const key = `${row.city || "pooled"}|${row.checkpoint || "unknown"}|${row.model_name || "model"}|${bin}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  const output = [];
  for (const [key, groupRows] of groups) {
    const [city, checkpoint, modelName, probabilityBin] = key.split("|");
    output.push({
      city,
      checkpoint,
      model_name: modelName,
      probability_bin: probabilityBin,
      count: groupRows.length,
      mean_probability: avg(groupRows.map((row) => Number(row.model_probability)).filter(Number.isFinite)),
      observed_frequency: avg(groupRows.map((row) => (row.is_winner ? 1 : 0))),
    });
  }
  state.cache.derived.calibration = output;
  return output;
}

function chartCard({ id, title, chart, legendGroups = [] }) {
  const focused = state.focusChart !== "__compact__" && (state.focusChart === "" || state.focusChart === id);
  return `<section class="chart-card ${focused ? "focused" : ""}" id="${escapeHtml(id)}">
    <header class="card-head">
      <div><h3>${escapeHtml(title)}</h3></div>
      <div class="card-actions">${legendHtml(legendGroups)}<button type="button" class="ghost tiny" data-focus-chart="${escapeHtml(id)}">${focused ? "Compact" : "Focus"}</button></div>
    </header>
    <div class="chart-body">${chart || emptyState("No chart data available.")}</div>
  </section>`;
}

function lineChart(rows, config, referenceValue = null, chartId = "chart") {
  rows = (rows || []).filter((row) => row[config.x] && isNumber(row[config.y]));
  if (!rows.length) return emptyState("No points match the current filters.");
  const groups = unique(rows.map((row) => row[config.group] || "value"));
  const svg = svgFrame(rows, config.x, config.y, config.xLabel, config.yLabel, ({ xScale, yScale, width, height, left, top, bottom }) => {
    const parts = [];
    if (isNumber(referenceValue)) {
      const y = yScale(Number(referenceValue));
      parts.push(`<line x1="${left}" y1="${y}" x2="${width - 18}" y2="${y}" class="ref-line"></line>`);
    }
    groups.forEach((group, index) => {
      const series = rows.filter((row) => (row[config.group] || "value") === group).sort((a, b) => String(a[config.x]).localeCompare(String(b[config.x])));
      const points = series.map((row) => `${xScale(row[config.x])},${yScale(Number(row[config.y]))}`).join(" ");
      parts.push(`<polyline points="${points}" fill="none" stroke="${COLORS[index % COLORS.length]}" stroke-width="2.5"></polyline>`);
      for (const row of series) {
        parts.push(`<circle cx="${xScale(row[config.x])}" cy="${yScale(Number(row[config.y]))}" r="4" fill="${COLORS[index % COLORS.length]}" data-event-key="${escapeHtml(row.event_key || "")}" data-hover-readout="${escapeHtml(`${group}: ${formatNumber(row[config.y])} at ${row[config.x]}`)}"></circle>`);
      }
    });
    return parts.join("");
  });
  return svg;
}

function scatterPlot(rows, xKey, yKey, groupKey, options) {
  rows = (rows || []).filter((row) => isNumber(row[xKey]) && isNumber(row[yKey]));
  if (!rows.length) return emptyState("No points match the current filters.");
  const groups = unique(rows.map((row) => row[groupKey] || "value"));
  return svgFrame(rows, xKey, yKey, options.xLabel, options.yLabel, ({ xScale, yScale }) => {
    return rows.map((row) => {
      const group = row[groupKey] || "value";
      const color = COLORS[Math.max(0, groups.indexOf(group)) % COLORS.length];
      return `<circle cx="${xScale(Number(row[xKey]))}" cy="${yScale(Number(row[yKey]))}" r="4.5" fill="${color}" opacity="0.85" data-event-key="${escapeHtml(row.event_key || "")}" data-hover-readout="${escapeHtml(`${group}: ${xKey} ${formatNumber(row[xKey])}, ${yKey} ${formatNumber(row[yKey])}`)}"></circle>`;
    }).join("");
  });
}

function groupedBars(rows, labelKey, groupKey, valueKey, options) {
  rows = (rows || []).filter((row) => row[labelKey] !== undefined && isNumber(row[valueKey]));
  if (!rows.length) return emptyState("No bars match the current filters.");
  const labels = sortedLabels(unique(rows.map((row) => String(row[labelKey]))));
  const groups = sortedLabels(unique(rows.map((row) => String(row[groupKey] || "value"))));
  const values = rows.map((row) => Number(row[valueKey]));
  const minValue = Math.min(0, ...values);
  const maxValue = Math.max(1, ...values);
  const width = 980;
  const height = 390;
  const left = 74;
  const top = 22;
  const bottom = 76;
  const plotW = width - left - 20;
  const plotH = height - top - bottom;
  const yScale = (value) => top + (maxValue - value) / (maxValue - minValue || 1) * plotH;
  const clusterW = plotW / labels.length;
  const barW = Math.max(5, (clusterW * 0.72) / Math.max(1, groups.length));
  const bars = [];
  labels.forEach((label, labelIndex) => {
    if (labelIndex % 2 === 0) bars.push(`<rect x="${left + labelIndex * clusterW}" y="${top}" width="${clusterW}" height="${plotH}" class="group-band"></rect>`);
    groups.forEach((group, groupIndex) => {
      const row = rows.find((item) => String(item[labelKey]) === label && String(item[groupKey] || "value") === group);
      if (!row) return;
      const value = Number(row[valueKey]);
      const x = left + labelIndex * clusterW + clusterW * 0.14 + groupIndex * barW;
      const y = yScale(Math.max(0, value));
      const base = yScale(0);
      const h = Math.abs(base - yScale(value));
      bars.push(`<rect x="${x}" y="${Math.min(y, base)}" width="${barW * 0.86}" height="${Math.max(1, h)}" rx="3" fill="${COLORS[groupIndex % COLORS.length]}" data-event-key="${escapeHtml(row.event_key || "")}" data-hover-readout="${escapeHtml(`${label} / ${group}: ${formatNumber(value)}`)}"></rect>`);
    });
  });
  return `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img">
    ${axisMarkup({ width, height, left, top, bottom, xLabel: options.xLabel, yLabel: options.yLabel, xTicks: labels.slice(0, 14), yTicks: ticks(minValue, maxValue, 5), yScale })}
    ${bars.join("")}
  </svg>`;
}

function heatmap(rows, xKey, yKey, valueKey, options) {
  rows = (rows || []).filter((row) => row[xKey] !== undefined && row[yKey] !== undefined && isNumber(row[valueKey]));
  if (!rows.length) return emptyState("No heatmap cells match the current filters.");
  const xs = sortedLabels(unique(rows.map((row) => String(row[xKey]))));
  const ys = sortedLabels(unique(rows.map((row) => String(row[yKey]))));
  const values = rows.map((row) => Number(row[valueKey]));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = 980;
  const height = Math.max(340, 112 + ys.length * 36);
  const left = 112;
  const top = 30;
  const cellW = (width - left - 24) / xs.length;
  const cellH = Math.min(36, (height - top - 104) / ys.length);
  const showValues = options.valueMode === "always" || (options.valueMode === "auto" && cellW > 56 && cellH > 22);
  const cells = rows.map((row) => {
    const x = left + xs.indexOf(String(row[xKey])) * cellW;
    const y = top + ys.indexOf(String(row[yKey])) * cellH;
    const value = Number(row[valueKey]);
    return `<g data-event-key="${escapeHtml(row.event_key || "")}" data-hover-readout="${escapeHtml(`${row[yKey]} / ${row[xKey]}: ${formatNumber(value)}`)}">
      <rect x="${x + 1}" y="${y + 1}" width="${Math.max(1, cellW - 2)}" height="${Math.max(1, cellH - 2)}" rx="4" fill="${heatColor(value, min, max)}"></rect>
      ${showValues ? `<text x="${x + cellW / 2}" y="${y + cellH / 2 + 4}" text-anchor="middle" class="heat-value">${escapeHtml(formatNumber(value))}</text>` : ""}
    </g>`;
  });
  return `<svg class="chart-svg heatmap-svg" viewBox="0 0 ${width} ${height}" role="img">
    <text x="${width / 2}" y="${height - 16}" text-anchor="middle" class="axis-label">${escapeHtml(options.xLabel)}</text>
    <text x="20" y="${height / 2}" transform="rotate(-90 20 ${height / 2})" text-anchor="middle" class="axis-label">${escapeHtml(options.yLabel)}</text>
    ${xs.map((x, i) => `<text x="${left + i * cellW + cellW / 2}" y="${height - 48}" text-anchor="middle" class="tick-label">${escapeHtml(shortLabel(x))}</text>`).join("")}
    ${ys.map((y, i) => `<text x="${left - 8}" y="${top + i * cellH + cellH / 2 + 4}" text-anchor="end" class="tick-label">${escapeHtml(y)}</text>`).join("")}
    ${cells.join("")}
    ${heatLegend(width, height, min, max)}
  </svg>`;
}

function stackedBars(rows, labelKey, valueKey, options) {
  rows = (rows || []).filter((row) => row[labelKey] !== undefined && isNumber(row[valueKey]));
  return groupedBars(rows.map((row) => ({ ...row, group: "probability" })), labelKey, "group", valueKey, options);
}

function svgFrame(rows, xKey, yKey, xLabel, yLabel, draw) {
  const width = 980;
  const height = 390;
  const left = 76;
  const top = 20;
  const bottom = 76;
  const xValues = rows.map((row) => row[xKey]);
  const yValues = rows.map((row) => Number(row[yKey])).filter(Number.isFinite);
  const yMin = Math.min(...yValues);
  const yMax = Math.max(...yValues);
  const xScale = makeXScale(xValues, left, width - 18);
  const yScale = makeYScale(yMin, yMax, top, height - bottom);
  return `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img">
    ${axisMarkup({ width, height, left, top, bottom, xLabel, yLabel, xTicks: sortedLabels(unique(xValues)).slice(0, 8), yTicks: ticks(yMin, yMax, 5), yScale })}
    ${draw({ xScale, yScale, width, height, left, top, bottom })}
  </svg>`;
}

function axisMarkup({ width, height, left, top, bottom, xLabel, yLabel, xTicks, yTicks, yScale }) {
  const plotBottom = height - bottom;
  const grid = yTicks.map((tick) => `<line x1="${left}" y1="${yScale(tick)}" x2="${width - 18}" y2="${yScale(tick)}" class="grid-line"></line><text x="${left - 10}" y="${yScale(tick) + 4}" text-anchor="end" class="tick-label">${escapeHtml(formatNumber(tick))}</text>`).join("");
  const xLabels = xTicks.map((tick, index) => `<text x="${left + (index / Math.max(1, xTicks.length - 1)) * (width - left - 28)}" y="${height - 48}" text-anchor="middle" class="tick-label">${escapeHtml(shortLabel(tick))}</text>`).join("");
  return `${grid}<line x1="${left}" y1="${plotBottom}" x2="${width - 18}" y2="${plotBottom}" class="axis-line"></line>${xLabels}<text x="${width / 2}" y="${height - 15}" text-anchor="middle" class="axis-label">${escapeHtml(xLabel || "")}</text><text x="20" y="${height / 2}" transform="rotate(-90 20 ${height / 2})" text-anchor="middle" class="axis-label">${escapeHtml(yLabel || "")}</text>`;
}

function makeXScale(values, minPx, maxPx) {
  const numeric = values.every((value) => isNumber(value));
  if (numeric) {
    const min = Math.min(...values.map(Number));
    const max = Math.max(...values.map(Number));
    return (value) => minPx + ((Number(value) - min) / (max - min || 1)) * (maxPx - minPx);
  }
  const ordered = sortedLabels(unique(values));
  return (value) => minPx + (ordered.indexOf(value) / Math.max(1, ordered.length - 1)) * (maxPx - minPx);
}

function sortedLabels(values) {
  return [...values].sort((a, b) => {
    if (isNumber(a) && isNumber(b)) return Number(a) - Number(b);
    const dateA = Date.parse(a);
    const dateB = Date.parse(b);
    if (Number.isFinite(dateA) && Number.isFinite(dateB)) return dateA - dateB;
    const checkpointA = checkpointOrder(a);
    const checkpointB = checkpointOrder(b);
    if (checkpointA !== null && checkpointB !== null) return checkpointA - checkpointB;
    return String(a).localeCompare(String(b), undefined, { numeric: true });
  });
}

function checkpointOrder(value) {
  const match = String(value).match(/t_(minus|plus)_(\d+)h/);
  if (!match) return null;
  const hours = Number(match[2]);
  return match[1] === "minus" ? -hours : hours;
}

function makeYScale(min, max, top, bottom) {
  const pad = (max - min || 1) * 0.08;
  const lo = min - pad;
  const hi = max + pad;
  return (value) => bottom - ((value - lo) / (hi - lo || 1)) * (bottom - top);
}

function ticks(min, max, count) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  return Array.from({ length: count }, (_, index) => min + ((max - min) * index) / (count - 1));
}

function heatColor(value, min, max) {
  const t = (value - min) / (max - min || 1);
  const hue = 150 - t * 120;
  const light = 86 - t * 38;
  return `hsl(${hue} 35% ${light}%)`;
}

function heatLegend(width, height, min, max) {
  const x = width - 230;
  const y = height - 30;
  const steps = 8;
  const rects = Array.from({ length: steps }, (_, index) => {
    const value = min + ((max - min) * index) / Math.max(1, steps - 1);
    return `<rect x="${x + index * 18}" y="${y}" width="18" height="9" fill="${heatColor(value, min, max)}"></rect>`;
  }).join("");
  return `<g class="heat-legend">
    <text x="${x}" y="${y - 6}" class="tick-label">Low</text>
    ${rects}
    <text x="${x + steps * 18 + 8}" y="${y + 8}" class="tick-label">High</text>
    <text x="${x + 70}" y="${y + 24}" text-anchor="middle" class="tick-label">${escapeHtml(formatNumber(min))} to ${escapeHtml(formatNumber(max))}</text>
  </g>`;
}

function tableCard(id, title, rows, columns, collapsed = true, primary = false) {
  return `<section class="table-card ${primary ? "primary-table" : ""}">
    <header class="card-head">
      <h3>${escapeHtml(title)}</h3>
    </header>
    ${tableHtml(id, rows || [], columns)}
  </section>`;
}

function tableHtml(id, rows, columns) {
  const sorted = sortRows(id, rows);
  if (!sorted.length) return emptyState("No table rows match the current filters.");
  return `<div class="table-wrap"><table><thead><tr>${columns.map((column) => tableHeaderHtml(id, column)).join("")}</tr></thead><tbody>${sorted
    .map((row) => `<tr data-event-key="${escapeHtml(row.event_key || "")}">${columns.map((column) => `<td>${escapeHtml(formatCell(row[column]))}</td>`).join("")}</tr>`)
    .join("")}</tbody></table></div>`;
}

function tableHeaderHtml(id, column) {
  const sort = state.tableSort[id];
  const indicator = sort?.column === column ? (sort.direction === "asc" ? " ▲" : " ▼") : "";
  return `<th><button type="button" data-sort-table="${escapeHtml(id)}" data-sort-column="${escapeHtml(column)}">${escapeHtml(column)}${indicator}</button></th>`;
}

function isTableCollapsed(id, fallback) {
  return state.tableCollapsed[id] === undefined ? fallback : state.tableCollapsed[id];
}

function cycleTableSort(id, column) {
  const current = state.tableSort[id];
  if (!current || current.column !== column) state.tableSort[id] = { column, direction: "asc" };
  else if (current.direction === "asc") state.tableSort[id] = { column, direction: "desc" };
  else delete state.tableSort[id];
}

function sortRows(id, rows) {
  const sort = state.tableSort[id];
  if (!sort) return rows;
  return [...rows].sort((a, b) => compareValues(a[sort.column], b[sort.column]) * (sort.direction === "asc" ? 1 : -1));
}

function compareValues(a, b) {
  if (isNumber(a) && isNumber(b)) return Number(a) - Number(b);
  return String(a ?? "").localeCompare(String(b ?? ""));
}

function summaryCardHtml([label, value]) {
  return `<div class="summary-card"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`;
}

function legendForRows(rows, key) {
  return unique((rows || []).map((row) => row[key]).filter((value) => value !== undefined && value !== null && value !== "")).slice(0, 8);
}

function legendHtml(groups) {
  if (!groups?.length) return "";
  return `<div class="legend">${groups.map((group, index) => `<span><i style="background:${COLORS[index % COLORS.length]}"></i>${escapeHtml(shortLabel(group))}</span>`).join("")}</div>`;
}

function emptyState(message) {
  return `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function setSourceStatus(message, isError) {
  const status = document.getElementById("sourceStatus");
  status.hidden = false;
  status.textContent = message;
  status.classList.toggle("error", Boolean(isError));
}

function hideSourceStatus() {
  document.getElementById("sourceStatus").hidden = true;
}

function smoothRows(rows, windowSize) {
  const grouped = groupBy(rows, "group");
  const output = [];
  for (const groupRows of grouped.values()) {
    const sorted = [...groupRows].sort((a, b) => String(a.x).localeCompare(String(b.x)));
    sorted.forEach((row, index) => {
      const slice = sorted.slice(Math.max(0, index - windowSize + 1), index + 1);
      output.push({ ...row, value: avg(slice.map((item) => Number(item.value)).filter(Number.isFinite)) });
    });
  }
  return output;
}

function groupBy(rows, key) {
  const groups = new Map();
  for (const row of rows || []) {
    const value = row[key] || "value";
    if (!groups.has(value)) groups.set(value, []);
    groups.get(value).push(row);
  }
  return groups;
}

function avg(values) {
  const valid = values.filter(Number.isFinite);
  if (!valid.length) return null;
  return valid.reduce((sum, value) => sum + value, 0) / valid.length;
}

function unique(values) {
  return [...new Set((values || []).filter((value) => value !== undefined && value !== null && value !== ""))];
}

function isNumber(value) {
  return value !== null && value !== "" && Number.isFinite(Number(value));
}

function formatCell(value) {
  if (isNumber(value)) return formatNumber(value);
  return value ?? "";
}

function formatNumber(value) {
  if (!isNumber(value)) return String(value ?? "n/a");
  const number = Number(value);
  if (Math.abs(number) >= 100) return number.toFixed(0);
  if (Math.abs(number) >= 10) return number.toFixed(1);
  return number.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}

function shortLabel(value) {
  const text = String(value ?? "");
  if (text.includes("T")) return text.slice(5, 16).replace("T", " ");
  return text.replaceAll("_", " ");
}

function titleCase(value) {
  return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

window.addEventListener("DOMContentLoaded", () => {
  void init();
});
 
