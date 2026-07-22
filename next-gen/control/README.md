# Control Plane

The control plane is the registry-driven foundation for turning Trends into a
Kalshi weather bot control center.

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
- `GET /control/api/artifacts`
- `GET /control/api/jobs`
- `GET /control/api/jobs/<job_id>`
- `GET /control/api/jobs/<job_id>/logs`
- `POST /control/api/jobs`
- `POST /control/api/exports/preview`
- `POST /control/api/exports/create`
- `POST /control/api/compatibility/model-run`

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
shell snippets from the UI.

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

