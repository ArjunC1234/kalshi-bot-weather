# Focused Regime Experiment

Historical development comparison, not a fresh holdout.

Fit through August 23, separate calibration August 24-27, evaluation August 28-September 10.
One common, receipt-verified afternoon snapshot per event; no threshold sweep.

| Model | Log loss | Brier | Trades | Wins | Hit rate | Net PnL |
|---|---:|---:|---:|---:|---:|---:|
| raw_market | 0.4764 | 0.2813 | 0 | 0 | n/a | $0.00 |
| mixed_market | 0.4638 | 0.2832 | 0 | 0 | n/a | $0.00 |
| mixed_weather | 0.4638 | 0.2832 | 0 | 0 | n/a | $0.00 |
| post_market | 0.4638 | 0.2830 | 0 | 0 | n/a | $0.00 |
| post_weather | 0.4638 | 0.2830 | 0 | 0 | n/a | $0.00 |

Lower log loss and Brier are better. Brier is the sum across six mutually exclusive brackets, averaged per event.

## Paired Comparisons

Differences are left minus right: negative favors left. Intervals resample whole days.

- mixed_weather minus mixed_market: +0.0000 log loss, descriptive 95% interval [+0.0000, +0.0000].
- post_weather minus post_market: +0.0000 log loss, descriptive 95% interval [+0.0000, +0.0000].
- post_weather minus mixed_weather: -0.0000 log loss, descriptive 95% interval [-0.0008, +0.0006].
- post_market minus mixed_market: -0.0000 log loss, descriptive 95% interval [-0.0008, +0.0006].
- mixed_weather minus raw_market: -0.0125 log loss, descriptive 95% interval [-0.0342, +0.0103].

## Decision

Provisional challenger chosen on calibration: mixed_weather.
Forward qualification: False.

```json
{
  "lower_log_loss_than_raw_market": true,
  "lower_log_loss_than_matched_market": false,
  "paired_interval_upper_below_zero": false,
  "at_least_twenty_trades": false,
  "observed_hit_rate_at_least_eighty_percent": false,
  "positive_net_profit": false,
  "positive_each_week": false
}
```

No live trading is enabled. Forward testing is not running; a saved specification is not an active observation process.

## Fourteen-Day Ledger

| Date | Model | Trades | Wins | Net PnL |
|---|---|---:|---:|---:|
| 2026-08-28 | raw_market | 0 | 0 | $0.00 |
| 2026-08-29 | raw_market | 0 | 0 | $0.00 |
| 2026-08-30 | raw_market | 0 | 0 | $0.00 |
| 2026-08-31 | raw_market | 0 | 0 | $0.00 |
| 2026-09-01 | raw_market | 0 | 0 | $0.00 |
| 2026-09-02 | raw_market | 0 | 0 | $0.00 |
| 2026-09-03 | raw_market | 0 | 0 | $0.00 |
| 2026-09-04 | raw_market | 0 | 0 | $0.00 |
| 2026-09-05 | raw_market | 0 | 0 | $0.00 |
| 2026-09-06 | raw_market | 0 | 0 | $0.00 |
| 2026-09-07 | raw_market | 0 | 0 | $0.00 |
| 2026-09-08 | raw_market | 0 | 0 | $0.00 |
| 2026-09-09 | raw_market | 0 | 0 | $0.00 |
| 2026-09-10 | raw_market | 0 | 0 | $0.00 |
| 2026-08-28 | mixed_weather | 0 | 0 | $0.00 |
| 2026-08-29 | mixed_weather | 0 | 0 | $0.00 |
| 2026-08-30 | mixed_weather | 0 | 0 | $0.00 |
| 2026-08-31 | mixed_weather | 0 | 0 | $0.00 |
| 2026-09-01 | mixed_weather | 0 | 0 | $0.00 |
| 2026-09-02 | mixed_weather | 0 | 0 | $0.00 |
| 2026-09-03 | mixed_weather | 0 | 0 | $0.00 |
| 2026-09-04 | mixed_weather | 0 | 0 | $0.00 |
| 2026-09-05 | mixed_weather | 0 | 0 | $0.00 |
| 2026-09-06 | mixed_weather | 0 | 0 | $0.00 |
| 2026-09-07 | mixed_weather | 0 | 0 | $0.00 |
| 2026-09-08 | mixed_weather | 0 | 0 | $0.00 |
| 2026-09-09 | mixed_weather | 0 | 0 | $0.00 |
| 2026-09-10 | mixed_weather | 0 | 0 | $0.00 |

## Evidence Limits

- All evaluation dates were previously researched; these are development comparisons, not independent confirmation.
- Regime composition, sample size and recency change together. This cannot isolate a settlement-rule causal effect.
- Only four calibration days are available; uncertainty and earlier search bias remain substantial.
- The common universe covers complete receipt-verified afternoon snapshots, not every collected event or hour.
- Gaussian weather uncertainty and city-bias shrinkage are assumptions, tested descriptively against settlements.
- Reliability bins contain dependent brackets, not independent bets; no binomial confidence is assigned to those rows.
- Fees assume general taker multiplier 1 and conservative cents rounding. Price/depth availability is not proof of execution.
- One-cent adverse execution is included. Zero/two-cent and next-quote results are in execution_sensitivity.csv.
- Twenty trades and an observed 80% hit rate would still not establish a true 80% minimum.

Method: [probability calibration](https://scikit-learn.org/stable/modules/calibration.html). Costs: [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf).
