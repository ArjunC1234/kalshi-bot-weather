# Monotone Barrier Model v1

The objective is maximum conservative net profit subject to reliability, not maximum
backtest PnL. “Success” requires every frozen test requirement in REQUIREMENTS.json.
An incomplete 14-day test, a profitable subset, or an 80% point estimate alone is
not success.

## Why this model

Daily maximum temperature is monotone: after an official station observation exceeds
a contract’s upper bound, that YES contract cannot win unless the observation stream
and settlement source disagree. The model buys NO only on those eliminated brackets.
It does not predict tomorrow’s weather, fit a high-capacity learner, or infer an edge
from the eventual settlement. Its learned component is only policy selection among a
small grid of gap, price, spread, age, and timing limits.

This is more defensible than another neural model here because prior flexible models
were unstable after leakage removal, while the timing stream currently supplies only
several prospective days. The cost is that a competent market may leave no profitable
quotes. That outcome is evidence, not a reason to weaken the requirements.

## Information boundary

- Feature: latest `observed_high_so_far_f` whose NWS observation payload was received
  before the market request. Same city and event only.
- Entry: first subsequent complete six-bracket market quote satisfying the frozen
  policy. The quote must be requested after the observation receipt and before close.
- Eligibility: finite upper bracket bound and observed high minus upper bound at least
  the selected integer gap. Prices must be bounded and uncrossed; displayed NO ask
  depth must be at least one contract.
- Selection among simultaneously eliminated contracts uses the lowest executable NO
  ask, then ticker. One intention per event; later quotes cannot replace it.
- Cost: one contract at NO ask plus one cent adverse execution, general taker fee
  rounded up to a cent. Multiplier two is a stress test. No maker assumptions.
- Outcome: only verified Kalshi settlements. Missing labels remain unscored. A
  contradiction means observed high exceeded the winning bracket’s upper bound.

## Splits

- Development: July 1-August 31. Used for implementation and diagnostics.
- Policy selection: September 1-12. All grid policies are evaluated, then the passing
  policy with highest fee-stress PnL is selected; ties prefer wider gap, lower ask,
  lower spread, younger observations, earlier entry, then lexical policy ID.
- Prospective test: September 13-26, fixed before this build. September 13-17 is an
  interim slice available today; September 18-26 is not yet fully settled. No policy
  change may use prospective results.

The selection period has been studied in earlier work, so it is not independent
confirmation. Only the complete prospective window can satisfy success requirements.

## Required report

Save the selected policy, every intention and exclusion, source receipt/quote times,
prices, depth, fees, labels, daily PnL, exact one-sided hit-rate bound, whole-day
bootstrap interval, each seven-day half, city concentration, and leave-one-city-out
PnL. Report base and doubled fees. The report must clearly distinguish `PASS`,
`FAIL`, and `PENDING` due to unavailable future dates.

No live order placement, deployment, or automatic promotion is part of this model.
