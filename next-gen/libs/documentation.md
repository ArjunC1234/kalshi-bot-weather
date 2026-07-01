# Libs

Shared reusable code belongs here.

## Belongs Here

- Supabase client helpers.
- Kalshi/NWS/Open-Meteo API clients.
- Time-window and climate-day helpers.
- Shared schemas and validation utilities.
- Generic metric/math helpers.

## File Map

- `models.py`: shared dataclasses for cities, snapshots, settlements, predictions, distributions, and results.
- `config.py`: `.env` loading and typed runtime config.
- `constants.py`: city metadata, source names, and table names.
- `supabase_client.py`: requests-based Supabase REST/Storage client.
- `kalshi_client.py`, `nws_client.py`, `openmeteo_client.py`: read-only provider clients.
- `time_utils.py`, `probabilities.py`, `metrics.py`, `validation.py`: reusable domain helpers.

## Does Not Belong Here

- Model training pipelines.
- Trading strategy rules.
- Backtest orchestration.
- Production bot loops.
