# Timing And Smaller Edges

Recorded September 13, 2026 before new performance calculations. All dates through
September 10 have been examined before. This is retrospective mechanism testing,
not a fresh holdout, and does not enable live orders or an unattended paper test.

## A. Smaller Edges

Keep the focused experiment run_v2 model lock, data and afternoon snapshots fixed.
Use its calibration-selected mixed_weather model, plus raw market as a control.
The primary comparison lowers minimum net edge from $0.05 to $0.01. Also report
$0.00 and $0.02 as descriptive sensitivity, never select the best threshold by
evaluation profit. Keep NO side, ask $0.50-$0.85, minimum predicted win 85%, spread
<=$0.05, $3 all-in order cap, $40 daily budget and verified integer depth unchanged.

Evaluate August 28-September 10, all 14 days. Include conservative per-order cent
taker fees (multiplier 1), one-cent adverse execution, zero/two-cent sensitivity,
and a next-available-quote limit-price proxy. Freeze intentions before checking
fills; no replacement trade if an intention fails. Reject incomplete, crossed,
unverified, post-close or more-than-75-minute-delayed execution quotes.

Report trades, wins, actual cash debit, fees, net PnL, each week, leave-one-day-out
PnL, day-block bootstrap intervals and an exact one-sided 95% hit-rate lower bound
(the latter assumes independent trades; not a guarantee with correlated outcomes).
Twenty trades, observed >=80% wins, positive each-week net PnL and a positive lower
day-bootstrap PnL bound are necessary to consider a new forward test. Passing on
these reused dates would remain a provisional hypothesis, not confirmation.

## B. Hour-Scale Forecast Timing

Use post-switch dates only. Fit August 14-27 using information and price-change
labels available before August 28 00:00 UTC. Evaluate August 28-September 10.
Verify all collected climate-hour 4-15 snapshots without price/outcome filtering.
Preserve source receipt metadata in the new derivative exports.

Signal: change in NWS daily daytime high from the immediately preceding hourly
snapshot of the same event, only with a strictly newer NWS updateTime and with
both published timestamps no later than their actual source receipt times.
Use source receipt times, not the nominal hourly snapshot, to date availability.
Require adjacent observations within 45-75 minutes. A material revision is at
least 1 F, first observed at climate-hour 6-12 inclusive. No crossing target days.
Keep the old/current forecast values, versions, publication and receipt times.
Do not interpret remaining-window maxima or observation floors as revisions.

Entry reference: first complete active market quote received after signal receipt,
within 75 minutes. Exit reference: the next collected complete market quote with
45-75 minutes elapsed. Use the same bracket tickers throughout. Market quote
validation and existence are separate from the outcome of subsequent price moves.
Historical hourly collection cannot establish a sub-hour latency advantage.

Primary mechanism test: predict the next quote's change in market-implied bracket
index, using normalized six-bracket YES midpoints. Avoid arbitrary tail temperature
values. Compare a Ridge(alpha=1) market baseline (current bracket-index mean,
entropy, previous observed market-index change, climate hour) with the identical
baseline plus clipped NWS revision in F. Scale features on fitting data only;
weight each event equally despite repeated hourly transitions. No hyperparameter
search or evaluation-based model choice. Save fitted models before evaluation.

Score paired squared-error differences on identical complete evaluation transitions;
also report the first material revision per event, signed subsequent price change,
and the price change already occurring between the previous quote and entry. Use
whole-day bootstrap intervals, and a descriptive within-day forecast-sign
randomization check. Contemporaneous association is not a causal lead.

A fixed diagnostic NO-contract probe accompanies the first material revision per
event: at the first post-signal quote, choose the largest reduction in Gaussian
bracket mass caused by that revision (sigma fixed at 2 F), among ask $0.50-$0.85,
spread <=$0.05 and visible depth >=1. Require positive mass reduction >=0.01.
Choice is made before examining the later quote. Report one-contract next-quote
bid minus entry ask, fees on both legs and one-cent adverse execution on each leg.
Do not sum overlapping probes into portfolio profit or call Gaussian mass an
80%-calibrated win estimate. Missing exits remain unscored, never successes.

Future-quote availability is used only to score transitions/probes, not to decide
which first material revision or which probe to select. Report missing pairs and
coverage. Timing would merit further model development only with at least 20
evaluation revision events, improvement over the market baseline whose paired
day-bootstrap 95% upper bound is below zero, and positive mean cost-adjusted probe
markout whose day-bootstrap 95% lower bound is above zero. This is a mechanism
gate, not authorization to trade and not proof of the requested 80% minimum.

## Verification And Limits

Test invariance of decisions under changed future outcomes/quotes, no future
forecast versions, no cross-day differencing, fit/evaluation separation, snapshot
cadence limits, whole-contract fees and delayed fills. Preserve prior artifacts.

Source freshness, stale displayed prices, selection effects, few independent days
and shared city weather remain limitations. Hourly snapshots miss the immediate
release-to-market path. A negative hour-scale result cannot exclude faster effects.

Sources: https://www.weather.gov/documentation/services-web-api,
https://kalshi.com/docs/kalshi-fee-schedule.pdf,
https://docs.kalshi.com/getting_started/fee_rounding.
