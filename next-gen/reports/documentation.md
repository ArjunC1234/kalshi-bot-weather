# Reports

`reports/` contains local generated reports. These are analysis artifacts, not database facts, and should not be uploaded back to Supabase.

## Layout

- `model/`: model evaluation and prediction reports, such as Raycaster runs.
- `quality/`: data quality, pipeline, and daily-health reports.
- `trends/`: optional Trends-only diagnostics or saved UI artifacts.

## Naming

Use timestamped report names. This avoids overwriting previous runs and lets Trends select exact report folders.

Examples:

- `reports/model/raycaster_v1_export_20260701_20260731_YYYYMMDDTHHMMSSZ/`
- `reports/quality/pipeline_20260701_20260731_YYYYMMDDTHHMMSSZ/`
- `reports/quality/daily_health_2026-07-04_export_YYYY_YYYYMMDDTHHMMSSZ/`

## Usage

Run Trends from `next-gen/` and it will discover model and quality reports from the typed folders:

```powershell
python -m trends.cli serve
```

Select one frozen export and one exact report folder in the GUI.

## Rule

Reports are disposable and reproducible. Supabase stores immutable facts; local reports store experiments, summaries, charts, and model outputs.
