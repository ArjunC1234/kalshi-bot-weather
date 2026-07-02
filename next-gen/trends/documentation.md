# Trends

Local GUI workbench for exploring values collected in Supabase exports and optional local model
reports. The GUI discovers local exports and reports, then loads the selected sources on demand.

## What Belongs Here

- Interactive visualization tooling for frozen exports.
- Dataset-to-chart transformations and derived analysis tables.
- Local model report ingestion for predictions, bracket distributions, errors, and metrics.
- Local web server code and static UI assets.
- Tests for trend data preparation.

## What Does Not Belong Here

- Model training logic.
- Trading or PnL logic.
- Collector code that writes to Supabase.
- Long-lived report artifacts.

## Usage

Run from `next-gen/`:

```powershell
python -m trends.cli serve
python -m trends.cli serve --data-root data --report-root reports/model --quality-root reports/quality
```

Then open the printed local URL in a browser and select:

- one local Supabase export folder
- one exact model report folder, optional
- one quality report folder, optional

## Report Folder Layout

Keep local reports grouped by report type:

- `reports/model/<model_report_run>` for Raycaster and future model evaluation reports.
- `reports/quality/<quality_report_run>` for data-quality reports.
- `reports/trends/<diagnostic_run>` for future Trends-only diagnostics if needed.

## GUI Modes

- `Overview`: coverage, settlement/final-high completion, report availability, and replayable events.
- `Trend Explorer`: multi-city line charts for weather, market, settlement, and model metrics.
- `Event Replay`: one city-day timeline across weather sources, market top bracket, model expected high, final high, and bracket probabilities.
- `Checkpoint Performance`: heatmaps and grouped bars for Raycaster/backtest metrics.
- `Feature vs Error`: source/disagreement feature scatter plots against model error.
- `Source Disagreement`: NWS/observed/HRRR/NBM/ensemble divergence and outlier tables.
- `Market vs Model`: model probability versus archived ask/midpoint and winner probability paths.
- `Calibration`: bracket probability reliability buckets.
- `Settlement Grid`: city-date table for final high, winner, model top bracket, and misses.
- `Data Quality`: missing city-hours, provider errors, pending labels, and table counts.

Model-specific modes degrade gracefully when no report is selected.

## Inputs

The GUI reads frozen local exports containing tables like:

- `events`
- `market_snapshots`
- `weather_snapshots`
- `settlements`
- `final_temperature_labels`

Optional report folders may include:

- `predictions.csv`
- `bracket_distributions.csv`
- `errors.csv`
- `by_checkpoint.csv`
- `by_city.csv`
- `temperature_metrics.csv`
- `bracket_metrics.csv`
- `training_diagnostics.csv`

The server exposes split endpoints instead of one large JSON payload:

- `/api/sources`
- `/api/load`
- `/api/catalog`
- `/api/overview`
- `/api/series/<metric>`
- `/api/analysis/<section>`
- `/api/event/<city|event_ticker>`
- `/api/table/<table_name>`

## Environment

No environment variables are required when using local exports. Direct Supabase querying can be
added later, but v1 intentionally uses frozen exports for reproducibility.
