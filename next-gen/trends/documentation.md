# Trends

`trends/` is the local GUI workbench for exploring frozen Supabase exports, optional model reports, and optional quality reports. It is for analysis and model design, not production trading.

## What Belongs Here

- Local web server for the analysis workbench.
- Static HTML/CSS/JS UI assets.
- Source discovery for local export/report folders.
- Dataset-to-chart transformations.
- Derived analysis tables for feature trends, source disagreement, model errors, calibration, and market/model comparison.
- Tests for Trends data preparation and source discovery.

## What Does Not Belong Here

- Model training logic.
- Trading or PnL logic.
- Collector code that writes to Supabase.
- Long-lived report artifacts.
- Direct writes back to Supabase.

## Usage

Run from `next-gen/`:

```powershell
python -m trends.cli serve
python -m trends.cli serve --data-root data --report-root reports/model --quality-root reports/quality --strategy-root reports/strategy
```

Open the printed local URL and select:

- one frozen local Supabase export folder
- one exact model report folder, optional
- one quality report folder, optional
- one strategy report folder, optional

The GUI uses a source selector. It does not require pre-generating a giant `trends_data.json` file.
If `trends/ui/dist/index.html` exists, the Python server serves the React workbench. Otherwise it
falls back to the legacy static UI under `trends/static/`.

## React UI

The reworked workbench source lives under `trends/ui/`.

```powershell
cd trends/ui
npm install
npm run build
```

For frontend development, run the Python server on port 8765, then run:

```powershell
cd trends/ui
npm run dev
```

Vite proxies `/api` requests to the Python Trends server.

## Source Folder Layout

Keep local reports grouped by report type:

- `data/<export_run>` for frozen Supabase exports.
- `reports/model/<model_report_run>` for Raycaster and future model reports.
- `reports/quality/<quality_report_run>` for data-quality and daily-health reports.
- `reports/strategy/<strategy_report_run>` for paper strategy and policy reports.
- `reports/trends/<diagnostic_run>` for future Trends-only diagnostics if needed.

## GUI Modes

- `Overview`: dataset coverage, snapshot counts, final-high completion, settlement completion, and replayable events.
- `Data Quality`: missing city-hours, provider errors, pending settlements, pending final highs, and table counts.
- `Trend Explorer`: multi-metric line charts for weather, market, settlement, and model values over time.
- `Event Replay`: one city-day timeline across NWS, observations, HRRR, NBM, ensemble, model expected high, final high, and bracket probabilities.
- `Source Disagreement`: NWS/observed/HRRR/NBM/ensemble divergence, outlier tables, and disagreement trends.
- `Settlement Grid`: city-date matrix for final high, winner, model top bracket, and miss distance.
- `Checkpoint Performance`: model metrics by city and checkpoint using heatmaps and supporting views.
- `Feature vs Error`: scatter plots comparing source/disagreement features to model error.
- `Calibration`: bracket probability reliability buckets and related summaries.
- `Market vs Model`: model probabilities versus archived ask/midpoint and winner probability paths.
- `Strategy Lab`: paper strategy PnL, drawdown, threshold sweeps, trades, CLV, and policy calibration.
- `Raw Tables`: sortable/searchable inspection for raw and derived tables.

Model-specific modes should degrade gracefully when no model report is selected.

## Inputs

Frozen exports may include:

- `events`
- `market_snapshots`
- `weather_snapshots`
- `settlements`
- `final_temperature_labels`
- `provider_errors`
- `raw_payloads`

Model report folders may include:

- `predictions.csv`
- `bracket_distributions.csv`
- `errors.csv`
- `by_checkpoint.csv`
- `by_city.csv`
- `temperature_metrics.csv`
- `bracket_metrics.csv`
- `training_diagnostics.csv`

Quality report folders may include:

- `quality_report.json`
- `daily_health_report.json`
- `table_counts.csv`
- `missing_city_hours.csv`
- `provider_errors.csv`
- `city_coverage.csv`

Strategy report folders may include:

- `summary.json`
- `trades.csv`
- `daily_pnl.csv`
- `threshold_sweep.csv`
- `validation_threshold_sweep.csv`
- `train_gate_sweep.csv`
- `candidates.csv`
- `predictions.csv`
- `policy_calibration.csv`
- `ranking_diagnostics.csv`

## API Shape

The server exposes split endpoints instead of one large JSON payload:

- `/api/sources`
- `/api/load`
- `/api/catalog`
- `/api/overview`
- `/api/series/<metric>`
- `/api/analysis/<section>`
- `/api/event/<city|event_ticker>`
- `/api/table/<table_name>`

## UX Principles

- Choose exact source folders in the GUI.
- Keep model selection tied to the selected report folder.
- Use view-specific controls rather than global filters.
- Avoid nested scrollbars.
- Use consistent y-axis domains where changing cities or metrics would otherwise mislead comparison.
- Use two-axis multi-metric charts only when the selected metrics belong to at most two metric families.

## Environment

No environment variables are required when using local exports. Direct Supabase querying is intentionally not part of v1 because frozen exports make analysis reproducible.
