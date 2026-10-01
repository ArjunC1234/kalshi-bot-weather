# Profitability Research

Status: abstain_validation_failed

Selected research candidate: neural

Selection was frozen using August 4-17 only. August 18-31 had been examined in prior work and is retrospective.
September performance was scored after saving the selection. Historical fills are simulated, not actual trades.

| Window | Trades | Wins | Hit rate | Net PnL | Fees | ROI |
|---|---:|---:|---:|---:|---:|---:|
| validation | 25 | 18 | 72.0% | $0.13 | $1.14 | 0.2% |
| test | 23 | 21 | 91.3% | $8.88 | $0.91 | 16.4% |
| fresh | 22 | 10 | 45.5% | $-20.03 | $1.09 | -37.8% |

## Frozen Policy

```json
{
  "model": "neural",
  "policy": {
    "side": "no",
    "min_price": 0.5,
    "max_price": 0.85,
    "min_hours": 10,
    "min_net_edge": 0.02,
    "bounded_only": false,
    "min_probability": 0.85,
    "max_spread": 0.05
  },
  "validation_qualified": false
}
```

## Fourteen-Day Breakdown

| Date | Trades | Wins | Net PnL | Cumulative |
|---|---:|---:|---:|---:|
| 2026-08-18 | 1 | 1 | $0.42 | $0.42 |
| 2026-08-19 | 2 | 2 | $0.84 | $1.26 |
| 2026-08-20 | 0 | 0 | $0.00 | $1.26 |
| 2026-08-21 | 2 | 2 | $0.98 | $2.24 |
| 2026-08-22 | 1 | 1 | $1.45 | $3.69 |
| 2026-08-23 | 1 | 1 | $0.73 | $4.42 |
| 2026-08-24 | 2 | 2 | $1.66 | $6.08 |
| 2026-08-25 | 3 | 3 | $1.51 | $7.59 |
| 2026-08-26 | 1 | 1 | $0.45 | $8.04 |
| 2026-08-27 | 2 | 1 | $-2.48 | $5.56 |
| 2026-08-28 | 2 | 1 | $-0.33 | $5.23 |
| 2026-08-29 | 2 | 2 | $0.98 | $6.21 |
| 2026-08-30 | 0 | 0 | $0.00 | $6.21 |
| 2026-08-31 | 4 | 4 | $2.67 | $8.88 |

## Limitations

- General taker multiplier 1 is assumed; fees are conservatively rounded up to cents per order, without rebates.
- One-cent adverse execution is included in selection and reported main results. Price and delayed-quote sensitivities are separate files.
- One position per event, $3 maximum all-in cost per order and $40 daily budget. Visible depth caps size.
- Reconstructed receipt timestamps gate decision time. Filling a quote seen earlier in the collection cycle still requires an execution assumption; the next-quote stress test is only a proxy.
- Bootstrap intervals group trades by day but cannot eliminate search bias or demonstrate a future minimum hit rate.
- The research candidate is not enabled for live trading. An abstention status means the validation criteria failed.

Fee references: https://kalshi.com/docs/kalshi-fee-schedule.pdf and https://docs.kalshi.com/getting_started/fee_rounding