# Raycaster Benchmark Decision

Headline weighting: equal weight per city-day.

- Best point forecast: raycaster (MAE 1.1279, RMSE 1.2951).
- Best bracket probability: raycaster_source_blend_hybrid (log loss 1.0272).
- Best top-pick calibration: raycaster_source_blend_hybrid (calibration MAE 0.0568).

## Raycaster vs Source Blend

- MAE delta: -0.0352 F.
- RMSE delta: -0.0557 F.
- Log-loss delta: +0.0282.
- Top-one accuracy delta: -0.0160.

## Hybrid vs Source Blend

- MAE delta: -0.0352 F.
- RMSE delta: -0.0557 F.
- Log-loss delta: +0.0000.
- Top-one accuracy delta: +0.0000.

## Caveat

Treat this as a small-sample benchmark until more fully settled target days exist. Use bootstrap intervals before declaring small deltas meaningful.
