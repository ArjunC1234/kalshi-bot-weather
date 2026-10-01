# Profitability Investigation

Recorded before the new experiments on 2026-09-10.

## Objective

Find a reproducible strategy with positive profit after taker fees and execution costs while retaining the user's 80% hit-rate objective. Neither property is assumed achievable. Report a complete 14-day window including no-trade days and failed candidates.

## Investigation And Execution

1. Audit joins, settlement validity, label availability, feature timing, quote timing, order selection, sizing, and costs. Reproduce the previous frozen-model result, then change execution timing alone to isolate that effect.
2. Repair the retrospective best-edge selection. Decisions must proceed chronologically, choosing among only the quotes available at the current timestamp. Missing settlements must never become automatic NO wins. Add behavioral regression tests.
3. Evaluate a limited, explicitly enumerated candidate set: the existing frozen weather model, market-shrunk weather probabilities, and a strongly regularized market-relative classifier using contemporaneous weather and quote features. Include a market-only comparator. Train against actual Kalshi winner tickers, without substituting temperature labels as trade outcomes.
4. Use July 15 through August 3 for fitting the candidate calibration/classifier; August 4 through August 17 for chronological selection. Freeze all model parameters and policy settings before scoring August 18 through August 31. Those August results are retrospective because this period was already inspected in prior work.
5. If September observations are available, fetch them without inspecting outcomes, lock the selected candidate, and score the available September dates without refitting. Report the exact coverage; do not call fewer than 14 days a 14-day fresh test.
6. Charge the published general taker formula with multiplier 1 as an explicit assumption, conservatively rounding up to cents per order. Include one-cent adverse execution in the selection objective and zero/two-cent sensitivity tests. Do not assume maker fills or visible liquidity absent from the export.
7. Select using validation performance only, requiring at least 20 trades, observed hit rate at least 80%, positive net profit in both validation weeks, and positive aggregate net profit. If none qualify, report that failure and the best exploratory candidate separately; the operational policy remains abstention.
8. Report trade-level and daily ledgers, calibration, city/side breakdowns, same-policy timing comparisons, and day-block bootstrap uncertainty. Confidence intervals are descriptive and do not remove repeated-search bias.
9. Save a frozen research artifact and a replay/scoring command usable on later exports. Promotion requires a fresh prospective test and verification of settlement rules and executable liquidity.

## Known Issues At Start

- Previous hit80 code selects the best estimated edge over the whole event day, introducing quote lookahead.
- Previous code treats a missing settlement winner as a NO win.
- Frozen versus rolling model comparisons change both fitted models and selected trades; their profit difference does not isolate leakage.
- The local lightweight export ends August 31. Snapshot counts greatly exceed independent city-days.
- The lightweight export omits actual collection/ingestion timestamps and order-book depth. Snapshot-time replay cannot prove fillability or full historical availability.
- Many prior experiments inspected August. New positive August results are research evidence, not an untouched confirmation.

## Fee References

- https://kalshi.com/docs/kalshi-fee-schedule.pdf (effective July 7, 2026)
- https://docs.kalshi.com/getting_started/fee_rounding
