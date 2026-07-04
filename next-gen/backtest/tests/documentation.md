# Backtest Tests

Tests in this folder cover the backtest subsystem only.

## Should Cover

- Local export loading.
- Supabase source shape compatibility.
- Dataset validation.
- Settlement validation.
- Final NWS high label loading.
- Metric calculations.
- Expanding-window and rolling-window leakage prevention.
- Daily health and quality report builders.
- Export pipeline behavior using mocks or tiny fixtures.

## Should Not Cover

- Raycaster model internals.
- Trends UI rendering.
- Production collector API calls.
- Strategy/PnL logic.

Run from repository root:

```powershell
python -m unittest discover -s next-gen
```
