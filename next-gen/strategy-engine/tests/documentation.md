# Strategy Engine Tests

Tests in this folder should cover strategy-specific behavior.

## Should Cover

- EV calculations.
- Kalshi-style fee handling.
- Position sizing.
- Trade/no-trade behavior.
- PnL accounting.
- Risk limits.
- Strategy evaluation from frozen exports and model reports.

## Should Not Cover

- Raycaster temperature prediction.
- Collector API calls.
- Trends UI rendering.

Run from repository root:

```powershell
python -m unittest discover -s next-gen
```
