# Raycaster v1

Raycaster v1 predicts the final NWS daily high temperature in Fahrenheit from
point-in-time weather snapshots, then converts the continuous forecast into
Kalshi bracket probabilities.

## What Belongs Here

- Feature extraction for point-in-time weather snapshots.
- Offline training, prediction, evaluation, and reporting code.
- Raycaster v1 model artifacts and schema definitions.
- Raycaster v1 unit tests under `tests/`.

## What Does Not Belong Here

- Kalshi price-based strategy decisions.
- Live order placement.
- Collector code that calls public APIs.
- Market-price features inside the weather model.

## Commands

Run from `next-gen/`:

```powershell
python maxtemp-engine/raycaster/v1/cli.py train --data data/export --output models/raycaster/v1/run_001
python maxtemp-engine/raycaster/v1/cli.py predict --data data/export --model models/raycaster/v1/run_001 --output reports/model/raycaster_v1_predictions
python maxtemp-engine/raycaster/v1/cli.py evaluate --data data/export --output reports/model/raycaster_v1_eval
python maxtemp-engine/raycaster/v1/cli.py report --run reports/model/raycaster_v1_eval
```

`evaluate` uses expanding-window backtesting by default. Passing `--model`
evaluates a fixed saved artifact against the dataset, which is useful for smoke
tests but should not be treated as a leakage-free backtest unless the artifact
was trained only on prior data.

## Required Environment

No live API credentials are required. The model reads frozen local exports using
the shared backtest loader.
