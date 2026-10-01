# Prospective Timing Experiment v1

This is a predeclared, observation-only replay, not a live paper-order log. No model
is fitted and no thresholds are selected using evaluation outcomes. The JSON file
is the numerical configuration; this document fixes interpretation. Both and the
research source code are hashed in LOCK.json before evaluation begins.

## Window And Availability

Signal receipts: September 15, 2026 00:00 UTC through September 29 00:00 UTC,
exclusive at the end: 14 full days. The scoring command is embargoed until October
2 00:00 UTC, allowing 72 hours for settlement labels. Data-quality audits remain
available throughout, but evaluation PnL is not produced early. The code cannot
prevent a human from inspecting raw data elsewhere; record deviations separately.

Use exact raw request/receipt times, never nominal snapshots as availability.
Only fully completed captures at the specified as-of cutoff enter an export.
Reject receipt time reversals, conflicting duplicates, inconsistent normalized
receipt metadata, malformed spools, and duplicate receipt IDs. Exact duplicate
archive/synced copies may be ignored. Canonical uncompressed body hashes identify
content; retain input-file hashes. The producer's gzip hashes include gzip header
timestamps and are NOT a stable forecast-version identifier.

## Signals And Controls

All six cities are retained. Extract the daily daytime high for the event's fixed
standard-time climate day, not a remaining-hour maximum or an observation floor.
Both daily and hourly forecasts must have valid publication metadata no later than
their receipt. No crossing event/date boundaries. Compare immediately adjacent
observations 240-420 seconds apart. Require nondecreasing updateTime/generatedAt;
a changed high requires a strictly newer daily updateTime. Both compared daily
versions must be at most 90 minutes old at forecast-ready time.

The first absolute revision of at least 1 F per city/event/date, observed at climate
hour 6 through 12 inclusive, is the sole revision intention. Consume that first
signal even when its quote or depth is unavailable; no replacement signal is
selected using later success. Preserve old/new values, versions and receipt IDs.

Retain all transition exclusions. The first valid unchanged-high observation per
city/event/date/integer climate hour is a control, including missing-entry controls.
For a revision's baseline, use prior control days from the preceding seven calendar
days in the SAME city and integer climate hour, with entry market-index distance
at most 1.0. A control's 15-minute outcome must already have been received BEFORE
the revision's signal time. Use equal weight across at least three distinct prior
dates. Controls are drawn from this predeclared evaluation stream, so the earliest
days naturally lack a baseline. This is a fixed sequential comparator, not a fitted
model or a retrospective match to future controls. Do not impute missing baselines.

## Entries And Horizons

Use only the signal cycle's quote, requested AFTER both forecast responses have
completed and received within 60 seconds of forecast readiness. Quote request
latency must be at most 15 seconds. Require six unique, contiguous brackets,
open/active status, pre-close receipts, bounded/noncrossed prices, and consistent
YES/NO complements. Preserve full market bodies and source IDs. Missing displayed
size is unknown, not unlimited liquidity and not zero-size evidence.

Market index is the normalized six-bracket YES-midpoint weighted index (0-5);
tails are indices, not invented temperatures. Outcomes are 5, 15 and 60 minutes
from entry receipt. For each horizon, use the earliest quote requested no earlier
than horizon minus 90 seconds and received no later than horizon plus 90 seconds,
with request after entry receipt and unchanged bracket universe. Report actual
elapsed time. No row-offset horizons, forward filling or replacement after the
first eligible quote has a changed market universe. The tolerance accommodates
five-minute sampling jitter; these are not precise tick-level measurements.

Primary statistic: mean revision-sign times (15-minute market-index change minus
the already-available control mean). Zero-change market persistence and raw signed
changes are descriptive references. The 5/60-minute outcomes are secondary and
cannot rescue a failing 15-minute test. Observational controls do not establish
causality or eliminate every market-state confounder.

## Hypothetical Economics

One fixed diagnostic NO-contract candidate per first revision: rank eligible
brackets by the reduction in Gaussian bracket mass from old to new forecast, using
sigma 2 F and half-degree boundaries. Require reduction >=0.01, NO ask $0.50-$0.85,
spread <=$0.05; ties break lexically by ticker. Rank without future prices, then
check the selected candidate's displayed ask size >=1. Missing size blocks economic
scoring; do not switch to a different bracket. Gaussian mass is not a calibrated
win probability, and this probe does not target an 80% hit rate.

Buy at ask plus $0.01; sell at later NO bid minus $0.01, floored at zero. Require
visible exit bid size >=1 for a short-horizon markout. Charge a conservative
one-contract taker fee ceil-to-cent(0.07 * multiplier * P * (1-P)) on both legs;
multiplier 1 is an assumption, with multiplier 2 stress. The general fee schedule
has finer rounding language; full-cent rounding here is deliberately conservative,
not a claim to reproduce account invoices. No maker fills or rebates are assumed.

Separate hold-to-settlement diagnostics require verified Kalshi settlement rows,
source raw ID, a winner in the entry's market universe, and settlement after entry
but no later than the fixed scoring deadline. NO wins when its ticker is not the
winner. Charge entry premium and fee; no settlement exit fee. Missing/conflicting
labels stay unresolved or fail validation, never become losses or wins by default.
One probe per city/date bounds notional exposure, but neither ledger is an actual
portfolio or evidence that a quoted order would have filled.

## Reporting And Promotion

Emit the sealed decisions separately from outcome attachment, input hashes, daily
14-day coverage and results (including zero-activity days), exclusions, price
pairing coverage, quote/depth availability, fees, cash debits, settlement wins,
net PnL, primary markouts, both weeks and leave-one-city-out mean markouts. Bootstrap
whole calendar-day blocks with seed 17 and 10,000 draws. City-days are not fully
independent, and only 14 day blocks limit confidence. Report missing outcome counts;
a positive complete-case mean with poor coverage cannot pass promotion.
Report an exact one-sided 95% binomial hit-rate lower bound as an IID reference,
not a guarantee under correlated city outcomes. The empirical day-bootstrap hit
interval can degenerate when every observed result wins or loses; it must not be
interpreted as certainty about the underlying hit probability.

Mechanism gate: >=20 first revision events across >=10 days and >=4 cities;
no city contributes >50% of events; cycle coverage >=98%, city-cycle capture
coverage >=95%, outcome coverage >=90%; >=20 revisions with matched prior controls;
positive lower endpoint of the two-sided 95% day-bootstrap interval for the primary
paired statistic. All input integrity checks must pass.

The additional economic gate needs >=20 depth-checked primary probe markouts,
>=90% candidate outcome coverage, positive lower 95% mean-markout bounds at both
fee multipliers, positive mean in both weeks, and positive mean after excluding
each city. Passing means only eligibility for further paper research. It does not
authorize trading, establish profitable settlement performance, or certify an
80% minimum hit rate. Insufficient sample/depth remains insufficient evidence;
do not lower gates after seeing outcomes.

Known preflight limitation: the initial production sample contains no displayed
market sizes. Economic promotion is blocked unless usable size evidence exists.
The collector is NOT changed by this research package. Any additional capture or
protocol change requires an explicit, separately recorded decision.

## References

- [NWS API documentation](https://www.weather.gov/documentation/services-web-api)
- [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf), reviewed
  September 14 UTC; fee multipliers/rounding remain explicit research assumptions.
