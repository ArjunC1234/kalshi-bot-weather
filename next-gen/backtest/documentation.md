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
python -m backtest.cli export --start 2026-07-01 --end 2026-09-30 --output data/export_2026q3
python -m backtest.cli validate --data data/export_2026q3
python -m backtest.cli quality --data data/export_2026q3 --output reports/quality/data_quality
python -m backtest.cli pipeline --start 2026-07-01 --end 2026-09-30 --data-output data/export_2026q3 --report-output reports/model/export_2026q3
python -m backtest.cli evaluate --data data/export_2026q3 --model baseline --output reports/model/baseline
python -m backtest.cli report --run reports/model/baseline
```

## Quality Reports

`quality` writes:

- `quality_report.json`
- `missing_city_hours.csv`
- `table_counts.csv`
- `provider_errors.csv`

`pipeline` is the standard reproducible entrypoint: export Supabase facts, validate the frozen export, and write data-quality reports before any model work.

## Does Not Belong Here

- Live bot loops.
- New weather model internals.
- Production order placement.
