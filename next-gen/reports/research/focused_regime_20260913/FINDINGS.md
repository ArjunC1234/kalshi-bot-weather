# Focused Experiment Findings

Completed September 13, 2026. No profitable, minimum-80%-hit model was established.
The principal result is **run_v2**, with full six-city coverage. All results are
retrospective development evidence, not a new forward test.

## What Was Tested

- Mixed-history fit: 108 city-days, August 6-23. August 4-5 had no collected data.
- Post-switch fit: 60 city-days, August 14-23, all Weather Company settlement rules.
- Separate calibration: 24 city-days, August 24-27.
- Evaluation: 84 city-days, August 28-September 10, exactly 14 calendar days.
- Every model used the same earliest complete afternoon snapshot per event.
- Model/policy selection was saved before evaluation scoring. No parameter sweep.

Both training histories were tested with market-only and market-plus-weather
probabilities, alongside raw market probabilities. Training and calibration used
only settlements available by their respective historical cutoff times. The
weather correction used training-only forecast bias and uncertainty estimates.

## Results

| Model | Event log loss | Multiclass Brier | Trades | Net simulated PnL |
|---|---:|---:|---:|---:|
| Raw market | 0.476374 | 0.281283 | 0 | $0.00 |
| Mixed market, calibrated | 0.463827 | 0.283185 | 0 | $0.00 |
| Mixed market + weather, calibrated | 0.463827 | 0.283185 | 0 | $0.00 |
| Post-switch market, calibrated | 0.463788 | 0.283026 | 0 | $0.00 |
| Post-switch market + weather, calibrated | 0.463788 | 0.283026 | 0 | $0.00 |

Lower scores are better. Brier sums squared errors across six brackets per event.
With zero trades, hit rate is **undefined**, not 0% or 100%. All 14 daily ledger
rows contain six eligible events, zero trades and $0 net PnL.

Both fitted weather coefficients were zero. Their predictions exactly equal the
corresponding market-only models. An independent finite-difference check found
that adding a small positive weather weight worsened training log loss even
before its regularization penalty: derivatives +0.029492 (mixed) and +0.026409
(post-switch). This concerns this fixed weather representation, not every possible
weather signal or alternative model.

Post-switch versus mixed-history log-loss difference was -0.000039, with a
descriptive day-bootstrap 95% interval [-0.000758, +0.000641]. This does not provide
clear evidence that post-switch-only training helped. It also does not isolate
the causal effect of the rule change: recency and sample size changed together.

Calibration improved mean log loss but worsened Brier versus raw market. For the
calibration-selected mixed-history challenger, log-loss difference versus raw
market was -0.012546, with interval [-0.034166, +0.010304]. Neither that result nor
these previously studied dates establishes a dependable probability advantage.

## Temperature Versus Trading

On the same 84 evaluation events, uncorrected anchor MAE was 1.466 F. Mixed-history
bias correction reduced MAE to 1.018 F; post-switch correction reduced it to
1.088 F. Nominal 90% Gaussian interval coverage was 94.0% and 90.5%, respectively.
These are descriptive historical results, not guarantees of future coverage.

Improving the temperature forecast relative to the raw forecast average did not
produce incremental value over the market in this correction model.

Why no trades: of 504 NO brackets at the selected snapshots, 46 met the price
band, 30 also met spread/depth requirements, and four also met the 85% predicted
win threshold. None met the five-cent edge requirement after modeled fees and
one-cent adverse execution. No thresholds were loosened after seeing this result.
Zero/two-cent and next-quote sensitivities also generated no trades.

## Coverage Repair And Verification

Run_v1 used an older derivative export whose receipt verification was conditioned
on quote prices. It covered only 61 evaluation city-days. That limitation was
documented in COVERAGE_AMENDMENT.md before extending verification and running v2.
Run_v1 is retained as a restricted-coverage result, not independent confirmation.

For v2, all climate-hour 14-19 observations were verified without price or outcome
selection: 936 August and 360 September original market payloads, with 10,368
source metadata records and 7,776 reconstructed quote rows. An additional raw
price comparison checked 31,104 dollar-price fields and found zero mismatches.
Original exports and remote facts were not overwritten.

All 69 focused/research/collector/backtest tests passed. The supplemental audit
checks input/model/code hashes, prediction reproduction, matching evaluation
events, timing and settlement-temperature consistency. No actual orders were
placed, and the live strategy was not modified.

## Decision

No candidate passed the predeclared requirements. The saved forward-test
specification is **inactive_no_qualified_candidate**, with no start date and live
trading disabled. There is no unattended 14-day test running.

This experiment does not support promoting this afternoon weather correction or
assuming that the settlement-source change explains the prior losses. A distinct
future experiment would need a new justification, such as information available
before the market absorbs it, rather than another threshold adjustment on these
same dates. That hypothesis has not been tested here.

## Reproduction

From `next-gen`, use a new output directory to preserve existing locks:

```powershell
python -m scripts.focused_regime_experiment --history data/focused_history_afternoon_verified_20260701_20260901 --fresh data/focused_fresh_afternoon_verified_20260901_20260912 --output reports/research/focused_regime_20260913/reproduction
python -m scripts.audit_focused_regime --run reports/research/focused_regime_20260913/run_v2 --history data/focused_history_afternoon_verified_20260701_20260901 --fresh data/focused_fresh_afternoon_verified_20260901_20260912
python -m unittest scripts.tests.test_focused_regime_experiment scripts.tests.test_profitability_research scripts.tests.test_research_data_integrity tests.test_collector_v3
python -m unittest discover -s backtest/tests -p 'test_*.py'
```

See `run_v2/REPORT.md` for the daily breakdown, `models.json` and `model_lock.json`
for frozen parameters, `temperature_diagnostics.csv` for source/city errors,
`entry_gate_counts.csv` for abstention reasons, and `supplemental_audit.json` for
verification. The protocol and coverage amendment preserve the experiment history.

Method references: [probability calibration](https://scikit-learn.org/stable/modules/calibration.html),
[Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf),
[fee rounding](https://docs.kalshi.com/getting_started/fee_rounding).
