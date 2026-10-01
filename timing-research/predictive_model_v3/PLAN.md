# Rolling Walk-Forward v3

## Objective

Use nearly all available dated history without treating overlapping rows as
independent proof. The model layer produces out-of-sample probabilities for each
eligible test day after the first training window. The strategy layer consumes only
those saved out-of-sample probabilities.

## Model Families

- `raw_market`: normalized contemporaneous market midpoint probabilities.
- `weather_logit_c0.1`: regularized bracket classifier with weather features only.
- `market_weather_logit_c0.1`: regularized classifier with weather plus market
  logit.

All learned models are event-weighted so repeated intraday snapshots do not count
as independent settlements.

## Walk-Forward Folds

- rolling 14 days
- rolling 21 days
- rolling 28 days
- expanding post-switch, beginning August 14

For a target test day, training labels must have been settled before the first quote
request on that test day. Scalers, imputers and model coefficients are fit only on
the fold's training rows. Default learned models train on predeclared climate hours
10, 14 and 18 to avoid treating every repeated intraday quote as fresh independent
training signal.

## Evaluation

Probability metrics are reported before strategy PnL: event log loss, multiclass
Brier score, top-one accuracy and average winner probability. Trading policy
selection is a separate validation/test process that chooses the fold/model/threshold
combination on validation days only. The validation gates are stricter than v2,
including selected-trade calibration and day-bootstrap lower-bound requirements.

Historical outputs are development evidence only. A deployable claim still requires
a frozen future paper window with no threshold changes after seeing outcomes.
