# Raycaster Benchmark Decision

Headline weighting: equal weight per city-day.

- Best point forecast: raycaster_city (MAE 1.0948, RMSE 1.2587).
- Best bracket probability: raycaster_city (log loss 1.0544).
- Best top-pick calibration: raycaster_city_residual_hybrid (calibration MAE 0.0454).

## Raycaster vs Source Blend

- MAE delta: -0.0272 F.
- RMSE delta: -0.0454 F.
- Log-loss delta: +0.0123.
- Top-one accuracy delta: -0.0226.

## Hybrid vs Source Blend

- MAE delta: -0.0272 F.
- RMSE delta: -0.0454 F.
- Log-loss delta: +0.0000.
- Top-one accuracy delta: +0.0000.

## Caveat

Treat this as a small-sample benchmark until more fully settled target days exist. Use bootstrap intervals before declaring small deltas meaningful.
