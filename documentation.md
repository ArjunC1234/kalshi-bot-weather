# Repository Documentation

This repository now has two chapters:

- `legacy-experimentation/`: archived prototypes, old reports, old collectors, and earlier weather/strategy experiments.
- `next-gen/`: the active modular codebase for collection, exports, Raycaster, backtesting, Trends, and future strategy work.

New work should happen in `next-gen/` unless the task is explicitly to inspect or recover legacy behavior.

## Current Workflow

The deployed droplet runs collector v3 hourly. Collector v3 stores immutable facts in Supabase/Postgres and raw payloads in Supabase Storage. Local development exports those facts into frozen folders under `next-gen/data/`, then runs model evaluation, quality reports, and Trends locally.

The intended loop is:

1. Let collector v3 run on the server.
2. Export a date range from Supabase into `next-gen/data/`.
3. Run validation and quality reports.
4. Train/evaluate Raycaster from the frozen export.
5. Inspect the export and report in Trends.
6. Keep model outputs local; do not write reports back to Supabase.

## Folder Rules

- Do not add new model, strategy, backtest, collector, or production code at the repository root.
- Do not commit real `.env` files, private keys, Supabase keys, Kalshi keys, or server-only credentials.
- Put reusable provider clients, dataclasses, metrics, and time helpers in `next-gen/libs/`.
- Put weather model code in `next-gen/maxtemp-engine/`.
- Put offline replay, export, quality, and scoring code in `next-gen/backtest/`.
- Put production-deployable server code only in `next-gen/production/deployable/`.
- Put GUI/data-analysis workbench code in `next-gen/trends/`.

## Common Commands

Run these from the repository root:

```powershell
python -m compileall next-gen
python -m unittest discover -s next-gen
python -m ruff check next-gen
```

Most operational commands should be run from `next-gen/`:

```powershell
cd next-gen
python -m backtest.cli pipeline --start 2026-07-01 --end 2026-07-31
python maxtemp-engine/raycaster/v1/cli.py evaluate --data data/export_YYYY --output reports/model/raycaster_v1_eval
python -m trends.cli serve
```

## Documentation Map

- `legacy-experimentation/documentation.md`: what the archive contains and how to treat it.
- `next-gen/documentation.md`: active architecture and subsystem responsibilities.
- `next-gen/production/deployable/collector/documentation.md`: deployed collector v3 runbook.
- `next-gen/backtest/documentation.md`: export, validation, quality, monitor, and report commands.
- `next-gen/maxtemp-engine/raycaster/v1/documentation.md`: Raycaster v1 train/evaluate workflow.
- `next-gen/trends/documentation.md`: local analysis GUI usage.
