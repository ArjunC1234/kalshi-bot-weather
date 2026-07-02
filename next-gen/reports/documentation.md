# Reports

Local generated reports live here, grouped by report type. These files are for local analysis and
should not be uploaded back to Supabase.

## Layout

- `model/`: model evaluation and prediction reports, such as Raycaster runs.
- `quality/`: data quality reports from backtest/export validation.
- `trends/`: optional Trends-only diagnostics or saved UI artifacts.

## Usage

Run Trends from `next-gen/` and it will discover model and quality reports from the typed folders:

```powershell
python -m trends.cli serve
```

