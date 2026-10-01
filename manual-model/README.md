# Manual Model Workspace

This directory is intentionally separate from `next-gen`.

Use it for hand-written model experiments, scratch implementations, notes, and small validation scripts before wiring anything back into the main pipeline.

## Setup

From this directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run The First Manual Model

This uses the existing local Supabase export created by the workbench:

```powershell
python -m src.cli run `
  --data "..\next-gen\data\current_20260701_20260827_lightweight" `
  --train-start 2026-07-01 `
  --train-end 2026-08-19 `
  --test-start 2026-08-20 `
  --test-end 2026-08-27 `
  --out "runs\manual_v1_20260701_20260819_test_20260820_20260827"
```

The run writes:

- `summary.json`
- `predictions.csv`
- `trades.csv`
- `daily_pnl.csv`
- `daily_pnl.png`

## What This Model Does

`manual_v1` is deliberately understandable:

- Loads local exported Supabase tables from `.json.gz`.
- Creates one row per city/date/snapshot from weather snapshots.
- Uses a hand-coded weather source blend as the baseline forecast.
- Learns residual bias and uncertainty from the training window only.
- Converts the forecast distribution into Kalshi bracket probabilities.
- Runs a simple paper strategy using EV, spread, price, and budget filters.
