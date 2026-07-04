# Backtest

This subsystem replays point-in-time data and scores models without calling live forecast APIs.

## Belongs Here

- Supabase exports and local replay loaders.
- Settlement ingestion and validation.
- Expanding-window evaluation.
- Weather and strategy metric reports.
- Charts and analysis outputs.

## CLI

Run from `next-gen/`:

```powershell
python -m backtest.cli export --start 2026-07-01 --end 2026-09-30
python -m backtest.cli validate --data data/export_20260701_20260930_YYYYMMDDTHHMMSSZ
python -m backtest.cli quality --data data/export_20260701_20260930_YYYYMMDDTHHMMSSZ
python -m backtest.cli daily-health --data data/export_20260701_20260930_YYYYMMDDTHHMMSSZ --date 2026-07-01
python -m backtest.cli monitor --date 2026-07-01
python -m backtest.cli pipeline --start 2026-07-01 --end 2026-09-30
python -m backtest.cli evaluate --data data/export_20260701_20260930_YYYYMMDDTHHMMSSZ --model baseline
python -m backtest.cli report --run reports/model/baseline_export_20260701_20260930_YYYYMMDDTHHMMSSZ_YYYYMMDDTHHMMSSZ
```

`export`, `quality`, `pipeline`, and `evaluate` create timestamped folders by default so
multiple runs over the same date range do not overwrite each other. Pass `--output`,
`--data-output`, or `--report-output` only when an exact path is required.

## Quality Reports

`quality` writes:

- `quality_report.json`
- `missing_city_hours.csv`
- `table_counts.csv`
- `provider_errors.csv`

`pipeline` is the standard reproducible entrypoint: export Supabase facts, validate the frozen export, and write data-quality reports before any model work.

`daily-health` writes a date-specific health report for a frozen export or,
without `--data`, directly from Supabase. `monitor` is read-only and prints the
current Supabase collector view plus the same daily health summary.

## Does Not Belong Here

- Live bot loops.
- New weather model internals.
- Production order placement.
