# Raycaster v1

Raycaster v1 predicts the final NWS daily high temperature in Fahrenheit from point-in-time weather snapshots, then converts that continuous forecast into Kalshi bracket probabilities.

## Model Shape

Raycaster v1 is staged:

1. Build weather-only features from frozen snapshots.
2. Use a deterministic source-blend fallback when there are too few training labels.
3. Train a `HistGradientBoostingRegressor` point model once enough final-high labels exist.
4. Train quantile regressors for uncertainty bands.
5. Convert the point/quantile forecast into bracket probabilities.
6. Score against final NWS highs and Kalshi settlement brackets.

## Inputs

Allowed inputs:

- City and checkpoint/time features.
- Hours elapsed and remaining in the climate day.
- NWS anchor and hourly-window values.
- Observed high so far and observation age.
- HRRR projected high, next-window maxes, and slopes.
- NBM projected high, next-window maxes, and slopes.
- Open-Meteo ensemble median/spread/count fields.
- Source disagreement features such as HRRR minus NWS.
- Final NWS high labels for training/evaluation.
- Kalshi bracket definitions for probability conversion.

Excluded inputs:

- Kalshi prices.
- Market midpoint.
- Ask/bid sizes.
- Volume/liquidity.
- PnL labels.
- Strategy labels.

## Commands

Run from `next-gen/`:

```powershell
python maxtemp-engine/raycaster/v1/cli.py train --data data/export_YYYY --output models/raycaster/v1/run_001
python maxtemp-engine/raycaster/v1/cli.py predict --data data/export_YYYY --model models/raycaster/v1/run_001 --output reports/model/raycaster_v1_predictions
python maxtemp-engine/raycaster/v1/cli.py evaluate --data data/export_YYYY --output reports/model/raycaster_v1_eval
python maxtemp-engine/raycaster/v1/cli.py rolling-eval --data data/export_YYYY --output reports/model/raycaster_v1_rolling --train-days 14 --test-days 1
python maxtemp-engine/raycaster/v1/cli.py report --run reports/model/raycaster_v1_eval
```

## Evaluation Modes

- `evaluate` with no saved model uses expanding-window evaluation by target date.
- `evaluate --model <artifact>` scores a fixed saved artifact. This is useful for smoke tests, but it is only leakage-free if the artifact was trained only on earlier data.
- `rolling-eval` trains on the prior N target dates and scores the next target date or block. Use this to approximate the future weekly retraining workflow.

## Output Files

Raycaster reports may contain:

- `summary.json`
- `predictions.csv`
- `bracket_distributions.csv`
- `training_diagnostics.csv`
- `temperature_metrics.csv`
- `bracket_metrics.csv`
- `by_city.csv`
- `by_checkpoint.csv`
- `errors.csv`
- `charts/`

## Required Environment

No live API credentials are required. Raycaster reads frozen local exports through the backtest loader.

## Current Data Reality

Raycaster can run with small datasets because it has fallback behavior, but real training needs enough final NWS high labels. Treat early runs as pipeline checks, not evidence of edge.
