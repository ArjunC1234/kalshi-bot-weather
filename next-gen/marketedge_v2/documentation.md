# MarketEdge v2

`marketedge_v2` is an offline-only, leakage-safe research pipeline. It does not
place orders, alter the deployed strategy, or select a production model.

## Design

1. The weather layer uses a regularized Ridge model to estimate final NWS high.
   It uses NWS, HRRR, NBM, ensemble, observation, trend, lead-time, and strongly
   regularized city inputs. A conformal residual scale turns the point estimate
   into a Normal bracket distribution.
2. The probability layer starts from the normalized Kalshi midpoint distribution.
   It searches a walk-forward, penalized weather-residual blend weight in logit
   space. A zero weight means the data did not justify moving the market.
3. The execution layer evaluates both YES and NO at executable asks, applies the
   Kalshi-style quadratic taker-fee convention used by legacy experimentation,
   enforces quote-size/spread/EV gates, requires two consecutive confirmations,
   and holds one position per event to settlement.

Every training fold contains only target dates before its evaluated date. Snapshot
rows are weighted so a city-event with many updates does not dominate the score.

## Run

```powershell
cd next-gen
python -m marketedge_v2.cli `
  --data data\export_20260630_20260714_20260714T140338Z `
  --output reports\model\marketedge_v2_<timestamp>
```

## Outputs

- `summary.json`: headline MAE, RMSE, probability loss, accuracy, and PnL.
- `feature_catalog.csv`: retained field, layer, role, and mathematical use.
- `weather_predictions.csv`: walk-forward final-high predictions and uncertainty.
- `market_distributions.csv` and `marketedge_distributions.csv`: baseline and
  market-residual contract probabilities.
- `training_diagnostics.csv`: per-date training count, conformal scale, and
  selected market-residual weight.
- `trade_decisions.csv`, `trades.csv`, and grouped trade metric CSVs: every
  accepted/rejected execution decision with fee-adjusted PnL.
- `walk_forward_metrics.png`: daily MAE, RMSE, log loss, and cumulative PnL.

## Promotion Rule

Do not deploy from this package until a fully walk-forward sample shows better
market-relative probability loss, positive fee-adjusted PnL and CLV, acceptable
drawdown, and no material concentration by city, side, or target day.
