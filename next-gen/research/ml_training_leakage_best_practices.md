# ML Training And Leakage Best Practices For The Weather Bot

Prepared for the Kalshi weather-market research pipeline.

Last updated: 2026-08-17

## Executive Summary

The project is already shaped like a serious research system: it exports frozen datasets, runs model reports, runs strategy reports, and tracks artifacts. The biggest remaining risk is not whether the code can train models. The biggest risk is believing a model is profitable because the training and backtest workflow accidentally gave it information it would not have had live.

For this project, leakage means any path where the model or strategy uses information from after the simulated trade decision. Examples include final temperatures, settlement winners, later market prices, future weather snapshots, future target dates, preprocessing fitted on all dates, or strategy gates tuned on the same dates used for the final PnL claim.

With roughly 258 labeled city-days in the current supervised set, the safest path is:

1. Treat `target_date` as the independent unit, not individual snapshot rows.
2. Train only on dates before the test dates.
3. Fit scalers, imputers, encoders, calibrators, and thresholds only inside each train fold.
4. Use small, regularized models before large neural nets.
5. Keep market-aware models strictly point-in-time: only market quotes available at the prediction snapshot can be used.
6. Separate weather prediction, probability calibration, strategy selection, and final test scoring.
7. Report uncertainty, not just the best result.

The practical target is not "find the best backtest." The target is "find a simple rule that keeps working as the date window moves forward."

## Current Pipeline Shape

Based on the repo structure and registry:

- `next-gen/backtest/` exports immutable Supabase facts, validates data, writes quality reports, and evaluates stored model outputs.
- `neuralcaster_v2` has:
  - `fixed_window`: explicit train and test date ranges.
  - `rolling_eval`: expanding or rolling walk-forward evaluation.
  - `mode=weather`: weather features only.
  - `mode=market`: weather features plus market-implied features.
- `edgecaster_v2` consumes a dataset plus a model report, then evaluates candidate trades with gates such as `min_predicted_reward`.
- Weather inputs include NWS, HRRR, NBM, ensemble, observation, timing, and source-disagreement features.
- Market inputs include quote-derived probabilities, spreads, market entropy, and market-implied expected high.
- Labels include final high temperatures and Kalshi settlement winners.

This design is workable, but it needs strict rules around what each stage is allowed to see.

## Plain-English Definitions

| Term | Plain meaning | Project example |
|---|---|---|
| Feature | Input the model can see | HRRR projected high at 08:00 UTC, market midpoint at 08:00 UTC |
| Label | Answer the model tries to predict | Final high temperature, winning contract |
| Snapshot | One observation at a time | City/event/weather/market state at a specific UTC hour |
| Target date | The weather day being predicted | `2026-07-17` for NYC max temp |
| Fold | One train/test split | Train July 2-16, test July 17-22 |
| Leakage | Information from the future sneaks into training/evaluation | Using July 22 settlement to pick a July 17 trading rule |
| Calibration | Whether predicted probabilities mean what they say | If model says 70%, it should win about 70% of the time |
| Backtest overfitting | A rule looks good only because many variants were tried | Trying 100 gates and reporting only the best one |

## Key Leakage Risks And How To Handle Them

### 1. Temporal Leakage

**What it means**

Temporal leakage happens when training uses future rows to predict earlier rows. Standard random cross-validation is unsafe for this project because rows from the same city/date/event are highly related across hours.

scikit-learn's `TimeSeriesSplit` documentation says ordinary CV is inappropriate for time-ordered data because it can train on future data and evaluate on past data. It also supports a `gap` parameter that excludes observations between train and test sets.

Source: [scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)

**How to incorporate it**

- Split by `target_date`, not by row.
- For each fold:
  - Train dates must be strictly before test dates.
  - Test dates should be full dates across all cities, not random rows.
  - A model report should record exact train/test target-date ranges.
- Rolling evaluation should produce out-of-sample predictions only.

**Pros**

- Most realistic simulation of live deployment.
- Prevents future-day information from contaminating earlier predictions.
- Makes model reports easier to compare.

**Cons / risks**

- Fewer folds with the current sample size.
- Metrics will look worse than leaky random splits.
- A few bad weather days can dominate a short test window.

**Recommendation for this repo**

Use fixed-window runs for "decision-quality" comparisons and rolling-eval for robustness checks. Do not use random train/test splits for any headline result.

### 2. Purging And Embargo

**What it means**

In financial ML, a label may cover an interval, not a point. If the label interval of a training row overlaps a test row, information can leak. Purging removes overlapping training examples. Embargo removes a small buffer after the test block to reduce serial-correlation leakage.

Financial ML Core describes purging as removing training observations whose labels overlap the test set, and embargo as eliminating a period after the test set. The `purgedcv` project explains that standard splits can leak when a financial label resolves after the bar it is assigned to.

Sources:

- [Financial ML Core: PurgedKFold](https://ppuertos.github.io/financial-ml-core/reference/model_selection/split/)
- [purgedcv README](https://github.com/landtml/purgedcv)

**How to incorporate it**

For this project, each prediction row has at least three important times:

- `snapshot_time_utc`: when the feature row is observed.
- `target_date`: the weather day being predicted.
- label availability time: when final high/settlement became known.

Implement a fold rule like:

- A training example is eligible only if its final label would have been known before the test decision time.
- If evaluating July 17 trades, do not use July 17 or later labels for model fitting, calibration, or strategy gate selection.
- Add at least a 1 target-day embargo for conservative strategy testing when using market features, because adjacent weather/market behavior is correlated.

**Pros**

- Stronger protection than simple chronological split.
- Especially useful for market-aware strategy tests.
- Makes "live-realistic" claims more defensible.

**Cons / risks**

- Costs data, which hurts with only about 258 labeled city-days.
- More complicated bookkeeping.
- Overly large embargo can make training sets too small.

**Recommendation for this repo**

Use a 1-day embargo for final strategy selection and no embargo for exploratory weather model diagnostics if the train/test split already uses clean target dates. Always record the embargo in `summary.json`.

### 3. Target Leakage

**What it means**

Target leakage means the answer, or something derived from the answer, is used as a feature. A recent PLOS One paper proposes a useful taxonomy: direct outcome encoding, execution-dependent metrics, and future information leakage. In this project, direct outcome encoding would be using final high, settlement winner, closing market price, or post-settlement fields as inputs.

Source: [PLOS One temporal leakage taxonomy](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0340167)

**How to incorporate it**

Create an explicit feature eligibility table:

| Field group | Allowed as feature? | Reason |
|---|---:|---|
| Weather forecast values at snapshot time | Yes | Available before decision |
| Observed high so far at snapshot time | Yes | Known so far |
| Final high temperature | No | Label only |
| Settlement winner | No | Label only |
| Closing midpoint after decision | No | Evaluation only |
| Future weather snapshots for same target date | No | Not known yet |
| Later market quotes for same contract | No | Not known at decision time |

**Pros**

- Eliminates the most dangerous class of inflated performance.
- Easy to enforce with tests.
- Makes model reports auditable.

**Cons / risks**

- Some useful signals must be represented only through valid historical aggregates.
- Requires discipline when adding features.

**Recommendation for this repo**

Every feature should have metadata:

- `available_at`: timestamp when the bot could know it.
- `source_table`: where it came from.
- `allowed_for_weather_model`: true/false.
- `allowed_for_strategy_model`: true/false.
- `leakage_class`: `safe`, `label_only`, `post_decision`, or `unknown`.

Unknown should fail closed.

### 4. Market Data Leakage

**What it means**

Market-aware models are not automatically leaky. They are only leaky if they use market information from after the prediction/trade decision. A quote at 08:00 UTC can be valid for an 08:00 decision. A closing quote from 23:00 UTC cannot be used to decide a morning trade.

Point-in-time backtesting sources emphasize that a backtest should only use information available at the simulated decision time.

Sources:

- [StockFit: point-in-time data for backtesting](https://developer.stockfit.io/blog/point-in-time-data-backtesting)
- [pfolio: look-ahead bias in backtesting](https://www.pfolio.io/academy/look-ahead-bias)

**How to incorporate it**

- For every strategy candidate, join model probabilities and market quotes at the same `snapshot_time_utc`.
- If a model report was produced at 08:00, Edgecaster should trade against 08:00 bid/ask, not later quotes.
- For market-aware Neuralcaster:
  - It may use market midpoint, spread, entropy, and market-implied expected high at or before the snapshot.
  - It must not use the eventual winner, closing quote, post-decision price movement, or settlement.

**Pros**

- Market prices are highly informative.
- Allows the model to learn when weather disagrees with the market.
- Helps estimate tradable edge instead of pure meteorology.

**Cons / risks**

- Easy to accidentally turn "market-aware" into "future-market-aware."
- Market features may crowd out real weather signal.
- Market data can make the model look good on accuracy while reducing profitable edge, because the market price already includes consensus information.

**Recommendation for this repo**

Maintain two model families:

- `weather_only`: no market features, used to measure independent forecast signal.
- `market_aware_point_in_time`: market features allowed only from the decision snapshot, used to measure tradable disagreement.

Never blend market probabilities into predictions for the same test data unless the blend weight was chosen on prior validation folds.

### 5. Preprocessing Leakage

**What it means**

Preprocessing leakage happens when scalers, imputers, encoders, feature selectors, or normalizers are fitted on all data before the split. scikit-learn explicitly warns to split first and never call `fit` or `fit_transform` on test data. It recommends `Pipeline` because the pipeline helps ensure transforms are fitted only on training data.

Source: [scikit-learn common pitfalls](https://scikit-learn.org/stable/common_pitfalls.html)

**How to incorporate it**

- The repo already uses scikit-learn `Pipeline` in several engines. Keep that pattern.
- Neuralcaster v2's normalizer should be fitted only on `train_examples` inside each fold. Based on the code, this is already the intended pattern.
- Feature selection and hyperparameter selection must also happen inside the training fold.

**Pros**

- Easy to enforce.
- Prevents subtle metric inflation.
- Makes saved reports reproducible.

**Cons / risks**

- The same model may produce slightly different transformed values per fold.
- Debugging fold-specific preprocessing can be more involved.

**Recommendation for this repo**

Add report fields:

- `preprocessor_fit_scope`: `train_fold_only`
- `feature_selector_fit_scope`: `train_fold_only`
- `normalizer_fit_scope`: `train_fold_only`

Any report that cannot prove this should be marked "research only."

### 6. Cross-Sectional Leakage

**What it means**

Cross-sectional leakage happens when the model learns from related samples that should not be independent. Here, many rows share the same city, event, target date, and settlement. If a split puts 08:00 NYC July 17 in train and 12:00 NYC July 17 in test, the test result is not independent.

**How to incorporate it**

- Treat `(city, target_date)` as the minimum independent label unit for weather modeling.
- Treat `(event_ticker, market_ticker, side, snapshot_time_utc)` as trade rows, but evaluate confidence by grouping back to city-day/event level.
- When testing city generalization, hold out entire cities, not rows.
- When testing date generalization, hold out entire dates across all cities.

**Pros**

- More honest estimate of generalization.
- Prevents repeated hourly snapshots from pretending to be hundreds of independent examples.

**Cons / risks**

- Metrics become noisier because the true sample size is smaller.
- City holdout is hard with only six cities.

**Recommendation for this repo**

Every report should show both:

- raw rows
- independent units: city-days, target dates, events, and trades

Do not trust a report that only advertises snapshot rows.

### 7. Probability Calibration

**What it means**

Calibration asks whether probabilities are meaningful. If the model says 80% on 100 comparable cases, about 80 should win. scikit-learn notes that calibration should be fitted on data independent from the data used to fit the classifier; otherwise the calibrator can be biased toward overconfident probabilities.

Source: [scikit-learn probability calibration](https://scikit-learn.org/stable/modules/calibration.html)

**How to incorporate it**

- For each rolling fold:
  - Train base model on train segment A.
  - Fit calibrator on validation segment B, still before the test segment.
  - Score only test segment C.
- Use calibration curves by:
  - model probability bucket
  - city
  - checkpoint
  - side
  - bracket type
- Prefer sigmoid/temperature scaling over isotonic calibration until there are far more labeled outcomes.

**Pros**

- Better probability quality helps strategy sizing and gates.
- Distinguishes "right direction" from "tradable confidence."

**Cons / risks**

- Calibration itself can overfit.
- Current sample size is small for many buckets.
- If buckets have fewer than about 20 independent outcomes, percentages are unstable.

**Recommendation for this repo**

Use simple global calibration first. Avoid city-specific or side-specific calibrators until each segment has enough independent examples. Report bucket counts beside every calibration chart.

### 8. Proper Scoring Rules

**What it means**

Accuracy and hit rate are not enough. A model can have a high hit rate and still lose money if it buys overpriced contracts. Probabilistic forecasting research recommends proper scoring rules because they reward honest probabilities. Log loss and Brier score are common examples.

Sources:

- [Gneiting and Raftery, Strictly Proper Scoring Rules](https://doi.org/10.1198/016214506000001437)
- [scikit-learn calibration docs](https://scikit-learn.org/stable/modules/calibration.html)
- [Weather and Forecasting: importance of proper scores](https://journals.ametsoc.org/view/journals/wefo/22/2/waf966_1.xml)

**How to incorporate it**

Weather model report:

- MAE/RMSE for expected high.
- Log loss and Brier for bracket distributions.
- Calibration error with bucket counts.

Strategy report:

- PnL and ROI.
- Hit rate.
- Expected value calibration by bucket.
- Profit factor.
- Drawdown.
- Number of independent target dates with trades.

**Pros**

- Encourages probabilities that can be traded.
- Helps identify overconfident losing models.

**Cons / risks**

- Log loss can punish rare confident misses heavily.
- Brier/log loss can improve while PnL worsens if prices are efficient.

**Recommendation for this repo**

Use log loss/Brier to select weather probability models. Use PnL only after the strategy gate has been chosen on prior validation folds.

### 9. Backtest Overfitting And Multiple Testing

**What it means**

If you try many model settings, date windows, gates, edge thresholds, and city filters, one configuration will look good by chance. Bailey, Borwein, Lopez de Prado, and Zhu warn that high simulated performance can be achieved after testing many alternatives, and the more alternatives tried, the higher the overfitting risk.

Source: [SSRN: Effects of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659)

**How to incorporate it**

- Track every experiment, not only winners.
- Save the parameter grid and number of attempted configurations.
- Choose thresholds on validation folds.
- Reserve the most recent period as a final untouched test.
- Report performance degradation from validation to test.

**Pros**

- Prevents false confidence.
- Makes profitable claims more credible.

**Cons / risks**

- Slower iteration.
- Some attractive charts will be demoted to "hypothesis only."

**Recommendation for this repo**

Add an `experiment_manifest.json` that records:

- every model run considered
- every strategy run considered
- which metric selected the winner
- which date window was untouched final test

If a gate was chosen after looking at test PnL, that result is exploratory only.

### 10. Small-Sample Pitfalls

**What it means**

The project has many rows, but the independent supervised sample is much smaller. With around 258 labeled city-days, the model can learn broad patterns, but it can easily memorize quirks of a few cities, weeks, or weather regimes.

**How to incorporate it**

- Count sample size by independent units:
  - target dates
  - city-days
  - events
  - contracts
  - trades
- Put confidence intervals or bootstrap bands around PnL and hit rate.
- Prefer simple models and strong regularization.
- Avoid per-city strategy gates unless supported by enough data.

**Pros**

- Keeps expectations realistic.
- Reduces the chance of overfitting to July/August weather.

**Cons / risks**

- May underfit real effects.
- Less exciting short-term backtests.

**Recommendation for this repo**

Until there are at least 90 settled days, model-selection should favor stability over peak PnL. Use city and side gates only if they survive rolling walk-forward tests.

## Model Classes: What They Are Good For Here

### Regularized Linear / Logistic Models

**How they work**

Linear models combine features using learned weights. Logistic regression maps those weights to probabilities. scikit-learn describes logistic regression as a linear classifier that models outcome probabilities with optional L1, L2, or ElasticNet regularization.

Source: [scikit-learn logistic regression](https://scikit-learn.org/stable/modules/linear_model.html#logistic-regression)

**Fit for this project**

Good for:

- Edge/no-edge classification.
- Profit/loss classification.
- Simple residual correction.
- Explaining which features matter.

Pros:

- Harder to overfit than a large network.
- Easy to debug.
- Works better with small samples.

Cons:

- Misses nonlinear interactions.
- May underuse rich weather-source features.

Recommendation:

Use as the baseline strategy model and as the first calibrator/gate model.

### Ridge / ElasticNet Residual Models

**How they work**

Ridge shrinks coefficients toward zero; ElasticNet can also set some coefficients close to zero. scikit-learn explains that larger Ridge `alpha` means more shrinkage and more robust coefficients under collinearity.

Source: [scikit-learn linear models](https://scikit-learn.org/stable/modules/linear_model.html)

**Fit for this project**

Good for:

- Correcting a weather-source blend.
- Learning small residual adjustments by checkpoint.
- Avoiding overfitting with many correlated weather sources.

Pros:

- Strong with limited data.
- Interpretable.
- Stable.

Cons:

- Weak on nonlinear source interactions.

Recommendation:

Keep Raycaster-style residual models as a benchmark and fallback.

### Random Forest

**How it works**

Random forest fits many decision trees on subsamples and averages them to improve accuracy and control overfitting.

Source: [scikit-learn RandomForestClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestClassifier.html)

**Fit for this project**

Good for:

- Exploratory feature importance.
- Nonlinear interactions.

Pros:

- Handles nonlinear relationships.
- Less tuning than boosting.

Cons:

- Probabilities can be poorly calibrated.
- Can overfit with many repeated snapshot rows.
- Less natural for extrapolating weather regimes.

Recommendation:

Use for diagnostics, not as the primary trading engine yet.

### Gradient Boosted Trees

**How they work**

Gradient boosting builds an additive model from trees, where each new tree tries to fix previous errors. XGBoost documentation emphasizes that max depth increases complexity and overfitting risk, while L1/L2 regularization and shrinkage make models more conservative.

Sources:

- [scikit-learn HistGradientBoostingClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html)
- [XGBoost parameters](https://xgboost.readthedocs.io/en/latest/parameter.html)

**Fit for this project**

Good for:

- Edgecaster reward modeling.
- Weather residuals after a source-blend baseline.
- Nonlinear source-disagreement patterns.

Pros:

- Strong tabular performance.
- Handles nonlinear interactions.
- HistGradientBoosting supports missing values.

Cons:

- Can overfit quickly with small independent sample size.
- Probability calibration still needed.
- Feature importance can be misleading with correlated weather sources.

Recommendation:

Use shallow boosted trees with high minimum leaf sizes and regularization. They are a good candidate for strategy edge modeling, but must be selected by rolling validation.

### Neural Networks / GRU Sequence Models

**How they work**

Neuralcaster v2 uses a GRU sequence encoder. A GRU reads hourly feature sequences and compresses them into a hidden state before predicting temperature distribution parameters. scikit-learn's neural-network documentation highlights common controls such as L2 regularization and early stopping; your PyTorch implementation also uses dropout, AdamW, patience, and weight decay.

Source: [scikit-learn MLPClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.neural_network.MLPClassifier.html)

**Fit for this project**

Good for:

- Learning how forecasts evolve over the day.
- Using sequences of hourly weather and market states.
- Producing smooth distribution forecasts.

Pros:

- Can learn temporal dynamics.
- Natural fit for hourly snapshots.
- Market-aware variant can detect weather/market disagreement over time.

Cons:

- High overfitting risk with only about 258 labeled city-days.
- Harder to explain.
- Hyperparameter search can create false winners.

Recommendation:

Keep Neuralcaster v2, but constrain it:

- 1 GRU layer.
- hidden size 16-48.
- dropout 0.1-0.3.
- weight decay 0.01 or higher.
- early stopping on a strictly earlier validation segment.
- compare against simple residual models every time.

## Recommended Split Policy For Current Data

Given the current data volume, use three layers:

### Layer 1: Research Rolling Evaluation

Purpose: understand whether the model works at all.

- Train window: 14 to 30 prior target dates.
- Test window: 1 day.
- Step: 1 day.
- Split unit: target date.
- Report: every fold, not just average.

Use this for model diagnostics, not final profitability claims.

### Layer 2: Strategy Selection Validation

Purpose: choose gates without touching the final test.

- Train model on earlier dates.
- Generate out-of-sample model predictions on validation dates.
- Tune Edgecaster gates on validation only.
- Candidate gates:
  - minimum predicted reward
  - minimum model-market edge
  - max spread
  - checkpoint window
  - side allowed
  - minimum calibration bucket count

Use this to pick one strategy policy.

### Layer 3: Final Untouched Test

Purpose: make a credible claim.

- Use the most recent 20% to 30% of target dates as final test, or at minimum the latest 10 settled target dates.
- No gate tuning on this period.
- Report all attempted configurations before final test.

If the final test fails, do not tune on it and call the retuned result final. Move the test boundary forward after more days arrive.

## Feature Eligibility Rules For This Bot

### Safe Features

Allowed if timestamped at or before `snapshot_time_utc`:

- NWS forecast/anchor high.
- HRRR projected high.
- NBM projected high.
- ensemble mean/median/stddev.
- observed high so far.
- latest observation temperature.
- warming rates up to the snapshot.
- hours since climate start.
- hours until climate end.
- market midpoint at snapshot.
- market bid/ask/spread at snapshot.
- market probability entropy at snapshot.

### Dangerous Features

Never allowed as model inputs for the same decision:

- final high temperature.
- settlement winner.
- `winner_ticker`.
- post-decision market prices.
- closing midpoint, unless used only as an evaluation metric.
- future weather snapshots from later hours.
- future target-date labels.
- any feature computed from all rows before splitting.

### Conditional Features

Allowed only if carefully timestamped:

- Historical city bias: allowed if computed only from prior settled dates.
- Historical model error: allowed if computed only from prior out-of-sample predictions.
- Historical source reliability: allowed if computed only from prior settled dates.
- Market movement features: allowed only using quotes before the decision snapshot.

## Strategy / Backtest Hygiene

### What A Clean Strategy Report Should Prove

Every strategy report should answer:

1. Which dataset was used?
2. Which model report was used?
3. Was the model report out-of-sample for the strategy test window?
4. What exact train/test date ranges were used?
5. Were gates chosen before the test window?
6. How many independent target dates had trades?
7. How many trades per city, side, bracket type, and checkpoint?
8. What was gross profit, gross loss, net PnL, hit rate, ROI, drawdown?
9. How much edge was predicted, and did higher predicted edge actually improve results?
10. Was the result robust if one city or one date is removed?

### Profitability Metrics To Prefer

Report these together:

- Net PnL.
- ROI.
- Hit rate.
- Average win.
- Average loss.
- Profit factor: gross profit / gross loss.
- Max drawdown.
- Number of trades.
- Number of independent traded days.
- PnL by city.
- PnL by side.
- PnL by checkpoint.
- PnL by edge bucket.
- PnL by market probability bucket.

Never judge strategy quality by hit rate alone. A 60% hit rate can lose money if losses are larger than wins or if the entry prices are too high.

## Concrete Recommendations For Current Pipeline

### Highest Priority

1. **Make `target_date` the official split unit.** Snapshot rows are repeated measurements, not independent examples.
2. **Add a feature eligibility manifest.** Every feature must declare when it becomes available.
3. **Fail closed on unknown feature availability.** If the pipeline cannot prove a feature is available at decision time, do not use it.
4. **Keep preprocessing inside each fold.** Scalers, imputers, encoders, normalizers, feature selectors, and calibrators fit only on training data.
5. **Separate gate selection from final testing.** Strategy gates picked on test PnL are not valid final results.

### Model Defaults Right Now

Weather engine:

- Baseline: source blend plus Ridge/ElasticNet residual correction.
- Neural: Neuralcaster v2 weather-only, 1 GRU layer, hidden size 24-48, dropout 0.15-0.25, weight decay 0.01.
- Market-aware: use only point-in-time quote features and compare against weather-only.

Strategy engine:

- Start with simple deterministic edge rule:
  - trade only when model probability minus ask-implied probability clears a threshold.
  - require max spread.
  - require calibration bucket count.
  - cap trades per city-day.
- Then compare against Edgecaster reward model with shallow HistGradientBoosting or Ridge.

### Suggested Current Experiment

Use latest settled export:

1. Weather model rolling eval:
   - `training_policy=rolling`
   - `train_days=21`
   - `test_days=1`
   - compare `mode=weather` vs `mode=market`
2. Strategy fixed-window:
   - Train/gate selection: earliest 70% of settled dates with model out-of-sample predictions.
   - Final test: latest 30% of settled dates.
3. Gate grid:
   - `min_edge`: 0.05, 0.08, 0.10, 0.12, 0.15
   - `max_spread`: 0.03, 0.05, 0.08
   - checkpoints: morning only, midday only, all
   - sides: both, yes-only, no-only
4. Select one gate by validation profit factor plus minimum trade count.
5. Score it once on final test.

## Red Flags In Reports

Treat a report as suspect if:

- It has high trade count but low independent traded days.
- It shows test PnL but no train/test ranges.
- It says "market-aware" but does not list quote snapshot times.
- It has a great city-specific result with fewer than 20 city-days.
- It uses a model report generated from the same test dates as the strategy gate selection.
- It reports 100% hit rate in high edge buckets with very few trades.
- It selected the best threshold after viewing final test PnL.
- It has missing row counts, unknown model mode, or unknown source association.

## Repo-Specific Implementation Ideas

These are recommendations, not code changes in this report.

### Add `feature_availability.json`

Example:

```json
{
  "weather_snapshots.hrrr_projected_high_f": {
    "available_at": "snapshot_time_utc",
    "allowed": ["weather_model", "strategy_model"],
    "leakage_class": "safe"
  },
  "final_temperature_labels.final_high_f": {
    "available_at": "label_settled_at_utc",
    "allowed": ["evaluation"],
    "leakage_class": "label_only"
  }
}
```

### Add Report Hygiene Fields

Every `summary.json` should include:

```json
{
  "split_unit": "target_date",
  "train_start_date": "YYYY-MM-DD",
  "train_end_date": "YYYY-MM-DD",
  "test_start_date": "YYYY-MM-DD",
  "test_end_date": "YYYY-MM-DD",
  "embargo_days": 1,
  "feature_availability_policy": "point_in_time",
  "preprocessing_fit_scope": "train_fold_only",
  "gate_selection_scope": "validation_only",
  "independent_city_days": 0,
  "independent_target_dates": 0
}
```

### Add One-Click Leakage Audit

The Workbench should show:

- Model uses only allowed feature groups.
- Train dates are before test dates.
- Model report covers strategy test dates.
- Model predictions used by Edgecaster are out-of-sample.
- Gate selected on validation, not final test.
- Final labels are used only for scoring.

## Final Position

The strongest near-term research design is not a bigger neural network. It is a stricter evaluation harness.

Use simple, regularized weather/residual models as baselines. Use Neuralcaster v2 only when it beats those baselines out-of-sample across rolling dates. Use market-aware features only when the timestamp proves the quote existed before the trade. Use Edgecaster only after model predictions are out-of-sample and gates are selected on a validation period.

The project can make real progress with the current data, but the claims should be framed as "promising paper-trading signal under leakage-controlled rolling validation" until more settled days accumulate.

## Source List

- scikit-learn, Common pitfalls and data leakage: https://scikit-learn.org/stable/common_pitfalls.html
- scikit-learn, TimeSeriesSplit: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
- scikit-learn, Probability calibration: https://scikit-learn.org/stable/modules/calibration.html
- scikit-learn, Logistic regression: https://scikit-learn.org/stable/modules/linear_model.html#logistic-regression
- scikit-learn, RandomForestClassifier: https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestClassifier.html
- scikit-learn, HistGradientBoostingClassifier: https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html
- scikit-learn, MLPClassifier: https://scikit-learn.org/stable/modules/generated/sklearn.neural_network.MLPClassifier.html
- XGBoost parameters and regularization controls: https://xgboost.readthedocs.io/en/latest/parameter.html
- Financial ML Core, PurgedKFold and embargo: https://ppuertos.github.io/financial-ml-core/reference/model_selection/split/
- purgedcv, combinatorial purged cross-validation: https://github.com/landtml/purgedcv
- Mishra et al., temporal leakage taxonomy, PLOS One: https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0340167
- Gneiting and Raftery, strictly proper scoring rules: https://doi.org/10.1198/016214506000001437
- Brocker and Smith, proper scoring for weather forecasts: https://journals.ametsoc.org/view/journals/wefo/22/2/waf966_1.xml
- Bailey, Borwein, Lopez de Prado, and Zhu, backtest overfitting: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659
- StockFit, point-in-time backtesting: https://developer.stockfit.io/blog/point-in-time-data-backtesting
- pfolio, look-ahead bias in backtesting: https://www.pfolio.io/academy/look-ahead-bias
