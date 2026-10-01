# September 18 Failure Analysis

## Conclusion

The project did not produce a model that met the frozen reliability and profitability
requirements. The result is a failure, not a near-pass. It does not prove that
weather-market trading is impossible. It does show that the currently available
signals, outcome alignment, sample size, and executable quotes do not support the
requested claim.

No model was deployed and no live orders were placed.

## What Was Tested

Two deliberately different approaches were preserved:

1. `monotone-barrier-v1` bought NO only after an official observed high had exceeded
   a bounded bracket's upper limit. This was intended to trade a physical certainty
   rather than a forecast.
2. `causal-bracket-logit-v2` estimated bracket probabilities from NWS, HRRR, NBM,
   ensemble, observed-temperature, city, climate-hour, and optionally market-price
   inputs. Four regularized model variants and 648 trading policies per model were
   evaluated on the selection interval. Test outcomes were attached only after a
   model-policy pair was fixed.

Both used raw payload receipt times. A weather row was admitted only after all of its
referenced successful source payloads were received, and only a later market request
could use it. Execution required one displayed contract at the ask, one cent of
adverse price movement, and a full-cent taker fee. A doubled-fee scenario was also
required to remain profitable.

## Frozen Predictive Result

The selected pair was:

- Model: `weather_logit_c0.1`
- Policy: `no_p0.80_e0.05_px0.50-0.80_s0.05_h10`
- Fit interval: August 14-26
- Selection interval: August 28-September 3
- Test interval: September 4-17

Selection performance:

- 12 trades
- 9 wins and 3 losses
- 75.0% hit rate
- $0.80 base-fee PnL
- $0.63 doubled-fee PnL
- Negative whole-day bootstrap lower bound

Test performance:

- 20 trades
- 12 wins and 8 losses
- 60.0% hit rate
- 39.4% exact one-sided 95% hit-rate lower bound
- -$1.05 base-fee PnL
- -$1.39 doubled-fee PnL
- -$5.39 whole-calendar-day bootstrap 95% lower PnL
- First seven days: -$0.16
- Second seven days: -$0.89
- 12 active days and all 6 cities represented
- Zero recorded feature-availability violations
- Zero input-integrity errors among admitted rows

The test also lacked one of the expected six September 16 settlements. All 20 actual
intentions were labelled, but the complete six-city test panel was not available, so
the full-window completeness requirement failed independently of PnL.

## Why It Failed

### 1. The predictive probabilities were badly overconfident

The model's average predicted success probability was 81.96%, but only 60.0% of the
trades won. The calibration gap was 21.96 percentage points. The selection interval
already showed warning signs: its predicted probability averaged 83.49%, while its
observed hit rate was 75.0% on only 12 trades.

The average test execution price plus fee was 65.25%. The realized 60.0% hit rate was
below that break-even level, which directly explains the loss. Better thresholding
cannot repair a probability model whose high-confidence scores are not calibrated.

### 2. The apparent data volume overstates independent information

The export contains tens of thousands of contract snapshots, but hourly rows from
the same city-day share one final outcome. They are not independent training labels.
The contract settlement source changed to Weather Company on August 14, so older NWS
regime outcomes were excluded from v2 fitting. The fit interval therefore contained
roughly 78 independent city-day events, not tens of thousands of independent cases.

That is a small sample for estimating city effects, time effects, forecast-source
bias, tail probabilities, and calibration simultaneously. Repeated snapshots help
describe how information changes during a day, but they cannot manufacture new
independent final temperatures.

### 3. The features and settlement target are not ideally aligned

After August 14 the contracts referenced Weather Company results, while the model's
weather features came from NWS and Open-Meteo sources. Those sources are correlated,
but station choice, observation handling, and daily-high conventions can differ.
The model was therefore learning a cross-provider mapping from a short post-change
history. That is a plausible explanation for the calibration failure, but this run
does not isolate it as the sole cause.

### 4. The deterministic edge was not executable

The monotone-barrier model found that by the time a prior official observation could
be used without receipt-time leakage, eliminated brackets were generally quoted at a
$1.00 NO ask with zero displayed ask depth. The physical inference was correct, but
the economic opportunity was already gone. Loosening depth or price requirements
would create fictional fills, not profit.

### 5. The selection evidence was weak

Only six grid entries passed selection, and they represented the same effective trade
sequence under inactive side and price-ceiling differences. The chosen sequence had
12 trades, a 75% hit rate, and a negative bootstrap lower bound. It passed the minimum
selection gates, but it did not provide strong evidence of a stable edge. The test
then rejected it.

### 6. Failure was not confined to one isolated day

Both seven-day halves lost money. New York accounted for -$1.34, but removing most
other cities still left total PnL negative. This makes a single-city data correction
an insufficient explanation. There were profitable dates and cities, but no broad,
stable pattern that passed the frozen robustness checks.

### 7. Execution costs matter, but they were not the primary failure

Base fees totaled $0.40. Gross PnL before fees was already -$0.65, so eliminating
fees would not have made the test profitable. Fees and one-cent adverse execution
worsened the result, but the main issue was prediction error.

## Leakage Assessment

No recorded availability violation was found among the admitted v2 trades. The test
suite verifies that same-cycle weather cannot be used by an earlier market request,
labels are absent from model features, the fitted model cannot trade before its last
training label was available, and later quotes cannot replace an earlier intention.

This is evidence that the implemented receipt-time boundary worked. It is not a
proof that every upstream timestamp or provider semantic is perfect. September 4-10
had also been examined in prior research, so the chronological September 4-17 replay
was not a pristine human-independent confirmation. That limitation was declared
before the v2 test and is another reason not to promote it.

## Is The Idea Impossible?

No universal impossibility has been established. Prediction markets can be
temporarily mispriced, weather forecasts can add information, and faster observation
delivery can create short-lived opportunities.

The narrower conclusion is less favorable: a profitable strategy with an 80% hit
rate has not been demonstrated using the current six-city data, current weather
features, hourly historical cadence, observed depth, and conservative costs. The
deterministic opportunity was unfillable, and the best frozen predictive candidate
was materially miscalibrated out of sample.

An 80% hit rate is also not equivalent to profitability. Buying highly priced
contracts can produce many wins and still lose after occasional losses and fees.
Conversely, a profitable lower-hit-rate strategy could exist but would not satisfy
the requested reliability constraint.

## What Would Change The Evidence

The next defensible experiment should not tune against the exposed September 4-17
outcomes. It should:

1. Collect the forecast and observation source that actually determines settlement,
   or explicitly model cross-provider station and rounding differences.
2. Accumulate at least several additional weeks of five-minute, depth-bearing quotes.
   The current high-cadence archive is only a few days long.
3. Record the precise receipt time for every feature and preserve the first actionable
   quote after that receipt.
4. Use event-weighted, nested walk-forward calibration. Policy selection should have
   a positive day-bootstrap lower bound, not merely positive point PnL.
5. Freeze a new model before a wholly future test window. Do not reuse September 4-17
   for model or threshold selection.
6. Paper-execute the frozen intentions and compare requested price, displayed depth,
   actual fill, and settlement. Historical top-of-book replay cannot prove fills.
7. Require performance across multiple non-overlapping windows or seasons before
   considering live capital.

If target-aligned data and verified paper fills repeatedly fail those prospective
tests, the practical conclusion should be that this market is not tradeable with the
available information and latency. That would still be a statement about this system
and access level, not a mathematical claim that no participant can ever have an edge.

## Reproducibility

The frozen requirements, complete selection grid, serialized selected model, trades,
software versions, and SHA-256 input/code hashes are in `predictive_model_v2/results`.
All 42 repository tests and Ruff checks passed after the final audit.
