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
    controls: ["trendMetricMulti", "dateRange", "cities", "smoothing"],
    render: renderTrendExplorer,
    defaults: () => ({ metrics: [firstMetric()], axisOrder: [], dateMode: "range", date: dateStart(), start: dateStart(), end: dateEnd(), cities: allCities(), cityMode: "multi", smoothing: "0" }),
  },
  replay: {
    group: "Weather",
    label: "Event Replay",
    description: "Replay one city-day timeline from snapshots to final result.",
    controls: ["singleCity", "singleDate", "checkpoint", "replaySourceMetricMulti", "replayOverlayMetricMulti"],
    render: renderReplay,
    defaults: () => ({
      city: allCities()[0] || "",
      date: dateStart(),
      checkpoint: "",
      sourceMetrics: ["nws_anchor_high_f", "observed_high_so_far_f", "hrrr_projected_high_f", "nbm_projected_high_f", "ensemble_raw_median_high_f"],
      overlayMetrics: [],
      axisOrder: [],
    }),
  },
  disagreement: {
    group: "Weather",
    label: "Source Disagreement",
    description: "Find when NWS, observations, HRRR, NBM, and ensembles materially diverged.",
    controls: ["disagreementMetricMulti", "threshold", "dateRange", "cities"],
    render: renderDisagreement,
    defaults: () => ({ metrics: ["hrrr_minus_nws", "nbm_minus_nws", "ensemble_minus_nws"], axisOrder: [], threshold: "3", dateMode: "range", date: dateStart(), start: dateStart(), end: dateEnd(), cities: allCities(), cityMode: "multi" }),
  },
  settlements: {
    group: "Weather",
    label: "Settlement Grid",
    description: "Scan final highs, settled brackets, and model misses by city and target date.",
    controls: ["dateRange", "cities", "cellValue", "heatmapValues"],
    render: renderSettlementGrid,
    defaults: () => ({ dateMode: "range", date: dateStart(), start: dateStart(), end: dateEnd(), cities: allCities(), cityMode: "multi", cellValue: "final_high_f", heatmapValues: "auto" }),
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
    defaults: () => ({ xFeature: defaultFeature(), yError: "absolute_error_f", dateMode: "range", date: dateStart(), start: dateStart(), end: dateEnd(), cities: allCities(), cityMode: "multi", checkpoints: allCheckpoints() }),
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
    controls: ["marketTimelineMetricMulti", "dateRange", "cities", "checkpoint", "candidateScope"],
    render: renderMarketModel,
    defaults: () => ({ timelineMetrics: ["model_probability", "yes_ask_dollars", "normalized_market_midpoint_probability"], axisOrder: [], dateMode: "range", date: dateStart(), start: dateStart(), end: dateEnd(), cities: allCities(), cityMode: "multi", checkpoint: "", candidateScope: "all" }),
  },
};

const PERFORMANCE_METRIC_PRESETS = {
  core: ["mae", "rmse", "bias", "log_loss", "rps", "winner_probability"],
  temperature: ["mae", "rmse", "bias", "within_1f", "within_2f"],
  bracket: ["log_loss", "brier", "rps", "winner_probability", "top_one_accuracy"],
  all: ["mae", "rmse", "bias", "within_1f", "within_2f", "log_loss", "brier", "rps", "winner_probability", "top_one_accuracy"],
};

const SOURCE_TEMPERATURE_KEYS = [
  "nws_anchor_high_f",
  "observed_high_so_far_f",
  "hrrr_projected_high_f",
  "nbm_projected_high_f",
  "ensemble_raw_median_high_f",
  "model_expected_high_f",
  "predicted_high_f",
  "actual_high_f",
  "final_high_f",
  "settlement_temperature_f",
  "final_high_by_day",
  "nws_anchor_by_snapshot",
  "observed_high_by_snapshot",
  "hrrr_projected_high_by_snapshot",
  "nbm_projected_high_by_snapshot",
  "ensemble_median_by_snapshot",
  "model_expected_high_by_snapshot",
];

const SOURCE_DELTA_KEYS = [
  "hrrr_minus_nws",
  "nbm_minus_nws",
  "ensemble_minus_nws",
  "observed_minus_nws",
  "hrrr_minus_nbm",
  "temperature_error_f",
  "error_f",
  "bias",
];

const SOURCE_SPREAD_KEYS = [
  "source_range_f",
  "all_weather_sources_range_f",
  "weather_source_stddev_f",
  "absolute_error_f",
  "absolute_temperature_error_f",
  "mae",
  "rmse",
  "bracket_miss_distance",
];

const PROBABILITY_KEYS = [
  "winner_probability",
  "model_top_probability",
  "model_probability",
  "market_top_probability",
  "market_top_probability_by_snapshot",
  "normalized_market_midpoint_probability",
  "market_probability",
  "yes_midpoint",
  "top_one_accuracy",
  "within_1f",
  "within_2f",
  "within_one_bracket",
  "observed_frequency",
  "mean_probability",
  "brier",
  "rps",
];

const PRICE_KEYS = [
  "yes_ask_dollars",
  "settled_bracket_ask_by_snapshot",
];

const DISAGREEMENT_METRICS = [
  { key: "hrrr_minus_nws", label: "HRRR - NWS" },
  { key: "nbm_minus_nws", label: "NBM - NWS" },
  { key: "ensemble_minus_nws", label: "Ensemble - NWS" },
  { key: "observed_minus_nws", label: "Observed - NWS" },
  { key: "hrrr_minus_nbm", label: "HRRR - NBM" },
  { key: "source_range_f", label: "Source range" },
  { key: "weather_source_stddev_f", label: "Source stddev" },
  { key: "all_weather_sources_range_f", label: "All-source range" },
];

const REPLAY_SOURCE_METRICS = [
  { key: "nws_anchor_high_f", label: "NWS" },
  { key: "observed_high_so_far_f", label: "Observed" },
  { key: "hrrr_projected_high_f", label: "HRRR" },
  { key: "nbm_projected_high_f", label: "NBM" },
  { key: "ensemble_raw_median_high_f", label: "Ensemble" },
  { key: "model_expected_high_f", label: "Model" },
];

const REPLAY_OVERLAY_METRICS = [
  { key: "market_top_probability", label: "Market top probability" },
  { key: "market_top_ask", label: "Market top ask" },
];

const MARKET_TIMELINE_METRICS = [
  { key: "model_probability", label: "Model probability" },
  { key: "yes_ask_dollars", label: "YES ask" },
  { key: "normalized_market_midpoint_probability", label: "Market midpoint" },
  { key: "model_minus_ask", label: "Model - ask" },
  { key: "model_minus_market", label: "Model - market" },
];

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
  const modeControls = document.getElementById("modeControls");
  modeControls.addEventListener("change", (event) => handleControlChange(event));
  modeControls.addEventListener("click", (event) => {
    handleControlClick(event);
  });
  document.getElementById("contentTabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-tab]");
    if (!button) return;
    state.activeTabs[state.mode] = button.dataset.tab;
    void render();
  });
  document.getElementById("content").addEventListener("click", (event) => {
    const focus = event.target.closest("[data-focus-chart]");
    if (focus) {
      const clickedId = focus.dataset.focusChart;
      state.focusChart = state.focusChart === clickedId ? "" : clickedId;
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
  suppressAutofill(document.getElementById("unloadedPanel"));
  suppressAutofill(document.getElementById("workspacePanel"));
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
  const summaryText = document.getElementById("metricSummaryText");
  if (summaryText) {
    summaryText.textContent = `${state.metadata.cities.length || 0} cities, ${overview.snapshot_count || 0} snapshots`;
  }
  document.getElementById("metricPills").innerHTML = pills
    .map(([label, value]) => `<div><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`)
    .join("");
}

function renderControls(def) {
  const controls = def.controls || [];
  const wrap = document.getElementById("modeControls");
  const openDropdowns = new Set(
    [...wrap.querySelectorAll("details[data-dropdown-key]")].filter((details) => details.open).map((details) => details.dataset.dropdownKey),
  );
  const items = controls.length
    ? controls.map((control) => controlHtml(control)).filter(Boolean)
    : [`<div class="muted-note">No parameters for this view.</div>`];
  items.push(`<div class="muted-note controls-footer">Select chart marks or table rows for details.</div>`);
  wrap.innerHTML = items.join("");
  wrap.querySelectorAll("details[data-dropdown-key]").forEach((details) => {
    if (openDropdowns.has(details.dataset.dropdownKey)) details.open = true;
  });
  suppressAutofill(wrap);
  const resetButton = document.getElementById("resetFiltersButton");
  resetButton.disabled = isDefaultParams();
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
  void html;
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
  const metricOptions = trendMetricOptions();
  const selectedMetrics = sanitizeMetricSelection(params.metrics?.length ? params.metrics : [firstMetric()], metricOptions, params.axisOrder || []);
  if (!selectedMetrics.length) selectedMetrics.push(firstMetric());
  params.metrics = selectedMetrics;
  let rows = [];
  for (const metric of selectedMetrics) {
    const metricRows = filterRows(await getSeries(metric), params, { groupKey: "group", timeKey: "x" })
      .map((row) => ({
        ...row,
        metric,
        metricLabel: metricLabelForKey(metric, metricOptions),
        group: row.group,
      }));
    rows.push(...metricRows);
  }
  if (Number(params.smoothing || 0)) rows = smoothRows(rows, Number(params.smoothing));
  const domains = await trendDomainsForMetrics(selectedMetrics, params.axisOrder || []);
  const selectedCities = params.cities?.length ? params.cities : allCities();
  const charts = selectedCities
    .map((city) => {
      const cityRows = rows.filter((row) => row.group === city);
      if (!cityRows.length) return "";
      return chartCard({
        id: `trend-main-${city}`,
        title: `${city.toUpperCase()} Trend`,
        chart: `${densityNote(cityRows)}${multiAxisLineChart(cityRows, {
          metrics: selectedMetrics,
          axisOrder: params.axisOrder || [],
          domains,
          xLabel: "Time",
        })}`,
        legendGroups: multiAxisLegend(selectedMetrics, metricOptions, params.axisOrder || []),
      });
    });
  return [{
    id: "trend",
    label: "Trend",
    html: chartGrid(charts) || emptyState("No selected trend metrics match the current filters."),
  }];
}

async function renderReplay() {
  const params = currentParams();
  const eventKey = selectedEventKey(params);
  const event = eventKey ? await getEvent(eventKey) : null;
  if (!event) return [{ id: "empty", label: "Replay", html: emptyState("No city-day matches the selected city and date.") }];
  params.sourceMetrics = sanitizeMetricSelection(params.sourceMetrics || [], REPLAY_SOURCE_METRICS, params.axisOrder || []);
  params.overlayMetrics = sanitizeMetricSelection(params.overlayMetrics || [], REPLAY_OVERLAY_METRICS, params.axisOrder || []);
  if (!params.sourceMetrics.length && !params.overlayMetrics.length) params.sourceMetrics = ["nws_anchor_high_f"];
  const timeline = params.checkpoint ? (event.timeline || []).filter((row) => row.checkpoint === params.checkpoint) : event.timeline || [];
  const metricOptions = [...REPLAY_SOURCE_METRICS, ...REPLAY_OVERLAY_METRICS];
  const selectedMetrics = [...params.sourceMetrics, ...params.overlayMetrics];
  const lineRows = replayMetricRows(timeline, selectedMetrics, metricOptions);
  const fullLineRows = replayMetricRows(event.timeline || [], selectedMetrics, metricOptions);
  if (isNumber(event.final_high_f)) {
    fullLineRows.push({ x: timeline.at(-1)?.snapshot_time_utc || event.target_date, group: event.city, metric: "final_high_f", metricLabel: "Final high", value: event.final_high_f, event_key: event.event_key });
  }
  const replayDomains = domainsForMetricRows(fullLineRows, selectedMetrics, params.axisOrder || []);
  const latestSnapshot = timeline.at(-1)?.snapshot_time_utc;
  const latestProbs = (event.bracket_probabilities || []).filter((row) => row.snapshot_time_utc === latestSnapshot);
  const eventOutcome = eventOutcomeHtml(event, timeline.length);
  renderDetails("");
  return [
    {
      id: "timeline",
      label: "Timeline",
      html: chartCard({
        id: "replay-timeline",
        title: `${event.city?.toUpperCase()} ${event.target_date || ""}`,
        metaHtml: eventOutcome,
        chart: multiAxisLineChart(lineRows, {
          metrics: selectedMetrics,
          axisOrder: params.axisOrder || [],
          domains: replayDomains,
          xLabel: "Snapshot time",
        }),
        legendGroups: multiAxisLegend(selectedMetrics, metricOptions, params.axisOrder || []),
      }),
    },
    {
      id: "brackets",
      label: "Brackets",
      html: chartCard({
        id: "replay-brackets",
        title: "Latest Bracket Probabilities",
        metaHtml: eventOutcome,
        chart: stackedBars(latestProbs, "market_ticker", "model_probability", { xLabel: "Bracket", yLabel: "Probability", yDomain: domainForValues([], "probability"), chartId: "replay-brackets" }),
        legendGroups: legendForRows(latestProbs, "market_ticker"),
      }),
    },
    {
      id: "tables",
      label: "Details",
      html: `${eventOutcome}<div class="spacer-stack">${tableCard("replay-timeline-table", "Replay Timeline", timeline, ["snapshot_time_utc", "checkpoint", "nws_anchor_high_f", "observed_high_so_far_f", "hrrr_projected_high_f", "nbm_projected_high_f", "market_top_ticker", "market_top_probability"], false, false)}${tableCard("replay-matches", "Matching City-Days", matchingEvents(params), ["city", "target_date", "event_ticker", "station_id"], true, true)}</div>`,
    },
  ];
}

async function renderPerformance() {
  const params = currentParams();
  const selectedMetrics = new Set(params.metrics || PERFORMANCE_METRIC_PRESETS.core);
  const allRows = await derivedPerformanceRows();
  const rows = filterRows(allRows, params, { groupKey: "city" })
    .filter((row) => selectedMetrics.has(row.metric))
    .filter((row) => !params.checkpoints?.length || params.checkpoints.includes(row.checkpoint));
  const selectedCities = params.cities?.length ? params.cities : allCities();
  const rowColorDomains = performanceRowDomains(allRows, [...selectedMetrics]);
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
          rowColorDomains,
          colorDomain: domainForRows(rows, "value", "auto"),
        }),
      });
    });
  return [
    {
      id: "heatmap",
      label: "Heatmap",
      html: chartGrid(heatmaps) || emptyState("No checkpoint metrics match the selected cities, checkpoints, and metrics."),
    },
    {
      id: "bars",
      label: "Bars",
      html: chartGrid(selectedCities
        .map((city) => {
          const cityRows = rows.filter((row) => row.city === city);
          if (!cityRows.length) return "";
          return chartCard({
            id: `performance-bars-${city}`,
            title: `${city.toUpperCase()} Grouped Metrics`,
            chart: groupedBars(cityRows, "checkpoint", "metric", "value", { xLabel: "Checkpoint", yLabel: "Metric value", yDomain: groupedMetricDomain(rows), chartId: `performance-bars-${city}` }),
            legendGroups: legendForRows(cityRows, "metric"),
          });
        })) || emptyState("No grouped metrics match the selected filters."),
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
  const allRows = await getAnalysis("feature_error_points");
  const rows = filterRows(allRows, params, { groupKey: "city", timeKey: "snapshot_time_utc" })
    .filter((row) => !params.checkpoints?.length || params.checkpoints.includes(row.checkpoint));
  const xDomain = featureAxisDomain(allRows, params.xFeature);
  const yDomain = featureAxisDomain(allRows, params.yError);
  return [
    {
      id: "scatter",
      label: "Scatter",
      html: chartCard({
        id: "feature-scatter",
        title: "Feature vs Error",
        chart: scatterPlot(rows, params.xFeature, params.yError, "city", { xLabel: params.xFeature, yLabel: params.yError, xDomain, yDomain, chartId: "feature-scatter" }),
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
  const metricOptions = DISAGREEMENT_METRICS;
  const selectedMetrics = sanitizeMetricSelection(params.metrics?.length ? params.metrics : ["hrrr_minus_nws"], metricOptions, params.axisOrder || []);
  if (!selectedMetrics.length) selectedMetrics.push("hrrr_minus_nws");
  params.metrics = selectedMetrics;
  const allRows = await getAnalysis("source_disagreement");
  const rows = filterRows(allRows, params, { groupKey: "city", timeKey: "snapshot_time_utc" });
  const toMetricRows = (sourceRows) => selectedMetrics.flatMap((metric) => sourceRows
    .filter((row) => isNumber(row[metric]))
    .map((row) => ({
      x: row.snapshot_time_utc,
      group: row.city,
      metric,
      metricLabel: metricLabelForKey(metric, metricOptions),
      value: row[metric],
      event_key: row.event_key,
    })));
  const lineRows = toMetricRows(rows);
  const allMetricRows = toMetricRows(allRows);
  const threshold = Number(params.threshold || 3);
  const outliers = [...rows].filter((row) => selectedMetrics.some((metric) => Math.abs(Number(row[metric] || 0)) >= threshold))
    .sort((a, b) => Math.max(...selectedMetrics.map((metric) => Math.abs(Number(b[metric] || 0)))) - Math.max(...selectedMetrics.map((metric) => Math.abs(Number(a[metric] || 0)))));
  const domains = domainsForMetricRows(allMetricRows, selectedMetrics, params.axisOrder || []);
  const sourceTempDomain = sharedSourceDomain(allRows, "nws_anchor_high_f");
  const selectedCities = params.cities?.length ? params.cities : allCities();
  const charts = selectedCities
    .map((city) => {
      const cityRows = lineRows.filter((row) => row.group === city);
      if (!cityRows.length) return "";
      return chartCard({
        id: `disagreement-line-${city}`,
        title: `${city.toUpperCase()} Source Disagreement`,
        chart: `${densityNote(cityRows)}${multiAxisLineChart(cityRows, {
          metrics: selectedMetrics,
          axisOrder: params.axisOrder || [],
          domains,
          xLabel: "Snapshot time",
        })}`,
        legendGroups: multiAxisLegend(selectedMetrics, metricOptions, params.axisOrder || []),
      });
    });
  return [
    {
      id: "timeline",
      label: "Timeline",
      html: chartGrid(charts) || emptyState("No disagreement metrics match the current filters."),
    },
    {
      id: "scatter",
      label: "HRRR vs NWS",
      html: chartCard({
        id: "disagreement-scatter",
        title: "HRRR vs NWS",
        chart: scatterPlot(rows, "nws_anchor_high_f", "hrrr_projected_high_f", "city", { xLabel: "NWS anchor (F)", yLabel: "HRRR projected high (F)", xDomain: sourceTempDomain, yDomain: sourceTempDomain, chartId: "disagreement-scatter" }),
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
  const metricOptions = MARKET_TIMELINE_METRICS;
  const selectedMetrics = sanitizeMetricSelection(params.timelineMetrics?.length ? params.timelineMetrics : ["model_probability"], metricOptions, params.axisOrder || []);
  if (!selectedMetrics.length) selectedMetrics.push("model_probability");
  params.timelineMetrics = selectedMetrics;
  const allRows = await getAnalysis("market_model_points");
  let rows = filterRows(allRows, params, { groupKey: "city", timeKey: "snapshot_time_utc" });
  if (params.checkpoint) rows = rows.filter((row) => row.checkpoint === params.checkpoint);
  if (params.candidateScope === "winners") rows = rows.filter((row) => row.is_winner);
  if (params.candidateScope === "model_edge") rows = rows.filter((row) => Number(row.model_minus_ask || 0) > 0);
  const timelineRows = marketTimelineMetricRows(rows, selectedMetrics, metricOptions, params.candidateScope);
  const allTimelineRows = marketTimelineMetricRows(allRows, selectedMetrics, metricOptions, params.candidateScope);
  const timelineDomains = domainsForMetricRows(allTimelineRows, selectedMetrics, params.axisOrder || []);
  const selectedCities = params.cities?.length ? params.cities : allCities();
  const timelineCharts = selectedCities
    .map((city) => {
      const cityRows = timelineRows.filter((row) => row.group === city);
      if (!cityRows.length) return "";
      return chartCard({
        id: `market-model-timeline-${city}`,
        title: `${city.toUpperCase()} Market / Model Timeline`,
        chart: `${densityNote(cityRows)}${multiAxisLineChart(cityRows, {
          metrics: selectedMetrics,
          axisOrder: params.axisOrder || [],
          domains: timelineDomains,
          xLabel: "Snapshot time",
        })}`,
        legendGroups: multiAxisLegend(selectedMetrics, metricOptions, params.axisOrder || []),
      });
    });
  return [
    {
      id: "scatter",
      label: "Ask vs Model",
      html: chartCard({
        id: "market-model-scatter",
        title: "Model Probability vs Ask",
        chart: scatterPlot(rows, "yes_ask_dollars", "model_probability", "city", { xLabel: "YES ask ($)", yLabel: "Model probability", xDomain: domainForValues([], "probability"), yDomain: domainForValues([], "probability"), chartId: "market-model-scatter" }),
        legendGroups: legendForRows(rows, "city"),
      }),
    },
    {
      id: "timeline",
      label: "Timeline",
      html: chartGrid(timelineCharts) || emptyState("No market/model timeline metrics match the current filters."),
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
        chart: groupedBars(rows, "probability_bin", "city", "observed_frequency", { xLabel: "Probability bucket", yLabel: "Observed frequency", yDomain: domainForValues([], "probability"), chartId: "calibration-reliability" }),
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
  const allRows = await getAnalysis("settlement_grid");
  const rows = filterRows(allRows, params, { groupKey: "city" });
  return [
    {
      id: "matrix",
      label: "Matrix",
      html: chartCard({
        id: "settlement-grid",
        title: "Settlement Matrix",
        chart: heatmap(rows, "target_date", "city", params.cellValue, { xLabel: "Target date", yLabel: "City", valueMode: params.heatmapValues, colorDomain: domainForRows(allRows, params.cellValue, scaleTypeForKey(params.cellValue)), chartId: "settlement-grid" }),
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
  if (control === "trendMetricMulti") {
    return multiMetricPickerHtmlWithKey("Metrics", "trend-metric", trendMetricOptions(), params.metrics || [firstMetric()], params.axisOrder || [], control);
  }
  if (control === "disagreementMetricMulti") {
    return multiMetricPickerHtmlWithKey("Metrics", "disagreement-metric", DISAGREEMENT_METRICS, params.metrics || ["hrrr_minus_nws"], params.axisOrder || [], control);
  }
  if (control === "replaySourceMetricMulti") {
    return multiMetricPickerHtmlWithKey("Weather sources", "replay-source-metric", REPLAY_SOURCE_METRICS, params.sourceMetrics || [], params.axisOrder || [], control);
  }
  if (control === "replayOverlayMetricMulti") {
    return multiMetricPickerHtmlWithKey("Overlays", "replay-overlay-metric", REPLAY_OVERLAY_METRICS, params.overlayMetrics || [], params.axisOrder || [], control);
  }
  if (control === "marketTimelineMetricMulti") {
    return multiMetricPickerHtmlWithKey("Timeline metrics", "market-timeline-metric", MARKET_TIMELINE_METRICS, params.timelineMetrics || [], params.axisOrder || [], control);
  }
  if (control === "metric") {
    return fieldHtml("Metric", `<select data-param="metric">${(state.metadata.metrics || [])
      .map((metric) => `<option value="${escapeHtml(metric.key)}" ${params.metric === metric.key ? "selected" : ""}>${escapeHtml(metric.label || metric.key)}</option>`)
      .join("")}</select>`);
  }
  if (control === "dateRange") {
    return dateControlHtml(params);
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
    return checkboxMenuHtmlWithKey("Checkpoints", "checkpoint-multi", allCheckpoints(), params.checkpoints || allCheckpoints(), control);
  }
  if (control === "metricPreset") {
    const presets = [...Object.keys(PERFORMANCE_METRIC_PRESETS), "custom"];
    return fieldHtml("Metric preset", `<select data-param="metricPreset">${presets
      .map((preset) => `<option value="${preset}" ${params.metricPreset === preset ? "selected" : ""}>${escapeHtml(titleCase(preset))}</option>`)
      .join("")}</select>`);
  }
  if (control === "metricMulti") {
    return checkboxMenuHtmlWithKey("Metrics", "metric-multi", PERFORMANCE_METRIC_PRESETS.all, params.metrics || PERFORMANCE_METRIC_PRESETS.core, control);
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

function dateControlHtml(params) {
  const mode = params.dateMode === "single" ? "single" : "range";
  const min = escapeHtml(dateStart());
  const max = escapeHtml(dateEnd());
  const date = escapeHtml(params.date || params.start || dateStart());
  const start = escapeHtml(params.start || dateStart());
  const end = escapeHtml(params.end || dateEnd());
  const toggle = `<div class="segmented">
    <button type="button" class="${mode === "range" ? "active" : ""}" data-date-mode="range">Range</button>
    <button type="button" class="${mode === "single" ? "active" : ""}" data-date-mode="single">Single</button>
  </div>`;
  const inputAttrs = `min="${min}" max="${max}" autocomplete="off" data-form-type="other" data-lpignore="true" data-1p-ignore="true" data-bwignore="true"`;
  const body = mode === "single"
    ? `<input type="date" data-param="date" ${inputAttrs} value="${date}">`
    : `<div class="inline-controls">
      <input type="date" data-param="start" ${inputAttrs} value="${start}">
      <span>to</span>
      <input type="date" data-param="end" ${inputAttrs} value="${end}">
    </div>`;
  return `<div class="control-field double date-control"><div class="control-title"><label>Date filter</label>${toggle}</div>${body}</div>`;
}

function suppressAutofill(root) {
  if (!root) return;
  root.querySelectorAll("input, select, textarea").forEach((field, index) => {
    field.setAttribute("autocomplete", "off");
    field.setAttribute("autocorrect", "off");
    field.setAttribute("autocapitalize", "off");
    field.setAttribute("spellcheck", "false");
    field.setAttribute("data-lpignore", "true");
    field.setAttribute("data-1p-ignore", "true");
    field.setAttribute("data-bwignore", "true");
    field.setAttribute("data-form-type", "other");
    field.setAttribute("data-dashlane-rid", "ignore");
    if (!field.getAttribute("name")) {
      field.setAttribute("name", `trends-${field.dataset.param || field.dataset.checkKind || field.dataset.multiKind || field.type || "field"}-${index}`);
    }
  });
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
  return checkboxMenuHtmlWithKey(label, kind, values, selected, kind);
}

function checkboxMenuHtmlWithKey(label, kind, values, selected, dropdownKey) {
  const selectedSet = new Set(selected || []);
  const count = selectedSet.size;
  const items = values
    .map((value) => `<label class="check-row"><input type="checkbox" data-check-kind="${kind}" value="${escapeHtml(value)}" ${selectedSet.has(value) ? "checked" : ""}>${escapeHtml(shortLabel(value))}</label>`)
    .join("");
  return `<details class="control-field dropdown-control" data-dropdown-key="${escapeHtml(dropdownKey)}"><summary><span>${escapeHtml(label)}</span><strong>${count}/${values.length}</strong></summary><div class="check-menu">${items}</div></details>`;
}

function trendMetricOptions() {
  return (state.metadata.metrics || []).map((metric) => ({ key: metric.key, label: metric.label || metric.key }));
}

function metricDefinitionMap(metricOptions) {
  const map = new Map();
  for (const metric of metricOptions || []) map.set(metric.key, metric);
  return map;
}

function selectedFamiliesForMetrics(selected, metricOptions, axisOrder = []) {
  const available = metricDefinitionMap(metricOptions);
  const families = [];
  for (const key of selected || []) {
    if (!available.has(key)) continue;
    const family = metricFamilyForKey(key);
    if (!families.includes(family)) families.push(family);
  }
  const ordered = (axisOrder || []).filter((family) => families.includes(family));
  for (const family of families) {
    if (!ordered.includes(family)) ordered.push(family);
  }
  return ordered.slice(0, 2);
}

function multiMetricPickerHtml(label, kind, metricOptions, selected, axisOrder) {
  return multiMetricPickerHtmlWithKey(label, kind, metricOptions, selected, axisOrder, kind);
}

function multiMetricPickerHtmlWithKey(label, kind, metricOptions, selected, axisOrder, dropdownKey) {
  const globalSelected = allSelectedViewMetrics(currentParams());
  const globalOptions = allMetricOptionsForCurrentView();
  const selectedSet = new Set(selected || []);
  const selectedFamilies = selectedFamiliesForMetrics(globalSelected, globalOptions.length ? globalOptions : metricOptions, axisOrder);
  const familyCount = selectedFamilies.length;
  const selectedCount = [...selectedSet].filter((key) => metricOptions.some((metric) => metric.key === key)).length;
  const grouped = new Map();
  for (const metric of metricOptions || []) {
    const family = metricFamilyForKey(metric.key);
    if (!grouped.has(family)) grouped.set(family, []);
    grouped.get(family).push(metric);
  }
  const familyBlocks = [...grouped.entries()].map(([family, metrics]) => {
    const side = selectedFamilies.indexOf(family) === 0 ? "L" : selectedFamilies.indexOf(family) === 1 ? "R" : "";
    const rows = metrics.map((metric) => {
      const checked = selectedSet.has(metric.key);
      const disabled = !checked && familyCount >= 2 && !selectedFamilies.includes(family);
      return `<label class="check-row ${disabled ? "disabled" : ""}">
        <input type="checkbox" data-multi-kind="${escapeHtml(kind)}" value="${escapeHtml(metric.key)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>
        <span>${escapeHtml(metric.label || metric.key)}</span>
      </label>`;
    }).join("");
    return `<div class="metric-family-block">
      <div class="metric-family-title"><span>${escapeHtml(metricFamilyLabel(family))}</span>${side ? `<b>${side} axis</b>` : ""}</div>
      ${rows}
    </div>`;
  }).join("");
  const note = familyCount >= 2 ? `<div class="control-note">Maximum 2 metric families. Clear one family to add another.</div>` : `<div class="control-note">Select any number of metrics from up to 2 families.</div>`;
  return `<details class="control-field dropdown-control metric-picker" data-dropdown-key="${escapeHtml(dropdownKey)}">
    <summary><span>${escapeHtml(label)}</span><strong>${selectedCount}/${metricOptions.length}</strong></summary>
    <div class="metric-axis-actions">
      <button type="button" class="ghost tiny" data-axis-action="swap" data-multi-kind="${escapeHtml(kind)}" ${selectedFamilies.length < 2 ? "disabled" : ""}>Swap axes</button>
      <button type="button" class="ghost tiny" data-axis-action="clear-right" data-multi-kind="${escapeHtml(kind)}" ${selectedFamilies.length < 2 ? "disabled" : ""}>Clear right</button>
    </div>
    <div class="check-menu">${familyBlocks}</div>
    ${note}
  </details>`;
}

function handleControlChange(event) {
  const target = event.target;
  const params = currentParams();
  if (target.matches("[data-multi-kind]")) {
    updateMultiMetricSelection(target.dataset.multiKind);
    void render();
    return;
  }
  if (target.matches("[data-param]")) {
    params[target.dataset.param] = target.value;
    if (target.dataset.param === "date") {
      params.start = target.value;
      params.end = target.value;
    }
    if (target.dataset.param === "start" || target.dataset.param === "end") {
      params.dateMode = "range";
      params.date = params.start || target.value;
      if (params.start && params.end && params.start > params.end) {
        if (target.dataset.param === "start") params.end = params.start;
        else params.start = params.end;
      }
    }
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
  const axisAction = event.target.closest("[data-axis-action]");
  if (axisAction) {
    applyAxisAction(axisAction.dataset.axisAction, axisAction.dataset.multiKind);
    void render();
    return;
  }
  const dateMode = event.target.closest("[data-date-mode]");
  if (dateMode) {
    const params = currentParams();
    params.dateMode = dateMode.dataset.dateMode;
    if (params.dateMode === "single") {
      params.date = params.date || params.start || dateStart();
      params.start = params.date;
      params.end = params.date;
    } else {
      params.start = params.start || params.date || dateStart();
      params.end = params.end || params.start || dateEnd();
      if (params.start > params.end) params.end = params.start;
      params.date = params.start;
    }
    void render();
    return;
  }
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

function metricParamForKind(kind) {
  return {
    "trend-metric": "metrics",
    "disagreement-metric": "metrics",
    "replay-source-metric": "sourceMetrics",
    "replay-overlay-metric": "overlayMetrics",
    "market-timeline-metric": "timelineMetrics",
  }[kind];
}

function metricOptionsForKind(kind) {
  return {
    "trend-metric": trendMetricOptions(),
    "disagreement-metric": DISAGREEMENT_METRICS,
    "replay-source-metric": REPLAY_SOURCE_METRICS,
    "replay-overlay-metric": REPLAY_OVERLAY_METRICS,
    "market-timeline-metric": MARKET_TIMELINE_METRICS,
  }[kind] || [];
}

function updateMultiMetricSelection(kind) {
  const params = currentParams();
  const param = metricParamForKind(kind);
  if (!param) return;
  const selected = [...document.querySelectorAll(`[data-multi-kind='${kind}']:checked`)].map((input) => input.value);
  params[param] = sanitizeMetricSelection(selected, metricOptionsForKind(kind), params.axisOrder || []);
  params.axisOrder = selectedFamiliesForMetrics(allSelectedViewMetrics(params), allMetricOptionsForCurrentView(), params.axisOrder || []);
}

function applyAxisAction(action, kind) {
  const params = currentParams();
  const allOptions = allMetricOptionsForCurrentView();
  const families = selectedFamiliesForMetrics(allSelectedViewMetrics(params), allOptions, params.axisOrder || []);
  if (action === "swap" && families.length >= 2) params.axisOrder = [families[1], families[0]];
  if (action === "clear-right" && families.length >= 2) {
    const rightFamily = families[1];
    for (const param of ["metrics", "sourceMetrics", "overlayMetrics", "timelineMetrics"]) {
      if (Array.isArray(params[param])) params[param] = params[param].filter((metric) => metricFamilyForKey(metric) !== rightFamily);
    }
    params.axisOrder = [families[0]];
  }
}

function sanitizeMetricSelection(selected, metricOptions, axisOrder = []) {
  const allowed = new Set((metricOptions || []).map((metric) => metric.key));
  const output = [];
  const families = selectedFamiliesForMetrics(selected, metricOptions, axisOrder);
  for (const key of selected || []) {
    if (allowed.has(key) && families.includes(metricFamilyForKey(key)) && !output.includes(key)) output.push(key);
  }
  return output;
}

function allSelectedViewMetrics(params) {
  return [
    ...(params.metrics || []),
    ...(params.sourceMetrics || []),
    ...(params.overlayMetrics || []),
    ...(params.timelineMetrics || []),
  ];
}

function allMetricOptionsForCurrentView() {
  if (state.mode === "trends") return trendMetricOptions();
  if (state.mode === "disagreement") return DISAGREEMENT_METRICS;
  if (state.mode === "replay") return [...REPLAY_SOURCE_METRICS, ...REPLAY_OVERLAY_METRICS];
  if (state.mode === "market-model") return MARKET_TIMELINE_METRICS;
  return [];
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

function replayMetricRows(timeline, metricKeys, metricOptions) {
  const rows = [];
  for (const point of timeline || []) {
    for (const metric of metricKeys || []) {
      const value = timelineMetricValue(point, metric);
      if (!isNumber(value)) continue;
      rows.push({
        x: point.snapshot_time_utc,
        value: Number(value),
        group: point.city || "event",
        metric,
        metricLabel: metricLabelForKey(metric, metricOptions),
        event_key: point.event_key,
      });
    }
  }
  return rows;
}

function marketTimelineMetricRows(rows, metricKeys, metricOptions, scope) {
  const groups = new Map();
  for (const row of rows || []) {
    const key = `${row.city}|${row.snapshot_time_utc}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  const output = [];
  for (const groupRows of groups.values()) {
    const candidate = marketTimelineCandidate(groupRows, scope);
    if (!candidate) continue;
    for (const metric of metricKeys || []) {
      const value = Number(candidate[metric]);
      if (!Number.isFinite(value)) continue;
      output.push({
        x: candidate.snapshot_time_utc,
        value,
        group: candidate.city,
        metric,
        metricLabel: metricLabelForKey(metric, metricOptions),
        event_key: candidate.event_key,
      });
    }
  }
  return output;
}

function marketTimelineCandidate(rows, scope) {
  const valid = (rows || []).filter((row) => row.snapshot_time_utc && row.city);
  if (!valid.length) return null;
  if (scope === "winners") return [...valid].sort((a, b) => Number(b.model_probability || 0) - Number(a.model_probability || 0))[0];
  if (scope === "model_edge") return [...valid].sort((a, b) => Number(b.model_minus_ask || -9) - Number(a.model_minus_ask || -9))[0];
  return [...valid].sort((a, b) => Number(b.model_probability || 0) - Number(a.model_probability || 0))[0];
}

function timelineMetricValue(point, metric) {
  if (isNumber(point?.[metric])) return point[metric];
  if (metric === "model_expected_high_f") {
    const modelKey = Object.keys(point || {}).find((key) => key.endsWith("_expected_high_f") && isNumber(point[key]));
    if (modelKey) return point[modelKey];
  }
  return null;
}

function eventOutcomeHtml(event, snapshotCount) {
  const items = [
    ["Final high", event.final_high_f],
    ["Winner", event.winner_label],
    ["Snapshots", snapshotCount],
  ];
  return `<div class="event-outcome">${items.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "n/a")}</strong></div>`).join("")}</div>`;
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

function chartCard({ id, title, chart, legendGroups = [], metaHtml = "" }) {
  const focused = state.focusChart === id;
  return `<section class="chart-card ${focused ? "focused" : ""}" id="${escapeHtml(id)}">
    <header class="card-head">
      <div class="card-title-block"><h3>${escapeHtml(title)}</h3>${metaHtml}</div>
      <div class="card-actions">${legendHtml(legendGroups)}<button type="button" class="ghost tiny" data-focus-chart="${escapeHtml(id)}">${focused ? "Compact" : "Focus"}</button></div>
    </header>
    <div class="chart-body">${chart || emptyState("No chart data available.")}</div>
  </section>`;
}

// Wraps a set of per-city (or other repeated) chart cards in a responsive grid so wide
// screens can show two charts per row. On narrower screens (or a single card) it collapses
// back to one column. When one card is focused, it spans the full grid width while the
// remaining cards keep flowing two-per-row beneath it.
function chartGrid(cards) {
  const list = (cards || []).filter(Boolean);
  if (!list.length) return "";
  if (list.length === 1) return list[0];
  return `<div class="chart-grid">${list.join("")}</div>`;
}

function scaleTypeForKey(key) {
  const text = String(key || "").toLowerCase();
  if (SOURCE_TEMPERATURE_KEYS.includes(text) || /(_high_f|high_by_|expected_high|projected_high|anchor|observed_high)/.test(text)) return "temperature";
  if (SOURCE_DELTA_KEYS.includes(text) || /(_minus_|error_by_snapshot|temperature_error|^error_f$|^bias$)/.test(text)) return "delta";
  if (SOURCE_SPREAD_KEYS.includes(text) || /(absolute_error|absolute_temperature_error|source_range|stddev|^mae$|^rmse$|miss_distance)/.test(text)) return "nonnegative";
  if (PROBABILITY_KEYS.includes(text) || /(probability|accuracy|frequency|within_|brier|rps)/.test(text)) return "probability";
  if (PRICE_KEYS.includes(text) || /(ask|bid|price|midpoint|dollars)/.test(text)) return "probability";
  if (/log_loss/.test(text)) return "score";
  if (/age_seconds/.test(text)) return "nonnegative";
  return "auto";
}

function metricFamilyForKey(key) {
  const type = scaleTypeForKey(key);
  if (type === "temperature") return "temperature";
  if (type === "delta") return "temperature_delta";
  if (type === "nonnegative") return "spread_error";
  if (type === "probability") return "probability_price";
  if (type === "score") return "score_count";
  return "score_count";
}

function metricFamilyLabel(family) {
  return {
    temperature: "Temperature (F)",
    temperature_delta: "Delta / signed error",
    spread_error: "Spread / absolute error",
    probability_price: "Probability / price",
    score_count: "Score / count",
  }[family] || titleCase(family);
}

function scaleTypeForFamily(family) {
  return {
    temperature: "temperature",
    temperature_delta: "delta",
    spread_error: "nonnegative",
    probability_price: "probability",
    score_count: "score",
  }[family] || "auto";
}

function valuesForKeys(rows, keys) {
  const output = [];
  for (const row of rows || []) {
    for (const key of keys) {
      const value = Number(row?.[key]);
      if (Number.isFinite(value)) output.push(value);
    }
  }
  return output;
}

function valuesForRows(rows, key) {
  return valuesForKeys(rows, [key]);
}

function domainForValues(values, type = "auto") {
  const numeric = (values || []).map(Number).filter(Number.isFinite);
  if (type === "probability") return { min: 0, max: 1, mode: "sequential", fixed: true };
  if (!numeric.length) return { min: 0, max: 1, mode: "sequential", fixed: false };
  if (type === "delta") {
    const extent = Math.max(1, ...numeric.map((value) => Math.abs(value)));
    const rounded = niceCeil(extent);
    return { min: -rounded, max: rounded, mode: "diverging", center: 0, fixed: false };
  }
  if (type === "nonnegative" || type === "score") {
    return { min: 0, max: niceCeil(Math.max(1, ...numeric)), mode: "sequential", fixed: false };
  }
  const min = Math.min(...numeric);
  const max = Math.max(...numeric);
  const pad = Math.max(1, (max - min || 1) * 0.08);
  return { min: Math.floor(min - pad), max: Math.ceil(max + pad), mode: "sequential", fixed: false };
}

function domainForRows(rows, key, type = scaleTypeForKey(key)) {
  return domainForValues(valuesForRows(rows, key), type);
}

function domainArray(domain) {
  return domain ? [Number(domain.min), Number(domain.max)] : null;
}

function niceCeil(value) {
  if (!Number.isFinite(value) || value <= 0) return 1;
  if (value <= 1) return 1;
  if (value <= 2) return 2;
  if (value <= 5) return 5;
  if (value <= 10) return 10;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / magnitude) * magnitude;
}

function sharedSourceDomain(rows, selectedKey) {
  const type = scaleTypeForKey(selectedKey);
  if (type === "temperature") return domainForValues(valuesForKeys(rows, SOURCE_TEMPERATURE_KEYS), "temperature");
  if (type === "delta") return domainForValues(valuesForKeys(rows, SOURCE_DELTA_KEYS), "delta");
  if (type === "nonnegative") return domainForValues(valuesForKeys(rows, SOURCE_SPREAD_KEYS), "nonnegative");
  return domainForRows(rows, selectedKey, type);
}

async function trendYDomain(metricKey) {
  const type = scaleTypeForKey(metricKey);
  const fixed = domainForValues([], type);
  if (fixed.fixed) return fixed;
  const metrics = (state.metadata.metrics || []).filter((metric) => scaleTypeForKey(metric.key) === type);
  const rows = [];
  for (const metric of metrics.length ? metrics : [{ key: metricKey }]) {
    rows.push(...await getSeries(metric.key));
  }
  return domainForValues(rows.map((row) => Number(row.value)).filter(Number.isFinite), type);
}

function featureAxisDomain(rows, key) {
  const type = scaleTypeForKey(key);
  if (type === "temperature") return domainForValues(valuesForKeys(rows, SOURCE_TEMPERATURE_KEYS), "temperature");
  if (type === "delta") return domainForValues(valuesForKeys(rows, SOURCE_DELTA_KEYS), "delta");
  if (type === "nonnegative") return domainForValues(valuesForKeys(rows, SOURCE_SPREAD_KEYS), "nonnegative");
  return domainForRows(rows, key, type);
}

function performanceRowDomains(rows, metrics) {
  const output = {};
  for (const metric of metrics || []) {
    output[metric] = domainForRows(rows.filter((row) => row.metric === metric), "value", scaleTypeForKey(metric));
  }
  return output;
}

function groupedMetricDomain(rows) {
  const metricTypes = unique(rows.map((row) => scaleTypeForKey(row.metric || "")));
  if (metricTypes.length === 1) return domainForRows(rows, "value", metricTypes[0]);
  return domainForRows(rows, "value", "auto");
}

function metricLabelForKey(key, metricOptions = []) {
  return metricOptions.find((metric) => metric.key === key)?.label || key;
}

function domainsForMetricRows(rows, metricKeys, axisOrder = []) {
  const domains = {};
  const families = selectedFamiliesForMetrics(metricKeys, metricKeys.map((key) => ({ key })), axisOrder);
  for (const family of families) {
    domains[family] = domainForValues(
      rows.filter((row) => metricFamilyForKey(row.metric) === family).map((row) => row.value),
      scaleTypeForFamily(family),
    );
  }
  return domains;
}

async function trendDomainsForMetrics(metricKeys, axisOrder = []) {
  const domains = {};
  const families = selectedFamiliesForMetrics(metricKeys, trendMetricOptions(), axisOrder);
  for (const family of families) {
    const metrics = (state.metadata.metrics || []).filter((metric) => metricFamilyForKey(metric.key) === family);
    const values = [];
    for (const metric of metrics.length ? metrics : metricKeys.map((key) => ({ key }))) {
      values.push(...(await getSeries(metric.key)).map((row) => Number(row.value)).filter(Number.isFinite));
    }
    domains[family] = domainForValues(values, scaleTypeForFamily(family));
  }
  return domains;
}

function multiAxisLegend(metricKeys, metricOptions = [], axisOrder = []) {
  const families = selectedFamiliesForMetrics(metricKeys, metricOptions.length ? metricOptions : metricKeys.map((key) => ({ key })), axisOrder);
  return metricKeys.map((metric, index) => {
    const family = metricFamilyForKey(metric);
    return {
      label: metricLabelForKey(metric, metricOptions),
      color: COLORS[index % COLORS.length],
      side: families.indexOf(family) === 1 ? "R" : "L",
    };
  });
}

function visibleSeriesCount(rows) {
  return unique((rows || []).map((row) => `${row.metric}|${row.group || ""}`)).length;
}

function densityNote(rows) {
  const count = visibleSeriesCount(rows);
  if (count <= 20) return "";
  return `<div class="density-note">Showing ${count} series. Reduce cities or metrics if the chart is hard to read.</div>`;
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
  }, { yDomain: config.yDomain });
  return svg;
}

function multiAxisLineChart(rows, options) {
  rows = (rows || []).filter((row) => row.x && isNumber(row.value) && row.metric);
  if (!rows.length) return emptyState("No points match the current metric selection.");
  const metricKeys = options.metrics?.length ? options.metrics : unique(rows.map((row) => row.metric));
  const families = selectedFamiliesForMetrics(metricKeys, metricKeys.map((key) => ({ key })), options.axisOrder || []);
  const leftFamily = families[0] || metricFamilyForKey(metricKeys[0]);
  const rightFamily = families[1] || null;
  const leftDomain = options.domains?.[leftFamily] || domainForValues(rows.filter((row) => metricFamilyForKey(row.metric) === leftFamily).map((row) => row.value), scaleTypeForFamily(leftFamily));
  const rightDomain = rightFamily
    ? options.domains?.[rightFamily] || domainForValues(rows.filter((row) => metricFamilyForKey(row.metric) === rightFamily).map((row) => row.value), scaleTypeForFamily(rightFamily))
    : null;
  const width = 980;
  const height = 430;
  const left = 76;
  const right = rightFamily ? 86 : 24;
  const top = 22;
  const bottom = 98;
  const plotRight = width - right;
  const plotBottom = height - bottom;
  const xValues = sortedLabels(unique(rows.map((row) => row.x)));
  const xScale = makeXScale(xValues, left, plotRight);
  const leftScale = makeYScale(leftDomain.min, leftDomain.max, top, plotBottom, true);
  const rightScale = rightDomain ? makeYScale(rightDomain.min, rightDomain.max, top, plotBottom, true) : null;
  const leftTicks = ticks(leftDomain.min, leftDomain.max, 5);
  const rightTicks = rightDomain ? ticks(rightDomain.min, rightDomain.max, 5) : [];
  const dense = xValues.length > 10;
  const xAxis = xValues.map((tick) => {
    const x = xScale(tick);
    const y = dense ? height - 60 : height - 54;
    const label = escapeHtml(shortLabel(tick));
    const text = dense
      ? `<text x="${x}" y="${y}" text-anchor="end" transform="rotate(-35 ${x} ${y})" class="tick-label">${label}</text>`
      : `<text x="${x}" y="${y}" text-anchor="middle" class="tick-label">${label}</text>`;
    return `<line x1="${x}" y1="${plotBottom}" x2="${x}" y2="${plotBottom + 7}" class="axis-tick"></line>${text}`;
  }).join("");
  const leftAxis = leftTicks.map((tick) => {
    const y = leftScale(tick);
    return `<line x1="${left}" y1="${y}" x2="${plotRight}" y2="${y}" class="grid-line"></line><line x1="${left - 6}" y1="${y}" x2="${left}" y2="${y}" class="axis-tick"></line><text x="${left - 10}" y="${y + 4}" text-anchor="end" class="tick-label">${escapeHtml(formatNumber(tick))}</text>`;
  }).join("");
  const rightAxis = rightFamily ? rightTicks.map((tick) => {
    const y = rightScale(tick);
    return `<line x1="${plotRight}" y1="${y}" x2="${plotRight + 6}" y2="${y}" class="axis-tick"></line><text x="${plotRight + 10}" y="${y + 4}" text-anchor="start" class="tick-label">${escapeHtml(formatNumber(tick))}</text>`;
  }).join("") : "";
  const series = [];
  metricKeys.forEach((metric, index) => {
    const family = metricFamilyForKey(metric);
    const axis = family === rightFamily ? "R" : "L";
    const yScale = axis === "R" && rightScale ? rightScale : leftScale;
    const metricRows = rows
      .filter((row) => row.metric === metric)
      .sort((a, b) => String(a.x).localeCompare(String(b.x)));
    const points = metricRows.map((row) => `${xScale(row.x)},${yScale(Number(row.value))}`).join(" ");
    if (!points) return;
    const color = COLORS[index % COLORS.length];
    const dash = axis === "R" ? ` stroke-dasharray="7 5"` : "";
    const label = metricRows[0]?.metricLabel || metric;
    series.push(`<polyline points="${points}" fill="none" stroke="${color}" stroke-width="2.5"${dash}></polyline>`);
    for (const row of metricRows) {
      series.push(`<circle cx="${xScale(row.x)}" cy="${yScale(Number(row.value))}" r="4" fill="${color}" data-event-key="${escapeHtml(row.event_key || "")}" data-hover-readout="${escapeHtml(`${axis} / ${label}: ${formatNumber(row.value)} at ${row.x}`)}"></circle>`);
    }
  });
  return `<svg class="chart-svg multi-axis-svg" viewBox="0 0 ${width} ${height}" role="img">
    ${leftAxis}
    <line x1="${left}" y1="${top}" x2="${left}" y2="${plotBottom}" class="axis-line"></line>
    <line x1="${left}" y1="${plotBottom}" x2="${plotRight}" y2="${plotBottom}" class="axis-line"></line>
    ${rightFamily ? `<line x1="${plotRight}" y1="${top}" x2="${plotRight}" y2="${plotBottom}" class="axis-line right-axis-line"></line>${rightAxis}` : ""}
    ${xAxis}
    ${series.join("")}
    <text x="${(left + plotRight) / 2}" y="${height - 14}" text-anchor="middle" class="axis-label">${escapeHtml(options.xLabel || "Time")}</text>
    <text x="20" y="${height / 2}" transform="rotate(-90 20 ${height / 2})" text-anchor="middle" class="axis-label">${escapeHtml(metricFamilyLabel(leftFamily))}</text>
    ${rightFamily ? `<text x="${width - 18}" y="${height / 2}" transform="rotate(90 ${width - 18} ${height / 2})" text-anchor="middle" class="axis-label">${escapeHtml(metricFamilyLabel(rightFamily))}</text>` : ""}
  </svg>`;
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
  }, { xDomain: options.xDomain, yDomain: options.yDomain });
}

function groupedBars(rows, labelKey, groupKey, valueKey, options) {
  rows = (rows || []).filter((row) => row[labelKey] !== undefined && isNumber(row[valueKey]));
  if (!rows.length) return emptyState("No bars match the current filters.");
  const labels = sortedLabels(unique(rows.map((row) => String(row[labelKey]))));
  const groups = sortedLabels(unique(rows.map((row) => String(row[groupKey] || "value"))));
  const values = rows.map((row) => Number(row[valueKey]));
  const explicitDomain = options.yDomain ? domainArray(options.yDomain) : null;
  const minValue = explicitDomain ? explicitDomain[0] : Math.min(0, ...values);
  const maxValue = explicitDomain ? explicitDomain[1] : Math.max(1, ...values);
  const width = 980;
  const height = 390;
  const left = 74;
  const top = 22;
  const bottom = 92;
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
    ${axisMarkup({
      width,
      height,
      left,
      top,
      bottom,
      xLabel: options.xLabel,
      yLabel: options.yLabel,
      xTicks: axisTicks(labels),
      yTicks: ticks(minValue, maxValue, 5),
      yScale,
    })}
    ${bars.join("")}
  </svg>`;
}

function heatmap(rows, xKey, yKey, valueKey, options) {
  rows = (rows || []).filter((row) => row[xKey] !== undefined && row[yKey] !== undefined && isNumber(row[valueKey]));
  if (!rows.length) return emptyState("No heatmap cells match the current filters.");
  const xs = sortedLabels(unique(rows.map((row) => String(row[xKey]))));
  const ys = sortedLabels(unique(rows.map((row) => String(row[yKey]))));
  const values = rows.map((row) => Number(row[valueKey]));
  const fallbackDomain = options.colorDomain || domainForValues(values, scaleTypeForKey(valueKey));
  const rowDomains = options.rowColorDomains || {};
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
    const domain = rowDomains[String(row[yKey])] || fallbackDomain;
    return `<g data-event-key="${escapeHtml(row.event_key || "")}" data-hover-readout="${escapeHtml(`${row[yKey]} / ${row[xKey]}: ${formatNumber(value)}`)}">
      <rect x="${x + 1}" y="${y + 1}" width="${Math.max(1, cellW - 2)}" height="${Math.max(1, cellH - 2)}" rx="4" fill="${heatColor(value, domain)}"></rect>
      ${showValues ? `<text x="${x + cellW / 2}" y="${y + cellH / 2 + 4}" text-anchor="middle" class="heat-value">${escapeHtml(formatNumber(value))}</text>` : ""}
    </g>`;
  });
  const legend = Object.keys(rowDomains).length
    ? heatLegend(width, height, fallbackDomain, "Per row")
    : heatLegend(width, height, fallbackDomain);
  return `<svg class="chart-svg heatmap-svg" viewBox="0 0 ${width} ${height}" role="img">
    <text x="${width / 2}" y="${height - 16}" text-anchor="middle" class="axis-label">${escapeHtml(options.xLabel)}</text>
    <text x="20" y="${height / 2}" transform="rotate(-90 20 ${height / 2})" text-anchor="middle" class="axis-label">${escapeHtml(options.yLabel)}</text>
    ${xs.map((x, i) => `<text x="${left + i * cellW + cellW / 2}" y="${height - 48}" text-anchor="middle" class="tick-label">${escapeHtml(shortLabel(x))}</text>`).join("")}
    ${ys.map((y, i) => `<text x="${left - 8}" y="${top + i * cellH + cellH / 2 + 4}" text-anchor="end" class="tick-label">${escapeHtml(y)}</text>`).join("")}
    ${cells.join("")}
    ${legend}
  </svg>`;
}

function stackedBars(rows, labelKey, valueKey, options) {
  rows = (rows || []).filter((row) => row[labelKey] !== undefined && isNumber(row[valueKey]));
  return groupedBars(rows.map((row) => ({ ...row, group: "probability" })), labelKey, "group", valueKey, options);
}

function svgFrame(rows, xKey, yKey, xLabel, yLabel, draw, options = {}) {
  const width = 980;
  const height = 410;
  const left = 76;
  const top = 20;
  const bottom = 98;
  const xValues = rows.map((row) => row[xKey]);
  const yValues = rows.map((row) => Number(row[yKey])).filter(Number.isFinite);
  const yDomain = options.yDomain ? domainArray(options.yDomain) : [Math.min(...yValues), Math.max(...yValues)];
  const xDomain = options.xDomain ? domainArray(options.xDomain) : null;
  const yMin = yDomain[0];
  const yMax = yDomain[1];
  const xScale = makeXScale(xValues, left, width - 18, xDomain);
  const yScale = makeYScale(yMin, yMax, top, height - bottom, Boolean(options.yDomain));
  const xTicks = options.xTicks || (xDomain ? ticks(xDomain[0], xDomain[1], 5) : axisTicks(sortedLabels(unique(xValues))));
  return `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img">
    ${axisMarkup({
      width,
      height,
      left,
      top,
      bottom,
      xLabel,
      yLabel,
      xTicks,
      yTicks: ticks(yMin, yMax, 5),
      xScale,
      yScale,
    })}
    ${draw({ xScale, yScale, width, height, left, top, bottom })}
  </svg>`;
}

function axisMarkup({
  width,
  height,
  left,
  top,
  bottom,
  xLabel,
  yLabel,
  xTicks,
  yTicks,
  xScale = null,
  yScale,
}) {
  const plotBottom = height - bottom;
  const yAxisTicks = yTicks.map((tick) => {
    const y = yScale(tick);
    return `<line x1="${left}" y1="${y}" x2="${width - 18}" y2="${y}" class="grid-line"></line><line x1="${left - 6}" y1="${y}" x2="${left}" y2="${y}" class="axis-tick"></line><text x="${left - 10}" y="${y + 4}" text-anchor="end" class="tick-label">${escapeHtml(formatNumber(tick))}</text>`;
  }).join("");
  const dense = xTicks.length > 10;
  const xAxisTicks = xTicks.map((tick, index) => {
    const x = xScale
      ? xScale(tick)
      : left + (index / Math.max(1, xTicks.length - 1)) * (width - left - 28);
    const y = dense ? height - 60 : height - 54;
    const label = escapeHtml(shortLabel(tick));
    const tickLine = `<line x1="${x}" y1="${plotBottom}" x2="${x}" y2="${plotBottom + 7}" class="axis-tick"></line>`;
    if (dense) {
      return `${tickLine}<text x="${x}" y="${y}" text-anchor="end" transform="rotate(-35 ${x} ${y})" class="tick-label">${label}</text>`;
    }
    return `${tickLine}<text x="${x}" y="${y}" text-anchor="middle" class="tick-label">${label}</text>`;
  }).join("");
  return `${yAxisTicks}<line x1="${left}" y1="${top}" x2="${left}" y2="${plotBottom}" class="axis-line"></line><line x1="${left}" y1="${plotBottom}" x2="${width - 18}" y2="${plotBottom}" class="axis-line"></line>${xAxisTicks}<text x="${width / 2}" y="${height - 14}" text-anchor="middle" class="axis-label">${escapeHtml(xLabel || "")}</text><text x="20" y="${height / 2}" transform="rotate(-90 20 ${height / 2})" text-anchor="middle" class="axis-label">${escapeHtml(yLabel || "")}</text>`;
}

function axisTicks(values) {
  return sortedLabels(values);
}

function makeXScale(values, minPx, maxPx, domain = null) {
  if (domain) {
    const [min, max] = domain;
    return (value) => minPx + ((Number(value) - min) / (max - min || 1)) * (maxPx - minPx);
  }
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

function makeYScale(min, max, top, bottom, fixed = false) {
  const pad = fixed ? 0 : (max - min || 1) * 0.08;
  const lo = min - pad;
  const hi = max + pad;
  return (value) => bottom - ((value - lo) / (hi - lo || 1)) * (bottom - top);
}

function ticks(min, max, count) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  return Array.from({ length: count }, (_, index) => min + ((max - min) * index) / (count - 1));
}

function heatColor(value, domain) {
  const min = Number(domain?.min ?? 0);
  const max = Number(domain?.max ?? 1);
  if (domain?.mode === "diverging") {
    const extent = Math.max(Math.abs(min), Math.abs(max), 1);
    const t = Math.max(0, Math.min(1, Math.abs(value) / extent));
    const hue = value < 0 ? 205 : 18;
    const sat = 18 + t * 44;
    const light = 93 - t * 42;
    return `hsl(${hue} ${sat}% ${light}%)`;
  }
  const t = Math.max(0, Math.min(1, (value - min) / (max - min || 1)));
  const hue = 150 - t * 120;
  const light = 88 - t * 42;
  return `hsl(${hue} 35% ${light}%)`;
}

function heatLegend(width, height, domain, label = "") {
  const min = Number(domain?.min ?? 0);
  const max = Number(domain?.max ?? 1);
  const x = width - 230;
  const y = height - 30;
  const steps = 8;
  const rects = Array.from({ length: steps }, (_, index) => {
    const value = min + ((max - min) * index) / Math.max(1, steps - 1);
    return `<rect x="${x + index * 18}" y="${y}" width="18" height="9" fill="${heatColor(value, domain)}"></rect>`;
  }).join("");
  const labelText = label ? `<text x="${x + 70}" y="${y - 6}" text-anchor="middle" class="tick-label">${escapeHtml(label)}</text>` : "";
  return `<g class="heat-legend">
    ${labelText || `<text x="${x}" y="${y - 6}" class="tick-label">Low</text>`}
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
  return `<div class="legend">${groups.map((group, index) => {
    const label = typeof group === "object" ? group.label : group;
    const color = typeof group === "object" && group.color ? group.color : COLORS[index % COLORS.length];
    const side = typeof group === "object" && group.side ? `<b class="axis-badge">${escapeHtml(group.side)}</b>` : "";
    return `<span><i style="background:${color}"></i>${side}${escapeHtml(shortLabel(label))}</span>`;
  }).join("")}</div>`;
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
  const grouped = groupBySeries(rows);
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

function groupBySeries(rows) {
  const groups = new Map();
  for (const row of rows || []) {
    const value = `${row.group || "value"}|${row.metric || "metric"}`;
    if (!groups.has(value)) groups.set(value, []);
    groups.get(value).push(row);
  }
  return groups;
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
 
