# Rolling Walk-Forward v3 Result

**Status: ABSTAIN**

No policy met the stricter validation requirements.

## Probability Models

- `expanding_post_switch / raw_market`: log loss 0.5784, Brier 0.3175, top-one 76.0%, groups 3283
- `rolling_14 / raw_market`: log loss 0.5784, Brier 0.3175, top-one 76.0%, groups 3283
- `rolling_21 / raw_market`: log loss 0.5784, Brier 0.3175, top-one 76.0%, groups 3283
- `rolling_28 / raw_market`: log loss 0.5784, Brier 0.3175, top-one 76.0%, groups 3283
- `expanding_post_switch / market_weather_logit_c0.1`: log loss 1.0119, Brier 0.4897, top-one 76.1%, groups 3283
- `rolling_28 / market_weather_logit_c0.1`: log loss 1.0182, Brier 0.4928, top-one 76.1%, groups 3283
- `rolling_21 / market_weather_logit_c0.1`: log loss 1.0468, Brier 0.5071, top-one 76.0%, groups 3283
- `rolling_14 / market_weather_logit_c0.1`: log loss 1.1226, Brier 0.5454, top-one 76.1%, groups 3283
- `expanding_post_switch / weather_logit_c0.1`: log loss 1.7163, Brier 0.8095, top-one 21.7%, groups 3283
- `rolling_28 / weather_logit_c0.1`: log loss 1.7179, Brier 0.8100, top-one 21.7%, groups 3283
- `rolling_21 / weather_logit_c0.1`: log loss 1.7214, Brier 0.8110, top-one 21.7%, groups 3283
- `rolling_14 / weather_logit_c0.1`: log loss 1.7292, Brier 0.8135, top-one 21.7%, groups 3283

## Strategy

No policy met the stricter validation requirements; status is abstention.

## Limits

Historical rolling folds are development evidence. A final claim still requires a frozen future window.
These are historical rolling development results. A future frozen paper window is still required before any promotion.
