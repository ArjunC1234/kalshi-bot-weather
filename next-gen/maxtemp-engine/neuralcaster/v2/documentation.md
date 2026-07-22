# Neuralcaster v2

`neuralcaster_v2` is an offline PyTorch experiment for final-high and bracket-probability modeling.

It uses causal hourly sequences per city-day, predicts a Gaussian final-high distribution as a residual over a point-in-time baseline, and writes the same evaluation artifacts as Raycaster. It is intentionally not wired into production or replay.

Example:

```powershell
python maxtemp-engine/neuralcaster/v2/cli.py rolling-eval --data data/export_20260701_20260715_20260715T175455Z --output reports/model/neuralcaster_v2_gru_weather_only --train-days 5 --test-days 1 --mode weather
python maxtemp-engine/neuralcaster/v2/cli.py rolling-eval --data data/export_20260701_20260715_20260715T175455Z --output reports/model/neuralcaster_v2_gru_market_aware --train-days 5 --test-days 1 --mode market
```

