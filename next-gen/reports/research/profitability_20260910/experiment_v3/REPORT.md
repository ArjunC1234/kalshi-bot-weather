# Profitability Research

Status: abstain_validation_failed

Selected research candidate: neural

Selection was frozen using August 4-17 only. August 18-31 had been examined in prior work and is retrospective.
September performance was scored after saving the selection. Historical fills are simulated, not actual trades.

September was first scored in experiment_v2 and has now been examined. The corrected experiment_v3 is an audit-controlled retrospective rerun, not another untouched test.

| Window | Trades | Wins | Hit rate | Net PnL | Fees | ROI |
|---|---:|---:|---:|---:|---:|---:|
| validation | 20 | 15 | 75.0% | $3.29 | $0.95 | 7.4% |
| test | 20 | 18 | 90.0% | $7.53 | $0.82 | 16.2% |
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
    "min_net_edge": 0.05,
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
| 2026-08-18 | 0 | 0 | $0.00 | $0.00 |
| 2026-08-19 | 2 | 2 | $0.84 | $0.84 |
| 2026-08-20 | 0 | 0 | $0.00 | $0.84 |
| 2026-08-21 | 1 | 1 | $0.53 | $1.37 |
| 2026-08-22 | 1 | 1 | $1.45 | $2.82 |
| 2026-08-23 | 1 | 1 | $0.73 | $3.55 |
| 2026-08-24 | 2 | 2 | $1.66 | $5.21 |
| 2026-08-25 | 2 | 2 | $1.03 | $6.24 |
| 2026-08-26 | 1 | 1 | $0.45 | $6.69 |
| 2026-08-27 | 2 | 1 | $-2.48 | $4.21 |
| 2026-08-28 | 2 | 1 | $-0.33 | $3.88 |
| 2026-08-29 | 2 | 2 | $0.98 | $4.86 |
| 2026-08-30 | 0 | 0 | $0.00 | $4.86 |
| 2026-08-31 | 4 | 4 | $2.67 | $7.53 |

## Limitations

- General taker multiplier 1 is assumed; fees are conservatively rounded up to cents per order, without rebates.
- One-cent adverse execution is included in selection and reported main results. Price and delayed-quote sensitivities are separate files.
- One position per event, $3 maximum all-in cost per order and $40 daily budget. Visible depth caps size.
- Reconstructed receipt timestamps gate decision time. Filling a quote seen earlier in the collection cycle still requires an execution assumption; the next-quote stress test is only a proxy.
- Bootstrap intervals group trades by day but cannot eliminate search bias or demonstrate a future minimum hit rate.
- The research candidate is not enabled for live trading. An abstention status means the validation criteria failed.

Fee references: https://kalshi.com/docs/kalshi-fee-schedule.pdf and https://docs.kalshi.com/getting_started/fee_rounding