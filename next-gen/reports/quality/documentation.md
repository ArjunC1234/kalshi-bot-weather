# Quality Reports

Data quality and collector-health reports belong here.

## Report Types

- `pipeline_*`: export, validation, and quality reports produced by `python -m backtest.cli pipeline`.
- `data_quality_*`: dataset-level quality reports produced by `python -m backtest.cli quality`.
- `daily_health_*`: date-specific collection health reports produced by `python -m backtest.cli daily-health`.

## Common Files

Dataset quality reports may contain:

- `quality_report.json`
- `table_counts.csv`
- `missing_city_hours.csv`
- `provider_errors.csv`

Daily health reports may contain:

- `daily_health_report.json`
- `city_coverage.csv`
- `missing_city_hours.csv`
- `provider_errors.csv`

## What To Check

- Six cities present.
- Expected city-hours collected.
- Provider errors are explainable.
- Pending settlements are reasonable for still-open or recently closed events.
- Pending final highs clear once NWS final labels become available.
