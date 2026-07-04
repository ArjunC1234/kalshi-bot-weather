# MaxTemp Engine

`maxtemp-engine/` contains weather-only models. The goal is to predict the final NWS-recorded daily maximum temperature, then convert that continuous forecast into Kalshi bracket probabilities.

## What Belongs Here

- Temperature feature builders from point-in-time snapshots.
- Final max-temperature point and quantile models.
- Temperature distribution construction.
- Conversion from temperature distributions to Kalshi bracket probabilities.
- Weather-only model evaluation helpers.
- Model templates and versioned model families.

## What Does Not Belong Here

- Trading decisions.
- Ask/bid filters.
- PnL calculations.
- Order placement.
- Market-price-trained strategy models.
- Collector code that writes to Supabase.

## Active Model Families

- `raycaster/`: current serious model family for final NWS high prediction.
- `thetemp/`: clonable template for future model families.

Each model family should have version folders such as `v1/`. New experimental versions should be created as new folders rather than rewriting prior assumptions.

## Model Boundary

Weather models may use:

- NWS forecasts and observations.
- Open-Meteo ensemble summaries.
- HRRR and NBM features.
- City/time/checkpoint metadata.
- Final NWS high labels for training.
- Kalshi brackets for probability conversion.

Weather models must not use:

- Kalshi ask, bid, midpoint, volume, or liquidity as predictive features.
- PnL labels.
- Trade/no-trade labels.

Those belong in `strategy-engine/`.
