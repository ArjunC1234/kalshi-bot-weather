# Libs

`libs/` contains reusable code shared by backtests, models, Trends, and production-adjacent tooling. It should stay generic and dependency-light.

## What Belongs Here

- Shared dataclasses and typed structures.
- City metadata and table/source constants.
- Supabase REST/Storage helpers.
- Read-only Kalshi, NWS, and Open-Meteo clients.
- Fixed-standard climate-day and checkpoint time helpers.
- Probability normalization, bracket integration, and scoring utilities.
- Metric calculations used by more than one subsystem.
- Input validation helpers.
- Safe `.env` loading for local tools.

## File Map

- `models.py`: `City`, `Bracket`, collector snapshots, settlements, final labels, predictions, distributions, datasets, and metric summaries.
- `constants.py`: city definitions, Kalshi series tickers, Supabase table names, provider URLs, and shared model/source names.
- `config.py`: `.env` loading and typed config objects.
- `supabase_client.py`: requests-based Supabase REST and Storage client.
- `kalshi_client.py`: read-only Kalshi API helpers.
- `nws_client.py`: read-only NWS API helpers.
- `openmeteo_client.py`: read-only Open-Meteo helpers.
- `time_utils.py`: UTC parsing, fixed-standard climate windows, checkpoint labels, and target-date keys.
- `probabilities.py`: probability floors, normalization, bracket lookup, top ticker, and distribution helpers.
- `metrics.py`: MAE, RMSE, bias, log loss, Brier, RPS, top-one accuracy, and within-one-bracket accuracy.
- `feature_utils.py`: shared feature extraction utilities for normalized snapshots.
- `validation.py`: schema checks, bracket contiguity, probability sums, and leakage checks.
- `io_utils.py` and `json_utils.py`: deterministic local file IO.
- `logging_utils.py`: common logging setup.
- `errors.py`: shared exception types.
- `ids.py`: deterministic ID helpers.

## What Does Not Belong Here

- Raycaster training logic.
- Model-specific feature engineering.
- Trading rules or PnL logic.
- Backtest orchestration commands.
- Production service loops.
- GUI rendering code.

## Import Convention

Run commands from `next-gen/` so imports resolve as:

```python
from libs.models import BacktestDataset
from libs.supabase_client import SupabaseClient
```
