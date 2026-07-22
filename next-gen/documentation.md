# Next-Gen Codebase

`next-gen/` is the active codebase. It is organized around immutable fact collection, frozen local exports, offline weather-model evaluation, analysis tooling, and future strategy simulation.

## Subsystems

- `libs/`: shared clients, schemas, constants, time helpers, metrics, probability utilities, and validation helpers.
- `backtest/`: Supabase export, local replay, validation, daily health, data quality, settlement loading, and model report writing.
- `maxtemp-engine/`: weather-only models that predict final NWS max temperature and convert that forecast into Kalshi bracket probabilities.
- `strategy-engine/`: future EV, fees, sizing, PnL, and trade/no-trade logic. This is intentionally separate from weather modeling.
- `production/`: deployment scripts and the server-ready `deployable/` tree.
- `production/deployable/collector/`: collector v3, the currently deployed hourly immutable fact collector.
- `trends/`: local GUI workbench for exports, quality reports, model reports, and feature/model analysis.
- `control/`: registry-driven control plane for discoverable exports, model/strategy runs, jobs, artifacts, schemas, and future bot monitoring.
- `reports/`: local generated report folders grouped by report type.
- `data/`: local frozen Supabase exports.
- `models/`: local model artifacts.
- `tests/`: cross-subsystem integration tests only.

## Current Daily Operating Loop

1. Collector v3 runs hourly on the droplet and writes immutable facts to Supabase.
2. Export the desired range locally:

```powershell
python -m backtest.cli export --start 2026-07-01 --end 2026-07-31
```

3. Validate and write quality reports:

```powershell
python -m backtest.cli pipeline --start 2026-07-01 --end 2026-07-31
python -m backtest.cli daily-health --data data/export_YYYY --date 2026-07-31
```

4. Evaluate Raycaster:

```powershell
python maxtemp-engine/raycaster/v1/cli.py evaluate --data data/export_YYYY --output reports/model/raycaster_v1_eval
```

5. Inspect the data and report:

```powershell
python -m trends.cli serve
```

## Rules

- Run commands from `next-gen/` unless a command explicitly says otherwise.
- Engine-specific tests belong in the engine's own `tests/` folder.
- Cross-engine tests belong in `next-gen/tests/`.
- Production deploy scripts must upload only `next-gen/production/deployable/`.
- Market prices must not be used inside weather-only max-temperature models.
- Model reports, Trends state, strategy simulations, and PnL outputs stay local and must not be written back to Supabase.
- Supabase is a compiled fact database: raw payload refs, immutable market/weather facts, provider errors, settlements, and final NWS high labels.

## Verification

Run from repository root:

```powershell
python -m compileall next-gen
python -m unittest discover -s next-gen
python -m ruff check next-gen
```
