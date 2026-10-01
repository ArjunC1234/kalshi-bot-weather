# Current Model Iteration Runbook

Last updated: 2026-08-28

## Active Candidate

Use this iteration when asked to rerun the current best model on coming weeks.

Name: Neuralcaster V2 Weather-Only Rolling14 Fixed-Gate EV

Purpose: paper-trade candidate for continued out-of-sample monitoring. This is promising, but not yet proven generalized or ready for meaningful real-money sizing.

## Data Snapshot Used

- Supabase label coverage at training time: 318 final labels, 53 labeled target dates, 6 cities
- Date range: 2026-07-01 through 2026-08-26
- Export profile: `lightweight_model_eval`
- Export path: `data/current_20260701_20260826_lightweight`

Export command:

```powershell
python -m control.cli export --profile lightweight_model_eval --start 2026-07-01 --end 2026-08-26 --output data/current_20260701_20260826_lightweight
```

## Model Training Command

Primary candidate model:

```powershell
python maxtemp-engine\neuralcaster\v2\cli.py rolling-eval --data data/current_20260701_20260826_lightweight --output reports/model/neuralcaster_v2_weather_current_20260701_20260826_rolling14 --mode weather --training-policy rolling --train-days 14 --test-days 1 --epochs 350 --market-probability-blend 0.0
```

Important settings:

- Model family: `neuralcaster_v2`
- Mode: `weather`
- Market awareness: disabled
- Training policy: rolling walk-forward
- Train window: 14 days
- Test step: 1 day
- Epochs: 350
- Market probability blend: 0.0

## Strategy Gate

This fixed gate was selected on validation data, not on the final test week.

Validation source:

- Validation window: 2026-08-13 through 2026-08-19
- Original validation-selected report: `reports/strategy/neuralcaster_ev_v2_market_current_train_20260813_20260819_test_20260820_20260826`

Selected gate:

- `min_ev`: 0.02
- `max_spread`: 0.10
- `min_entry_price`: 0.50
- `max_entry_price`: 0.65
- `daily_budget`: 40
- `max_order_cost`: 3
- `max_contracts_per_order`: 20
- `max_no_contracts_per_order`: 10
- `max_positions_per_event`: 1
- `entry_policy`: `best-ev`
- `allow_yes`: true
- `allow_no`: true

## Strategy Evaluation Command

Use this pattern for the next completed week. Replace only:

- export path
- model report path
- output path
- `--start-date`
- `--end-date`

Current tested command:

```powershell
python -m strategy.cli neural-ev --data data/current_20260701_20260826_lightweight --model-report reports/model/neuralcaster_v2_weather_current_20260701_20260826_rolling14 --output reports/strategy/neuralcaster_ev_v2_weather_current_fixed_policy_test_20260820_20260826 --start-date 2026-08-20 --end-date 2026-08-26 --min-ev 0.02 --max-spread 0.10 --min-entry-price 0.50 --max-entry-price 0.65 --daily-budget 40 --max-order-cost 3 --max-contracts-per-order 20 --max-no-contracts-per-order 10 --max-positions-per-event 1 --entry-policy best-ev
```

## Latest Holdout Result

Test window: 2026-08-20 through 2026-08-26

- Trades: 38
- Orders: 38
- Signals scanned: 11,652
- Contracts: 185
- Total risk/staked: $103.58
- Total PnL: +$51.42
- ROI: 49.64%
- Hit rate: 84.21%
- Positive CLV rate: 81.58%
- Mean CLV: 0.2547
- Median CLV: 0.3950
- Max drawdown: -$4.03

Daily PnL:

- 2026-08-20: +$15.45
- 2026-08-21: +$9.00
- 2026-08-22: +$1.78
- 2026-08-23: +$7.08
- 2026-08-24: +$5.67
- 2026-08-25: +$4.80
- 2026-08-26: +$7.64

Removing 2026-08-20:

- Trades: 32
- Risk/staked: $87.03
- PnL: +$35.97
- ROI: 41.3%
- Hit rate: 81.2%

## Next-Day Paper Check

This first reran the same one-week candidate setup after 2026-08-27 settled, then was reproduced with a frozen model artifact so future checks can score without retraining.

- Fresh export path: `data/current_20260701_20260827_lightweight`
- Model report: `reports/model/neuralcaster_v2_weather_current_20260701_20260827_rolling14`
- Frozen model artifact: `models/neuralcaster_v2/weather_rolling14_asof_20260827_seed70`
- Frozen score report: `reports/model/neuralcaster_v2_weather_frozen_asof_20260827_score_20260827`
- Strategy report: `reports/strategy/neuralcaster_ev_v2_weather_current_fixed_policy_test_20260827`
- Frozen strategy report: `reports/strategy/neuralcaster_ev_v2_weather_frozen_asof_20260827_test_20260827`
- Test date: 2026-08-27
- Gate: same one-week fixed gate, `min_ev=0.02`, `max_spread=0.10`, `min_entry_price=0.50`, `max_entry_price=0.65`, `entry_policy=best-ev`

Frozen artifact command:

```powershell
python maxtemp-engine\neuralcaster\v2\cli.py train-artifact --data data/current_20260701_20260827_lightweight --artifact models/neuralcaster_v2/weather_rolling14_asof_20260827_seed70 --train-start 2026-08-13 --train-end 2026-08-26 --mode weather --max-seq-len 24 --hidden-size 48 --layers 1 --dropout 0.10 --epochs 350 --patience 45 --learning-rate 0.003 --weight-decay 0.01 --min-training-examples 60 --seed 70
```

No-retraining score command:

```powershell
python maxtemp-engine\neuralcaster\v2\cli.py score-artifact --data data/current_20260701_20260827_lightweight --artifact models/neuralcaster_v2/weather_rolling14_asof_20260827_seed70 --output reports/model/neuralcaster_v2_weather_frozen_asof_20260827_score_20260827 --start-date 2026-08-27 --end-date 2026-08-27 --probability-floor 0.001
```

Frozen strategy command:

```powershell
python -m strategy.cli neural-ev --data data/current_20260701_20260827_lightweight --model-report reports/model/neuralcaster_v2_weather_frozen_asof_20260827_score_20260827 --output reports/strategy/neuralcaster_ev_v2_weather_frozen_asof_20260827_test_20260827 --start-date 2026-08-27 --end-date 2026-08-27 --min-ev 0.02 --max-spread 0.10 --min-entry-price 0.50 --max-entry-price 0.65 --daily-budget 40 --max-order-cost 3 --max-positions-per-event 1 --entry-policy best-ev
```

Result:

- Trades: 5
- Total risk/staked: $12.98
- Total PnL: -$7.98
- ROI: -61.48%
- Hit rate: 20.00%
- Positive CLV rate: 20.00%
- Mean CLV: -0.3650
- Median CLV: -0.5450
- Max drawdown: -$7.98

Trade detail:

| City | Side | Checkpoint | Entry | Model probability | Edge | Contracts | PnL | Hit | CLV |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| nyc | no | utc_07 | 0.65 | 0.9411 | 0.2911 | 4 | -$2.60 | 0 | -0.635 |
| aus | no | utc_11 | 0.55 | 0.7583 | 0.2083 | 5 | -$2.75 | 0 | -0.545 |
| okc | no | utc_11 | 0.52 | 0.6929 | 0.1729 | 5 | -$2.60 | 0 | -0.515 |
| den | no | utc_13 | 0.51 | 0.7820 | 0.2720 | 5 | +$2.45 | 1 | +0.485 |
| la | no | utc_13 | 0.62 | 0.9615 | 0.3415 | 4 | -$2.48 | 0 | -0.615 |

## Three-Week Fixed-Gate Diagnostic

This applies the same documented fixed gate to 2026-08-06 through 2026-08-26.

Important: this is useful only as a stability diagnostic, but it is not a clean non-leaky proof window because the fixed gate was originally selected from 2026-08-13 through 2026-08-19, which sits inside this wider historical range. The clean 3-week train-selected test below is the correct result to use for historical evaluation.

Report path: `reports/strategy/neuralcaster_ev_v2_weather_current_fixed_policy_test_20260806_20260826`

Command:

```powershell
python -m strategy.cli neural-ev --data data/current_20260701_20260826_lightweight --model-report reports/model/neuralcaster_v2_weather_current_20260701_20260826_rolling14 --output reports/strategy/neuralcaster_ev_v2_weather_current_fixed_policy_test_20260806_20260826 --start-date 2026-08-06 --end-date 2026-08-26 --min-ev 0.02 --max-spread 0.10 --min-entry-price 0.50 --max-entry-price 0.65 --daily-budget 40 --max-order-cost 3 --max-contracts-per-order 20 --max-no-contracts-per-order 10 --max-positions-per-event 1 --entry-policy best-ev
```

Result:

- Trades: 96
- Orders: 96
- Signals scanned: 34,920
- Contracts: 473
- Total risk/staked: $261.83
- Total PnL: +$35.17
- ROI: 13.43%
- Hit rate: 63.54%
- Positive CLV rate: 62.50%
- Mean CLV: 0.0689
- Median CLV: 0.3700
- Max drawdown: -$30.25

Daily PnL:

- 2026-08-07: +$4.30
- 2026-08-08: +$6.53
- 2026-08-09: -$5.90
- 2026-08-10: -$0.55
- 2026-08-11: -$8.40
- 2026-08-12: -$0.19
- 2026-08-13: -$7.25
- 2026-08-14: -$7.96
- 2026-08-15: +$8.25
- 2026-08-16: +$1.28
- 2026-08-17: -$4.11
- 2026-08-18: +$9.75
- 2026-08-19: -$12.00
- 2026-08-20: +$15.45
- 2026-08-21: +$9.00
- 2026-08-22: +$1.78
- 2026-08-23: +$7.08
- 2026-08-24: +$5.67
- 2026-08-25: +$4.80
- 2026-08-26: +$7.64

## Clean Three-Week Train-Selected Test

This is the correct historical 3-week test. The gate is selected only from the pre-test training/validation window, then applied once to the next 3 weeks.

- Gate-selection window: 2026-07-16 through 2026-08-05
- Test window: 2026-08-06 through 2026-08-26
- Report path: `reports/strategy/neuralcaster_ev_v2_weather_current_train_20260716_20260805_test_20260806_20260826`

Selected gate from training:

- `min_ev`: 0.0
- `max_spread`: 0.10
- `min_entry_price`: 0.50
- `max_entry_price`: 0.99
- `min_hours_elapsed`: 14.0
- `allow_yes`: true
- `allow_no`: true
- `entry_policy`: `best-ev`
- `daily_budget`: 40
- `max_order_cost`: 3
- `max_contracts_per_order`: 20
- `max_no_contracts_per_order`: 10
- `max_positions_per_event`: 1

Training/validation result:

- Trades: 47
- Total risk/staked: $126.59
- Total PnL: +$9.41
- ROI: 7.43%
- Hit rate: 80.85%
- Positive CLV rate: 78.72%
- Max drawdown: -$9.01

3-week test result:

- Trades: 46
- Orders: 46
- Signals scanned: 34,920
- Contracts: 178
- Total risk/staked: $125.17
- Total PnL: +$4.83
- ROI: 3.86%
- Hit rate: 78.26%
- Positive CLV rate: 76.09%
- Mean CLV: 0.0260
- Median CLV: 0.1100
- Max drawdown: -$7.71

Daily PnL:

- 2026-08-06: +$0.51
- 2026-08-07: +$1.71
- 2026-08-08: -$1.67
- 2026-08-09: -$0.60
- 2026-08-10: -$1.74
- 2026-08-11: +$1.56
- 2026-08-12: $0.00
- 2026-08-13: +$1.20
- 2026-08-14: -$0.02
- 2026-08-15: -$2.16
- 2026-08-16: -$2.80
- 2026-08-17: +$2.30
- 2026-08-18: +$0.24
- 2026-08-19: +$0.84
- 2026-08-20: -$0.54
- 2026-08-21: $0.00
- 2026-08-22: +$2.80
- 2026-08-23: -$0.55
- 2026-08-24: -$2.16
- 2026-08-25: +$1.66
- 2026-08-26: +$4.25

## Baseline Comparison From Same Holdout

| Model / strategy | Trades | Risk | PnL | ROI | Hit rate |
|---|---:|---:|---:|---:|---:|
| V2 weather-only, fixed gate | 38 | $103.58 | +$51.42 | 49.64% | 84.21% |
| V2 market-aware blend95, validation-selected | 17 | $47.37 | +$13.63 | 28.77% | 70.59% |
| V3 Huber residual, same fixed gate | 37 | $101.29 | -$9.29 | -9.17% | 51.35% |

## Interpretation

Current best candidate is V2 weather-only rolling14 with the fixed EV gate above.

Do not call this generalized yet. The result is out-of-sample for one adjacent 7-day holdout, but the dataset is still small. Treat the next 3-4 weeks as the minimum paper-trading confirmation period, with no manual retuning after seeing results.

## Next Rerun Protocol

When new labeled days are available:

1. Export the full current dataset from 2026-07-01 through the latest labeled date.
2. For historical walk-forward research, use `rolling-eval`.
3. For forward monitoring or any later evaluation, train a frozen artifact once with `train-artifact`.
4. Score new settled days from that artifact with `score-artifact`; do not rerun `rolling-eval` if the goal is only evaluation.
5. Apply the same fixed gate to the scored model report.
6. Compare daily PnL, hit rate, positive CLV, side mix, city mix, and drawdown.
7. Do not change the gate unless a separate validation window selects a new gate before looking at the next test results.
