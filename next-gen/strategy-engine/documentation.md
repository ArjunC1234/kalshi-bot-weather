# Strategy Engine

`strategy-engine/` will decide whether model probabilities justify trades. It is intentionally separate from Raycaster and the collector.

## What Belongs Here

- EV filters.
- Fee modeling.
- Position sizing.
- PnL simulation.
- Trade/no-trade decision models.
- Strategy backtests using archived ask/bid/market facts and model reports.
- Risk controls and exposure limits.

## What Does Not Belong Here

- Weather-source ingestion.
- Final temperature prediction.
- Raw Supabase collection.
- Raycaster training.
- Trends UI code.
- Live production loops.

## Inputs

Strategy code may consume:

- Frozen Supabase exports.
- Model report folders from `reports/model/`.
- Kalshi market snapshot facts.
- Settlement facts.
- Final NWS high labels for analysis.

Strategy code should not write results to Supabase. Strategy outputs should go under local report folders until a production trading system is intentionally designed.

## Current Status

This subsystem is a placeholder for the next phase. Do not promote any strategy to live/demo production based only on small-sample early Raycaster runs.
