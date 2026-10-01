# Timing And Smaller-Edge Investigation

Completed September 13, 2026. Neither tested avenue qualifies for promotion.
These are retrospective development results, not new forward evidence. No live
orders were placed, the live strategy was not modified, and no unattended test
was activated.

## Smaller Edges

The previously frozen, calibration-selected mixed-history model was reused without
refitting. The same 84 city-days and afternoon snapshots were scored from August
28 through September 10. Only the minimum net edge changed. The primary alternative
was one cent, declared before scoring; zero/two cents were sensitivities, not a
search from which the most profitable result was selected.

| Minimum net edge | Trades | Wins | Observed hit rate | Net simulated PnL |
|---|---:|---:|---:|---:|
| $0.00 | 3 | 1 | 33.3% | -$3.02 |
| $0.01, primary | 3 | 1 | 33.3% | -$3.02 |
| $0.02 | 0 | 0 | Undefined | $0.00 |
| $0.05, previous rule | 0 | 0 | Undefined | $0.00 |

All rows include conservative per-order taker fees and one-cent adverse execution.
The primary rule deployed $6.02 across seven contracts; net PnL was -$2.58 in the
first week and -$0.44 in the second. Removing the best day would leave -$3.44.
The descriptive day-bootstrap 95% PnL interval was [-$8.62, +$0.84]. Three trades
cannot establish a dependable hit rate or rule out a positive long-run mean.

The trades were Austin August 31 (-$2.58), Austin September 7 (-$0.86), and New
York September 8 (+$0.42). All quoted asks were $0.84. Average predicted win
probability was 87.4%; this tiny selected sample won once. Cash breakeven required
an 86% contract-weighted hit rate, while actual contract-weighted wins were 42.9%.

Zero slippage produced the same three primary-rule trades at -$2.95. Two-cent
slippage removed all three at the primary one-cent edge gate. The fixed-intention,
next-quote limit proxy filled two at -$2.16. The independent audit confirmed both
of those quote requests started after their signals. These are simulated fills,
not proof of live execution. The raw-market control made no trades.

Conclusion: lowering this gate did not reveal profitability in this window. The
sample is too small to infer that every smaller-edge strategy must fail.

## Hour-Scale Timing

The signal was a versioned NWS daily daytime-high revision, not a remaining-window
forecast maximum or a forecast feature incorporating observed temperatures.
Training used August 14-27 only, with next-price labels available before August
28 00:00 UTC. Evaluation used August 28-September 10. Both periods contained 84
city-days and 588 complete hourly transitions in the predeclared morning window.

The market-only Ridge model used market-implied bracket index, entropy, recent
market movement and climate hour. The challenger added the NWS high revision.
Scaling and coefficients used fitting data only. Entry quotes had to be requested
after the forecast receipt; the next-hour quote supplied the prediction target.
No settlement labels were used in either timing model.

| Predictor | Mean squared error of next bracket-index change |
|---|---:|
| Market-only model | 0.043344 |
| Market plus NWS revision | 0.043404 |
| No-change prediction, post-hoc descriptive control | 0.043523 |

Errors are in squared bracket-index units, not degrees Fahrenheit or dollars.
The weather-minus-market error difference was +0.000060, with descriptive
day-bootstrap 95% interval [-0.000024, +0.000196]. There was no clear improvement.
The no-change control was checked after scoring and was not a fitted or selected
trading candidate.

There were only **nine first material revisions** of at least 1 F during evaluation,
on six days. On that subset, the weather-minus-market error difference was
+0.004306, with interval [-0.001883, +0.011077]. Signed subsequent price movement
was approximately zero (-0.00106 bracket units), with interval [-0.11241, +0.09017].
The within-day sign randomization has only one day with both revision signs;
its reported p-value is not strong evidence about a general timing effect.

Three diagnostic NO-contract probes met the fixed price/spread/depth conditions,
all in Austin. Their entry-ask to later-bid changes averaged +$0.04 per contract.
After fees on both legs and one-cent adverse execution on each leg, average net
markout was -$0.02, with descriptive interval [-$0.05, +$0.03]. The individual net
markouts were -$0.05, -$0.04 and +$0.03. Three conditional probes do not establish
a raw predictive edge, and they are not an 80%-calibrated trading strategy.

## What The Data Cannot Resolve

For the nine revisions, the median stored NWS updateTime was 48.5 minutes old at
receipt; generatedAt was 31.9 minutes old. The median wait to a quote whose request
started after receipt was 60.2 minutes. The median updateTime-to-entry age was
108.6 minutes. These are recorded source timestamps, not independently observed
first-publication times.

This cadence cannot identify a seconds-to-minutes reaction advantage. The data
show what remained at much later sampled quotes. They do not establish that the
market always absorbed the signal immediately, or that faster observation would
produce profit. Overlapping information sources, forecast representation, source
freshness, saturation of market prices and sampling uncertainty remain plausible
alternative explanations.

The next useful evidence for the faster-timing hypothesis would be an observation-only
capture with source versions, exact request/receipt times, and immediate post-response
market quotes. Requirements are recorded in CAPTURE_REQUIREMENTS.md. That capture
has not been deployed or started.

## Verification

- Verified 2,016 original market payloads and retained 16,128 source metadata records.
- Matched 48,384 dollar-price and 48,384 quantity fields against raw payloads.
- Independently reconciled 588 evaluation price changes and model predictions,
  three small-edge trades, two delayed fills and three diagnostic probe markouts.
- Verified input, code and model hashes, disjoint fit/evaluation events, historical
  fitting cutoffs and post-signal request times.
- Passed 84 focused, research, collector and backtest tests, including future-data
  decision invariance, missing quotes, source-version ordering and fee arithmetic.

The initial timing export included an unused, misleading pair_valid field on probe
rows. The corrected `timing_v2` export removes it and clarifies provenance notes.
Models and all evaluation predictions exactly match the initial run. Both runs
are preserved; TIMING_OUTPUT_AUDIT.md explains the correction.

## Artifacts And Reproduction

The principal artifacts are `small_edges/REPORT.md`, `timing_v2/REPORT.md`, their
14-day ledgers, model locks, trade/probe records, and the root `verification.json`.
Both `PROTOCOL.md` and the availability clarification precede timing fitting.

From `next-gen`, verification and tests are reproducible without refitting:

```powershell
python -m scripts.verify_timing_small_edges
python -m unittest scripts.tests.test_timing_small_edges scripts.tests.test_focused_regime_experiment scripts.tests.test_profitability_research scripts.tests.test_research_data_integrity tests.test_collector_v3
python -m unittest discover -s backtest/tests -p 'test_*.py'
```

Runners refuse to overwrite their existing locks. To repeat calculations, pass
new sibling output directories to `scripts.investigate_small_edges` and
`scripts.investigate_forecast_timing`. Do not relabel any historical rerun as a
new forward test.

Sources: [NWS API documentation](https://www.weather.gov/documentation/services-web-api),
[Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf),
[fee rounding](https://docs.kalshi.com/getting_started/fee_rounding).
