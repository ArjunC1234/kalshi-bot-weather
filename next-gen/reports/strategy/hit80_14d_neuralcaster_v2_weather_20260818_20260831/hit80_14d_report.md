# Hit80 14-Day Research Report

- Validation window: 2026-07-15 through 2026-08-17
- Frozen test window: 2026-08-18 through 2026-08-31
- Selection status: selected_policy_met_validation_hit_target
- Trades: 64
- Hit rate: 0.8281
- 80% Wilson lower bound: 0.7598
- Gross PnL: 15.1400
- Gross ROI: 0.0891
- Max drawdown: -8.8400

## Selected Policy

- side_mode: all
- min_ev: 0.05
- max_ev: None
- max_spread: 0.05
- min_entry_price: 0.65
- max_entry_price: 0.9
- min_model_probability: 0.85
- min_hours_elapsed: None
- entry_policy: best-ev

## Baseline Comparison

| Policy | Trades | Hit rate | Gross PnL | Gross ROI | Max drawdown |
|---|---:|---:|---:|---:|---:|
| broad_ev_50_65 | 76 | 0.5658 | 2.08 | 0.0100 | -40.43 |
| prior_no_60_65 | 58 | 0.7069 | 21.00 | 0.1346 | -24.56 |
| hit80_selected | 64 | 0.8281 | 15.14 | 0.0891 | -8.84 |

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
| 0.000 | 0.00 | 15.14 | 0.0891 |
| 0.005 | 1.14 | 14.01 | 0.0825 |
| 0.010 | 2.27 | 12.87 | 0.0758 |
| 0.015 | 3.40 | 11.74 | 0.0691 |
| 0.020 | 4.54 | 10.60 | 0.0624 |

This is a paper-only research result. Policy selection used only the validation window.
