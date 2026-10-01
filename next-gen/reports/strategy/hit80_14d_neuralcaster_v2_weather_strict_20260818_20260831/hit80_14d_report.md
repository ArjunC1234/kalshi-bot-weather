# Hit80 14-Day Research Report

- Validation window: 2026-07-15 through 2026-08-17
- Frozen test window: 2026-08-18 through 2026-08-31
- Selection status: selected_best_available_policy_below_validation_hit_target
- Trades: 41
- Hit rate: 0.6098
- 80% Wilson lower bound: 0.5098
- Gross PnL: 7.5000
- Gross ROI: 0.0667
- Max drawdown: -18.1100

## Selected Policy

- side_mode: all
- min_ev: 0.05
- max_ev: None
- max_spread: 0.05
- min_entry_price: 0.5
- max_entry_price: 0.65
- min_model_probability: 0.75
- min_hours_elapsed: None
- entry_policy: best-ev

## Data Audit

- source_export_id: current_20260701_20260831_lightweight
- export_start: 2026-07-01
- export_end: 2026-08-31
- target_dates: ['2026-08-18', '2026-08-19', '2026-08-20', '2026-08-21', '2026-08-22', '2026-08-23', '2026-08-24', '2026-08-25', '2026-08-26', '2026-08-27', '2026-08-28', '2026-08-29', '2026-08-30', '2026-08-31']
- target_date_count: 14
- cities: ['aus', 'den', 'la', 'mia', 'nyc', 'okc']
- city_count: 6
- event_rows: 661
- market_rows: 11874
- settlement_rows: 84
- final_temperature_rows: 168
- settled_city_days: 84
- settlement_sources: {'compatible': False, 'label_source': 'nws_cli_daily', 'label_sources': {'nws_cli_daily': 348, 'weather_company_daily': 348}, 'market_settlement_source': 'unknown', 'market_settlement_sources': {'unknown': 612}, 'settlement_row_sources': {'unknown': 348}, 'settlement_source': 'unknown', 'warnings': ['Market rule settlement source is unknown; verify Kalshi rules before trading.']}

## Fee Stress

| Fee/contract | Total fee | Net PnL | Net ROI |
|---:|---:|---:|---:|
| 0.000 | 0.00 | 7.50 | 0.0667 |
| 0.005 | 0.99 | 6.51 | 0.0578 |
| 0.010 | 1.99 | 5.51 | 0.0490 |
| 0.015 | 2.98 | 4.52 | 0.0401 |
| 0.020 | 3.98 | 3.52 | 0.0313 |

This is a paper-only research result. Policy selection used only the validation window.
