# Profitability Investigation

Completed September 12, 2026. The requested reliably profitable, minimum-80%-hit model was not established.

## Corrected Results

The selected exploratory policy buys NO, with quoted ask $0.50-$0.85, at least 10 hours into the climate day, predicted win probability at least 85%, spread at most $0.05, and estimated edge after costs at least $0.05. It takes at most one position per event. Each order costs at most $3 including fees; the daily budget is $40.

| Window | Trades | Wins | Hit rate | Net simulated PnL | Cash deployed |
|---|---:|---:|---:|---:|---:|
| August 4-17 validation | 20 | 15 | 75.0% | +$3.29 | $44.71 |
| August 18-31 retrospective test | 20 | 18 | 90.0% | +$7.53 | $46.47 |
| September 1-10 replay | 22 | 10 | 45.5% | -$20.03 | $53.03 |

All main figures include a general taker-fee multiplier of 1, conservative per-order cent rounding without rebates, and $0.01 adverse execution per contract. No actual trades were placed.

No candidate passed all predeclared validation requirements: at least 20 trades, at least 80% observed wins, positive aggregate net profit, and positive net profit in both validation weeks. The saved operational policy is abstention. That prevents deploying this failed candidate; it is not a claim of profitability.

The August 14-day daily breakdown and trade-level ledgers are in `experiment_v3/REPORT.md`, `experiment_v3/test_daily.csv`, and `experiment_v3/test_trades.csv`. September 11 did not have complete six-city settlements in the export; September 1-10 is ten days, not a new 14-day test.

## Verified Problems And Repairs

1. **Quote lookahead.** The old selector ranked opportunities over the entire future event day. It now selects chronologically. Holding probabilities, policy and old data fixed, changing timing alone moved frozen-model gross PnL from -$3.04 to -$6.94, and rolling-model gross PnL from +$15.14 to +$7.28. This isolates timing, not the entire rolling-versus-frozen difference.
2. **Unordered export pagination.** A historical export repeated 2,109 identical event keys and joined only 36,498 of 49,152 quotes. Stable primary-key ordering yielded zero repeated event keys and joined all 49,152 quotes. The shared Supabase client and the research exporter now specify stable ordering.
3. **Contract-unit conversion.** The collector used its cents converter on quantities. A stored 0.1725-contract quote was 17.25 contracts in the original API payload. The parser now converts only legacy cent-denominated price fields. Historical and September research copies reconstruct quantities from original payloads; originals and remote facts remain intact.
4. **Training-label availability.** Five initial validation trades used rolling-model reports whose latest training settlement arrived after the decision. The corrected runner withholds those probabilities until all required training settlements are available.
5. **Missing settlements and duplicate predictions.** Missing winners no longer count as NO wins. Identical duplicates are collapsed, conflicting probabilities are rejected, and joins validate their cardinality.

Quote reconstruction checked 765 historical and 328 September market payloads. Recorded source receipt times now determine the earliest decision time; verified quantities cap order size. Immediate fills at a quote observed earlier in the collection cycle remain an assumption, tested separately with a next-quote limit-price proxy.

## What The Evidence Suggests

- In September, average predicted win probability was 93.2%, but observed wins were 45.5%. This is a severe calibration failure in the selected trades.
- Miami and Austin account for 9 of the 12 September losses. Eight losses settled above the model's 95th-percentile temperature forecast. Across all 22 entries, nine settled above that percentile and one below the 5th percentile. These are selected-trade diagnostics, not estimates of unconditional forecast coverage.
- None of the 12 losing trades directly contradicted the observed-high floor. That specific hypothesis did not explain these losses.
- Stored market rules switch from NWS to The Weather Company starting August 14 in all six cities. A model trained mostly before this switch may be poorly matched to later outcomes. That causal claim remains a hypothesis; weather changes, forecast-source errors, stale model weights, selection bias and underestimated uncertainty are also plausible contributors.
- The last-selected training labels were created later, but all 264 values equal earlier Kalshi settlement temperatures. Their creation dates alone therefore do not establish future information. Availability is checked against those earlier equivalent settlement facts.
- Adding market information and strong regularization generally caused the alternative models to abstain. The tested alternatives did not demonstrate a usable profitable edge.

## Evidence Limits

Seven probability variants and 48 policies each were evaluated, for 336 validation combinations. August was already researched extensively. September was initially scored only after the v2 lock, but the later audit correction makes the v3 September evaluation retrospective. These are not independent repeated confirmations.

The descriptive day-block bootstrap 95% interval for August net PnL is approximately -$1.35 to +$15.38. September's is approximately -$29.58 to -$11.09. Such intervals do not correct repeated-search bias, prove fills, or guarantee future results.

## Artifacts And Reproduction

- `PLAN.md`: protocol recorded before the new experiments.
- `audit/timing_ablation.csv`: same-model, same-policy timing comparison.
- `settlement_source_timeline.csv`: source labels extracted from stored rule text.
- `experiment_v1/ABORTED.md`: export-corrupted run, rejected before selection.
- `experiment_v2/SUPERSEDED.md`: first selection, superseded after the label-timing audit.
- `experiment_v3/selection_lock.json`: corrected frozen policy, model hashes and validation result.
- `experiment_v3/models.joblib`: regularized model comparators.
- `experiment_v2/neural_artifact/`: the frozen neural model, trained through August 17. Its reproduced predictions match all 11,874 original unique frozen probability rows exactly.
- `experiment_v3/operational_policy.json`: explicit abstention status.
- `experiment_v3/verification.json`: independent ledger checks and residual limitations.
- `experiment_v3/artifact_manifest.json`: hashes of models, prediction reports and input exports, compiled after scoring for reproduction.
- `experiment_v3/fresh_trade_diagnostics.csv`: forecast and settlement comparison for every September trade.

From `next-gen`, reproduce the corrected scoring without refitting or selecting a new policy:

```powershell
python -m scripts.profitability_research score --history data/profitability_history_verified_20260701_20260901 --rolling-report reports/model/neuralcaster_v2_weather_current_20260701_20260831_rolling14 --fixed-report reports/model/neuralcaster_v2_weather_strict_train_20260701_20260817_test_20260818_20260831 --fresh data/profitability_fresh_verified_20260901_20260912 --fresh-neural-report reports/research/profitability_20260910/experiment_v2/neural_fresh --fresh-end 2026-09-11 --output reports/research/profitability_20260910/experiment_v3
```

Verification passed 42 collector and research tests plus 16 backtest tests. All 62 corrected trade ledger entries reconcile against settlements, fees, budgets, visible quantities and receipt times. None has a detected training-label availability violation. These checks have the limits listed in `verification.json`.

Future work should test settlement-source-aware forecast bias and uncertainty calibration against a market-only comparator, using dated, already-available labels and newly collected forward observations. The September loss does not justify deleting losing cities or retuning thresholds on that same window and calling the result confirmed.

Primary references: [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf), [fee rounding](https://docs.kalshi.com/getting_started/fee_rounding), and [contract quantity units](https://docs.kalshi.com/getting_started/fixed_point_migration).
