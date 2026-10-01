# Smaller-Edge Ablation

Frozen model and identical 84 city-days, August 28-September 10.
Historical development evidence. No threshold was selected by evaluation profit.

| Model | Net edge | Trades | Wins | Hit rate | Net PnL | First week | Second week |
|---|---:|---:|---:|---:|---:|---:|---:|
| raw_market | $0.00 | 0 | 0 | n/a | $0.00 | $0.00 | $0.00 |
| raw_market | $0.01 | 0 | 0 | n/a | $0.00 | $0.00 | $0.00 |
| raw_market | $0.02 | 0 | 0 | n/a | $0.00 | $0.00 | $0.00 |
| raw_market | $0.05 | 0 | 0 | n/a | $0.00 | $0.00 | $0.00 |
| mixed_weather | $0.00 | 3 | 1 | 33.3% | $-3.02 | $-2.58 | $-0.44 |
| mixed_weather | $0.01 | 3 | 1 | 33.3% | $-3.02 | $-2.58 | $-0.44 |
| mixed_weather | $0.02 | 0 | 0 | n/a | $0.00 | $0.00 | $0.00 |
| mixed_weather | $0.05 | 0 | 0 | n/a | $0.00 | $0.00 | $0.00 |

## Primary One-Cent Rule

```json
{
  "model": "mixed_weather",
  "edge": 0.01,
  "slippage": 0.01,
  "execution": "snapshot",
  "trades": 3,
  "settled_trades": 3,
  "wins": 1,
  "unsettled_trades": 0,
  "hit_rate": 0.3333333333333333,
  "net_pnl": -3.0199999999999996,
  "fees": 0.07,
  "gross_pnl": -2.9499999999999997,
  "cash_debit": 6.02,
  "roi": -0.5016611295681063,
  "contracts": 7.0,
  "breakeven_contract_hit_rate": 0.86,
  "contract_hit_rate": 0.42857142857142855,
  "max_daily_drawdown": -3.4399999999999995,
  "mean_probability": 0.8741849422007878,
  "day_bootstrap_pnl_95_interval": [
    -8.62,
    0.8400000000000001
  ],
  "day_bootstrap_hit_95_interval": [
    0.0,
    1.0
  ],
  "first_week_pnl": -2.5799999999999996,
  "second_week_pnl": -0.43999999999999995,
  "worst_leave_one_day_out_pnl": -3.4399999999999995,
  "iid_one_sided_95_hit_lower_bound": 0.016952427508441496
}
```

Provisional qualification: False. No live trading or forward logging is activated.

The exact hit lower bound assumes independent trades. Day-bootstrap intervals account for within-day grouping,
but neither corrects prior research selection or guarantees future profitability.

## Fourteen Days

| Date | Trades | Wins | Net PnL |
|---|---:|---:|---:|
| 2026-08-28 | 0 | 0 | $0.00 |
| 2026-08-29 | 0 | 0 | $0.00 |
| 2026-08-30 | 0 | 0 | $0.00 |
| 2026-08-31 | 1 | 0 | $-2.58 |
| 2026-09-01 | 0 | 0 | $0.00 |
| 2026-09-02 | 0 | 0 | $0.00 |
| 2026-09-03 | 0 | 0 | $0.00 |
| 2026-09-04 | 0 | 0 | $0.00 |
| 2026-09-05 | 0 | 0 | $0.00 |
| 2026-09-06 | 0 | 0 | $0.00 |
| 2026-09-07 | 1 | 0 | $-0.86 |
| 2026-09-08 | 1 | 1 | $0.42 |
| 2026-09-09 | 0 | 0 | $0.00 |
| 2026-09-10 | 0 | 0 | $0.00 |

Fees use the general taker multiplier 1 with conservative cent rounding per order.
Main results include one-cent adverse execution; delayed fills are limit-price proxies, not proven executions.
All costs, zero/two-cent sensitivities and delayed comparisons are in comparisons.csv.
Fee reference: https://kalshi.com/docs/kalshi-fee-schedule.pdf
