# Synthesis: Generalized Weather + Strategy Engine

Generated: 2026-08-17

## Decision

The best current generalized trading path is:

1. **Primary probability engine:** Neuralcaster v2, `market` mode, rolling 14-day walk-forward, 1-day test folds, `market_probability_blend=0.95`.
2. **Weather guardrail engine:** Neuralcaster v3 Huber residual. Use it as a sanity check and benchmark, not as the primary trading probability model yet.
3. **Strategy engine:** Neuralcaster EV validation-fixed-window strategy with robust validation selection, timing-gate search, CLV gate, minimum trade count, and abstention when no validation policy passes.

This is not a claim that the bot is now proven. It is the best leakage-aware current configuration from available data.

## Why This Wins

The ML research report recommends simple, date-split, leakage-controlled evaluation and warns against large neural nets with small independent sample sizes.

The local evidence report shows a split:

- V3 Huber/market-baseline residual models are excellent for **temperature MAE**.
- Market-aware Neuralcaster v2 is best for **contract probability quality**, which is closer to trading.
- Raw edge is not reliable by itself.
- Strategy selection needs validation gating, not test tuning.

The current Supabase export has **258 labeled city-days**. That is enough to compare broad families, but still too little to trust high-capacity neural structures or permanent city-specific gates.

## Disparities Reconciled

| Disparity | Explanation | Decision |
|---|---|---|
| ML research says favor simple models, but local evidence says v2 GRU is best. | The GRU is not winning from pure weather sequence learning; it wins because market-aware features and market probability blending strongly improve contract probabilities. | Use v2 market as primary, but keep architecture constrained: 1 layer, 48 hidden size, dropout, weight decay, early stopping. |
| V3 Huber has better temperature behavior than many neural runs, but strategy PnL was worse. | Temperature MAE is not the same as tradable edge. The market already prices much of the temperature information. | Use V3 Huber as guardrail/benchmark, not primary strategy input. |
| Earlier local report favored YES-only, but latest current-data holdout selected NO-only. | Side behavior is regime/window sensitive. The latest policy was selected only from validation, then tested forward. It should not become a permanent hard rule. | Let validation choose side; require CLV/trade-count support; abstain if none passes. |
| High raw edge often lost even when model accuracy was decent. | Very high model-market disagreement often means model error or stale context, not confidence. | Robust strategy objective prioritizes hit/CLV lower bounds before raw PnL. |

## Current Data Holdout Results

Fresh export:

`next-gen/data/current_20260701_20260816_lightweight`

Rows:

| Table | Rows |
|---|---:|
| events | 5,888 |
| weather_snapshots | 5,888 |
| market_snapshots | 35,328 |
| final_temperature_labels | 258 |
| settlements | 257 |

### Candidate Weather Engines Tested

| Engine | Report | Result |
|---|---|---|
| V3 robust ensemble | `reports/model/neuralcaster_v3_robust_ensemble_current_20260701_20260816_rolling14_test1` | Accurate enough, but slow and strategy lost |
| V3 Huber | `reports/model/neuralcaster_v3_huber_current_20260701_20260816_rolling14_test1` | Fast and good temperature engine, but strategy lost |
| V2 market rolling14 blend95 | `reports/model/neuralcaster_v2_market_current_20260701_20260816_rolling14_blend95` | Best current trading candidate |

V2 market metrics:

| Metric | Value |
|---|---:|
| Temperature MAE | 0.8428 F |
| RMSE | 1.2118 F |
| Within 1F | 71.50% |
| Within 2F | 90.33% |
| Bracket log loss | 0.5775 |
| Brier | 0.3234 |
| Top-one accuracy | 74.87% |
| Winner probability | 64.30% |

### Strategy Holdout

Report:

`reports/strategy/neuralcaster_ev_v2_market_timing_grid_current_train_20260803_20260809_test_20260810_20260816`

Validation window:

- 2026-08-03 to 2026-08-09

Final test window:

- 2026-08-10 to 2026-08-16

Selected validation policy:

| Setting | Value |
|---|---:|
| Side | NO only |
| Min hours elapsed | 10 |
| Min EV | 0.00 |
| Max spread | 0.10 |
| Entry price band | 0.50 to 0.80 |
| Max positions/event | 1 |
| Validation trades | 13 |
| Validation PnL | +$8.12 |
| Validation ROI | 23.28% |
| Validation hit rate | 76.92% |
| Validation positive CLV | 76.92% |

Final test result:

| Metric | Value |
|---|---:|
| Trades | 26 |
| PnL | +$3.27 |
| ROI | 4.62% |
| Hit rate | 65.38% |
| Positive CLV | 69.23% |
| Max drawdown | -$8.04 |
| Avg entry price | 0.6327 |
| Avg model probability | 0.6411 |
| Avg edge | 0.0084 |

Failed comparable candidates:

| Candidate | Test PnL | ROI |
|---|---:|---:|
| V3 robust ensemble EV | -$20.26 | -19.25% |
| V3 Huber EV | -$5.41 | -4.86% |
| V3 Huber EV with timing grid | -$11.83 | -12.88% |

## Engine Changes Implemented

### Weather Engine

- Added Neuralcaster v3 `robust_ensemble` model kind.
- Changed Neuralcaster v3 registry default to `huber_residual` because it is faster and better supported by evidence.
- Updated Neuralcaster v2 rolling-eval registry defaults to the recommended market-aware contract model:
  - `mode=market`
  - `training_policy=rolling`
  - `train_days=14`
  - `test_days=1`
  - `market_probability_blend=0.95`

### Strategy Engine

- Added timing-gate search to Neuralcaster EV validation:
  - no gate
  - 6 hours elapsed
  - 10 hours elapsed
  - 14 hours elapsed
- Added robust validation objective:
  - prioritizes hit-rate lower bound
  - prioritizes positive-CLV lower bound
  - penalizes drawdown
  - still includes PnL as a later tie-breaker
- Added validation CLV requirement.
- Added abstention behavior when no policy passes validation.
- Added UI registry entry for `neuralcaster_ev`.
- Fixed control-plane boolean materialization so `false` can become `--no-allow-no` / `--no-allow-yes`.

## Practical Default Run

Use this workflow:

1. Export latest settled data through the latest fully labeled target date.
2. Run `Neuralcaster v2 -> Rolling Evaluation` with the new defaults.
3. Run `Neuralcaster EV -> Validation Fixed Window`.
4. Use validation dates before the final test dates.
5. Do not tune after seeing final test PnL.

Current recommended strategy settings:

| Field | Recommended |
|---|---:|
| `validation_objective` | `robust` |
| `min_validation_trades` | 10 |
| `min_validation_positive_clv` | 0.50 |
| `daily_budget` | 40 |
| `max_order_cost` | 3 |
| `max_positions_per_event` | 1 |
| `entry_policy` | `best-ev` |

Let validation select side, price band, min EV, max spread, and timing gate.

## Caution

The current result is profitable but small. The correct operational interpretation is:

> This configuration is the strongest leakage-aware current candidate. It should be paper-forward tested, not deployed as proven live edge.

The main next milestone is repeated forward validation over more settled days. If the same v2 market + robust EV protocol keeps positive CLV and positive PnL across multiple new windows, confidence increases materially.
