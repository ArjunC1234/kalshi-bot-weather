# Kalshi Weather Workbench Backend

This package is the registry-driven backend foundation for the Kalshi Bot
Weather Workbench. Trends remains useful reference code, but the Workbench API is
separate and mounted under `/control/api/*` so the app can manage exports,
models, reports, and bot monitoring without depending on the Trends UI routes.

The intended developer workflow is:

1. Write model or strategy code.
2. Add a registry JSON file under `control/registry/builtin/` or an overlay registry root.
3. Make the code write a manifest-backed local artifact.
4. Validate the registry.
5. Run, inspect, and visualize artifacts through the control API.

## Commands

Run from `next-gen/`.

```powershell
python -m control.cli registry validate
python -m control.cli registry list
python -m control.cli artifacts scan
python -m control.cli serve --port 8775
python -m control.cli serve-workbench --port 8775
```

From `trends/ui/`, one command starts both the backend and the Vite frontend:

```powershell
npm run dev:workbench
```

Create a local export from a registered profile:

```powershell
python -m control.cli export `
  --profile lightweight_model_eval `
  --start 2026-07-01 `
  --end 2026-07-21 `
  --output data/export_20260701_20260721_control
```

## HTTP API

The control API is mounted under `/control/api/*` so existing Trends `/api/*`
routes can remain stable during migration.

- `GET /control/api/registry`
- `GET /control/api/dashboard`
- `GET /control/api/export-profiles`
- `GET /control/api/models`
- `GET /control/api/artifacts`
- `GET /control/api/exports`
- `GET /control/api/exports/<export_id>`
- `GET /control/api/reports`
- `GET /control/api/datasets`
- `GET /control/api/datasets/<dataset_id>`
- `GET /control/api/datasets/<dataset_id>/coverage`
- `GET /control/api/datasets/<dataset_id>/cities`
- `GET /control/api/datasets/<dataset_id>/cities/<city>`
- `GET /control/api/jobs`
- `GET /control/api/jobs/<job_id>`
- `GET /control/api/jobs/<job_id>/logs`
- `GET /control/api/bot/<resource>`
- `POST /control/api/jobs`
- `POST /control/api/jobs/<job_id>/cancel`
- `POST /control/api/exports/preview`
- `POST /control/api/exports/create`
- `POST /control/api/exports/validate`
- `POST /control/api/exports/compare`
- `POST /control/api/exports/clone`
- `POST /control/api/exports/reduce`
- `POST /control/api/exports/extend`
- `POST /control/api/exports/archive`
- `POST /control/api/compatibility/model-run`
- `POST /control/api/visualizations/query`

The bot endpoint is intentionally a deferred stub until deployed bot telemetry
is wired in.

## Dataset And Export Operations

The export manager supports the UI operations needed before direct Supabase or
scheduled export work is added:

- list and inspect local exports
- validate required table/city/date coverage
- compare export table counts and new city coverage
- clone exports
- reduce exports by table, city, and target date range
- extend exports by merging rows from a second export
- archive exports under `data/.archive`

Server routes resolve requested artifact IDs through the artifact scanner and
guard destination paths so UI-driven file operations stay within the repository.

## Visualization Query Contract

`POST /control/api/visualizations/query` reads a selected local artifact table
and returns chart-ready rows plus metadata. It supports:

- field selection for `x`, `y`, and `group`
- equality, range, and set filters
- aggregations: `count`, `sum`, `avg`, `min`, `max`
- snapshot-hour binning with `hour_blocks` where the block count divides 24
- deterministic sampling, stride decimation, pagination, and density metadata

This keeps large datasets readable in the UI without pushing proprietary chart
logic into every page.

## Model Registry Contract

A model registry entry declares:

- stable `id`, `kind`, `version`, and `label`
- registered entrypoints and command argv
- JSON-schema-like parameter metadata for UI forms
- required dataset/report inputs
- produced artifact type and visualization contract
- required and optional output files
- semantic output mappings

The job runner only executes registered argv lists. It does not accept arbitrary
shell snippets from the UI. UI-submitted parameters must be declared by the
registered entrypoint schema, aside from reserved control parameters such as
`dataset_path`, `model_report_path`, `output_path`, and `timeout_seconds`.
Completed registered jobs write a `run_manifest.json` with the registry ID,
entrypoint, command, selected dataset/report inputs, and output contract.

## Export Profile Contract

An export profile declares:

- source provider
- enabled tables
- protected columns
- required columns
- optional include/exclude columns

Protected and required columns are kept even when a profile excludes a broader
column group. This prevents UI-created slim exports from silently breaking model
loaders or joins.

## Artifact Contract

New artifacts should write `run_manifest.json` when possible. Legacy exports and
reports are still discovered by marker files, but manifests are preferred.

Recommended model report files:

- `run_manifest.json`
- `summary.json`
- `predictions.csv`
- `bracket_distributions.csv`
- `errors.csv`
- `by_checkpoint.csv`
- `by_city.csv`
- `temperature_metrics.csv`
- `bracket_metrics.csv`
- `training_diagnostics.csv`

Recommended strategy report files:

- `run_manifest.json`
- `summary.json`
- `trades.csv`
- `daily_pnl.csv`
- `threshold_sweep.csv`
- `policy_calibration.csv`
- `ranking_diagnostics.csv`
