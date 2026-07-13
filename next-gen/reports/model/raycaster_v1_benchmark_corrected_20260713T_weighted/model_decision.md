# Raycaster Benchmark Decision

Headline weighting: equal weight per city-day.

- Best point forecast: raycaster (MAE 1.1426, RMSE 1.3149).
- Best bracket probability: source_blend (log loss 1.0639).
- Best top-pick calibration: source_blend (calibration MAE 0.0454).

## Raycaster vs Source Blend

- MAE delta: -0.0309 F.
- RMSE delta: -0.0501 F.
- Log-loss delta: +0.0115.
- Top-one accuracy delta: -0.0237.

## Caveat

Treat this as a small-sample benchmark until more fully settled target days exist. Use bootstrap intervals before declaring small deltas meaningful.
