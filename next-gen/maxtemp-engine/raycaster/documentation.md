# Raycaster

Raycaster is the active model family for predicting the final NWS-recorded daily maximum temperature. Each version lives in its own subfolder so experiments can be compared without overwriting prior assumptions.

## What Belongs Here

- Versioned Raycaster models such as `v1/`.
- Family-level model notes and promotion criteria.
- Shared Raycaster assumptions that apply across versions.
- Documentation explaining how versions differ.

## What Does Not Belong Here

- Trading rules, position sizing, or PnL logic. Put those in `strategy-engine/`.
- Production deployment scripts. Put those in `production/`.
- Raw collectors or Supabase sync code. Put those in `production/deployable/collector/` or generic clients in `libs/`.
- Market-price features. Raycaster is weather-only.

## Current Version

- `v1/`: staged final-high model using weather/time features, fallback source blend, histogram gradient boosting point model, quantile models, and bracket probability conversion.

## How To Use

Run version-specific commands from `next-gen/`:

```powershell
python maxtemp-engine/raycaster/v1/cli.py evaluate --data data/export_YYYY --output reports/model/raycaster_v1_eval
python maxtemp-engine/raycaster/v1/cli.py rolling-eval --data data/export_YYYY --output reports/model/raycaster_v1_rolling --train-days 14 --test-days 1
```

## Promotion Rule

A Raycaster version should not be promoted to production or strategy use just because one run looks good. It needs enough settled city-days, better out-of-sample temperature and bracket metrics, reasonable calibration, and no obvious city/checkpoint failure mode.
