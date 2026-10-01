# Backtest

`backtest/` exports immutable Supabase facts, loads frozen local datasets, validates them, writes quality reports, and scores stored model outputs. It must not call live weather APIs during evaluation.

## What Belongs Here

- Supabase export commands.
- Local export loaders.
- Dataset validation and no-leakage checks.
- Settlement and final-high loading from exported facts.
- Data quality and daily collector health reports.
- Replay/evaluation helpers that score model predictions.
- Report writing and chart generation for offline analysis.

## What Does Not Belong Here

- Live collector code.
- Live bot loops.
- Production order placement.
- Raycaster model internals.
- Strategy/PnL simulation logic beyond carrying model probabilities for later strategy use.

## Main CLI

Run from `next-gen/`:

```powershell
python -m backtest.cli export --start 2026-07-01 --end 2026-09-30
python -m backtest.cli validate --data data/export_20260701_20260930_YYYYMMDDTHHMMSSZ
python -m backtest.cli quality --data data/export_20260701_20260930_YYYYMMDDTHHMMSSZ
python -m backtest.cli daily-health --data data/export_20260701_20260930_YYYYMMDDTHHMMSSZ --date 2026-07-01
python -m backtest.cli import-labels --data data/export_20260701_20260930_YYYYMMDDTHHMMSSZ --labels weather_company_labels.csv --source-provider weather_company_daily
python -m backtest.cli settlement-sources --data data/export_20260701_20260930_YYYYMMDDTHHMMSSZ
python -m backtest.cli monitor --date 2026-07-01
python -m backtest.cli pipeline --start 2026-07-01 --end 2026-09-30
python -m backtest.cli evaluate --data data/export_20260701_20260930_YYYYMMDDTHHMMSSZ --model baseline
python -m backtest.cli report --run reports/model/baseline_export_20260701_20260930_YYYYMMDDTHHMMSSZ_YYYYMMDDTHHMMSSZ
```

`export`, `quality`, `pipeline`, and `evaluate` create timestamped folders by default so repeated runs over the same date range do not overwrite each other.

## Recommended Daily Use

Use `monitor` for a fast read-only live check:

```powershell
python -m backtest.cli monitor --date 2026-07-04
```

Use `pipeline` to create the frozen dataset and quality report used by models and Trends:

```powershell
python -m backtest.cli pipeline --start 2026-07-01 --end 2026-07-04
```

Use `daily-health` to inspect a single collection date from either a frozen export or Supabase:

```powershell
python -m backtest.cli daily-health --data data/export_20260701_20260704_YYYYMMDDTHHMMSSZ --date 2026-07-04
python -m backtest.cli daily-health --date 2026-07-04
```

The no-`--data` form queries Supabase directly. The `--data` form is preferred for reproducible reports.

Use `settlement-sources` whenever Kalshi changes source language or before trusting a report
for live trading:

```powershell
python -m backtest.cli settlement-sources --data data/current_20260701_20260827_lightweight
```

The report writes `summary.json`, `source_coverage.csv`, and
`label_source_comparison.csv`. It keeps market settlement source separate from
training label source. If Weather Company daily labels are not present, the
report stays coverage-only and emits a warning instead of pretending an NWS
backtest is source-compatible.

Use `import-labels` to add official alternate final-temperature labels without
overwriting the NWS labels already in the export:

```powershell
python -m backtest.cli import-labels `
  --data data/current_20260701_20260827_lightweight `
  --labels weather_company_labels.csv `
  --source-provider weather_company_daily
```

The CSV must include `city`, `event_ticker`, `target_date`, and `final_high_f`.
Optional fields are `station_id`, `product_id`, `issued_at_utc`,
`validation_status`, and `warnings`.

## Output Folders

Default outputs are timestamped:

- Exports: `data/export_<start>_<end>_<timestamp>/`
- Quality reports: `reports/quality/<report_name>_<timestamp>/`
- Model reports from generic evaluators: `reports/model/<model_name>_<dataset>_<timestamp>/`

## Export Contents

A complete export may include:

- `collector_runs`
- `raw_payloads`
- `events`
- `market_snapshots`
- `weather_snapshots`
- `settlements`
- `final_temperature_labels`
- `provider_errors`

Backtests should prefer `final_temperature_labels.final_high_f` for final NWS max-temperature labels when present. Kalshi settlement rows remain authoritative for bracket winners.

## Quality Reports

`quality` writes:

- `quality_report.json`
- `missing_city_hours.csv`
- `table_counts.csv`
- `provider_errors.csv`

`daily-health` writes:

- `daily_health_report.json`
- `city_coverage.csv`
- `missing_city_hours.csv`
- `provider_errors.csv`

## Implementation Map

- `cli.py`: command entrypoint.
- `export_supabase.py`: Supabase-to-local export.
- `data_sources.py`: local and Supabase-backed data sources.
- `load_dataset.py`: converts normalized rows into shared dataclasses.
- `validators.py`: dataset consistency checks.
- `quality.py`: dataset-level quality reports.
- `health.py`: date-specific collector health reports.
- `label_import.py`: append alternate final-temperature label sources to exports.
- `settlement_source_report.py`: source coverage and NWS-vs-Weather Company label diffs.
- `pipeline.py`: export plus validation plus quality report.
- `settlements.py`: settlement validation helpers.
- `splitters.py`: leakage-safe date splitters.
- `evaluate.py`: generic stored-probability evaluator.
- `reports.py` and `plots.py`: report output helpers.
