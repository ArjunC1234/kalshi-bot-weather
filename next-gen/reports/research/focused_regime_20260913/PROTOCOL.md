# Focused Settlement-Regime Experiment

Recorded September 13, 2026 before fitting this experiment. Previously examined
history is development evidence, not an untouched test. No live orders.

## Question And Comparisons

Does a small weather correction improve market probabilities on identical events?
Compare raw market probabilities and a 2 x 2 design:

- Mixed history: August 4-23, including both settlement-rule regimes.
- Current regime: August 14-23, Weather Company rules only.
- For each history, market-only versus market plus weather.

The history comparison changes recency, regime composition and sample size. It
does not by itself isolate the causal effect of the settlement-rule change.
July is excluded because receipt verification in the existing research export
starts August 4. Missing collection days remain missing, not synthetic samples.

## Timing And Observations

- Base fit: target dates through August 23; labels received before August 24 18:00 UTC.
- Separate calibration: August 24-27; labels received before August 28 18:00 UTC.
- Retrospective evaluation: August 28-September 10, exactly 14 calendar days.
- One snapshot per event: earliest complete, active, receipt-verified snapshot
  at climate-hour 14 or later, before hour 20 and before climate-day close.
- Choose snapshots without looking at outcomes, model probabilities or trade success.
  Require all six brackets, finite noncrossed dollar quotes, and at least two
  available forecast anchors. This common universe is used for every model.
- All bracket probabilities at a snapshot sum to one. Compute market probabilities
  from normalized YES bid/ask midpoints. Score complete settled events equally.
- Source receipts must precede decisions, and fitted/calibrated models must be
  available before the predictions evaluated. Missing labels never become wins.

## Fixed Models

Forecast anchor: equal mean of NWS, HRRR, NBM and ensemble median when present.
Estimate pooled forecast bias, with city deviations shrunk by n/(n+20). Estimate
residual RMS uncertainty from the fit data with a 1.5 F floor. Gaussian bracket
mass uses half-degree rounding boundaries. Observations are not a hard floor:
their source need not match settlement. These are deliberately simple assumptions.

Use market log probabilities as an offset. Fit a market sharpness correction a
and, for weather models only, coefficient b on log(weather_p)-log(market_p).
Use SciPy bounded optimization of mean event multinomial log loss plus
0.5*(a^2+b^2), with a in [-0.5,0.5] and b in [0,0.5]. No parameter sweep.
Fit a separate inverse-temperature calibration scalar c on the later calibration
events, bounded [0.5,1.5], penalized by 0.5*(c-1)^2. Report before and after
calibration. Never fit calibration on the evaluation labels.

## Fixed Trading Policy And Decision

One NO position at most per event, quoted ask $0.50-$0.85, predicted win >=85%,
spread <=$0.05 and edge >=$0.05 after fees and one-cent adverse execution.
At the single selected snapshot only; no later retries or policy sweep.
Maximum all-in order $3, daily cash budget $40, integer contracts capped by
verified visible depth. General taker multiplier 1; fees rounded up per order.
Also report zero/two-cent slippage and next-quote limit-fill proxy without tuning.

Primary comparison: paired event log-loss difference weather minus corresponding
market-only model. Also multiclass Brier score, reliability bins, city/source
forecast error and 90% temperature interval coverage, and day-block bootstrap
intervals. Positive improvement is not inferred from win rate alone.

Record a provisional preferred challenger using calibration-period log loss only,
then serialize and hash all models before evaluation. A challenger can qualify
for a forward paper test only if, in development evaluation, its log loss beats
both raw market and its corresponding market-only model, its paired day-bootstrap
95% upper bound versus that market-only model is below zero, and its fixed policy
has >=20 trades, >=80% observed wins, positive net PnL in total and in each week.
Failure means no trading candidate is promoted; do not loosen the criteria.

If a challenger qualifies, freeze its artifacts for a new 14-day forward paper
test starting only after activation and actual decision logging. Historical
replays do not count as forward observations. If none qualifies, save an inactive
forward-test specification rather than pretend an unattended test is running.
No claim of a true minimum 80% hit rate follows from a small observed sample.

## Sources And Limits

- https://scikit-learn.org/stable/modules/calibration.html
- https://kalshi.com/docs/kalshi-fee-schedule.pdf
- https://docs.kalshi.com/getting_started/fee_rounding

The calibration window has only four days. Day-block bootstrap intervals are
descriptive, do not correct earlier research selection, and cannot establish
future profitability. The single-afternoon snapshot deliberately tests a narrower
strategy than the earlier all-day model. Quotes collected before the last weather
response may no longer fill; the delayed proxy cannot establish real execution.
