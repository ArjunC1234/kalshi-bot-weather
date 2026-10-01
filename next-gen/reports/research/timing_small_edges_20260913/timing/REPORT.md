# Hour-Scale Forecast Timing

Versioned NWS daily-high revisions, post-switch data only.
Fit August 14-27; retrospective evaluation August 28-September 10.
Entry quotes were requested after receipt of the forecast. Hourly snapshots cannot establish faster effects.

Evaluation: 588 complete hourly transitions across 84 city-days; 9 scored first material revisions.

```json
{
  "training_events": 84,
  "training_transitions": 588,
  "evaluation_events": 84,
  "evaluation_transitions": 588,
  "first_material_revisions": 9,
  "scored_first_revisions": 9,
  "market_mse": {
    "rows": 588,
    "events": 84,
    "days": 14,
    "mean": 0.043343657065136945,
    "day_bootstrap_95_interval": [
      0.03555572725642497,
      0.0523737580081619
    ]
  },
  "market_weather_mse": {
    "rows": 588,
    "events": 84,
    "days": 14,
    "mean": 0.04340395108231887,
    "day_bootstrap_95_interval": [
      0.03567916783973077,
      0.05242635541895437
    ]
  },
  "paired_error_all": {
    "rows": 588,
    "events": 84,
    "days": 14,
    "mean": 6.029401718192759e-05,
    "day_bootstrap_95_interval": [
      -2.355316905272217e-05,
      0.00019604170749887604
    ]
  },
  "paired_error_first_revisions": {
    "rows": 9,
    "events": 9,
    "days": 6,
    "mean": 0.004306437430589757,
    "day_bootstrap_95_interval": [
      -0.0018833971602505762,
      0.011077342256130317
    ]
  },
  "signed_later_change": {
    "rows": 9,
    "events": 9,
    "days": 6,
    "mean": -0.0010599612382287127,
    "day_bootstrap_95_interval": [
      -0.11241457327276017,
      0.09016894082050067
    ]
  },
  "signed_already_moved": {
    "rows": 9,
    "events": 9,
    "days": 6,
    "mean": 0.07592738447394594,
    "day_bootstrap_95_interval": [
      -0.016335414496779693,
      0.16668632965412797
    ]
  },
  "sign_randomization": {
    "observed_mean_signed_change": -0.0010599612382287127,
    "within_day_sign_permutation_one_sided_p": 1.0,
    "note": "Descriptive only; sign exchangeability within days is not guaranteed."
  },
  "net_probe_markout": {
    "rows": 3,
    "events": 3,
    "days": 3,
    "mean": -0.02000000000000002,
    "day_bootstrap_95_interval": [
      -0.05000000000000001,
      0.029999999999999954
    ]
  },
  "probe_intentions": 3,
  "missing_probe_exits": 0,
  "median_forecast_publication_age_minutes": 48.45545006666667,
  "median_wait_for_entry_minutes": 60.172014716666666,
  "coverage": {
    "candidate_hourly_transitions": 1176,
    "valid": 1176,
    "complete_transitions": 1176
  },
  "mechanism_checks": {
    "at_least_twenty_complete_revision_events": false,
    "revision_model_error_interval_upper_below_zero": false,
    "net_probe_interval_lower_above_zero": false
  },
  "mechanism_qualified": false,
  "live_trading_enabled": false,
  "hourly_cannot_resolve_subhour_edges": true
}
```

A negative squared-error difference favors adding weather. Bootstrap groups complete days and weights events equally.
Probe markouts are diagnostic one-contract round trips, not portfolio PnL or an 80%-hit strategy.
No models or thresholds were selected using evaluation performance. No live trades or forward logging were activated.

## Fourteen Days

| Date | Complete transitions | First revisions | Probe intentions | Mean net probe markout |
|---|---:|---:|---:|---:|
| 2026-08-28 | 42 | 0 | 0 | n/a |
| 2026-08-29 | 42 | 2 | 1 | $-0.05 |
| 2026-08-30 | 42 | 2 | 0 | n/a |
| 2026-08-31 | 42 | 0 | 0 | n/a |
| 2026-09-01 | 42 | 0 | 0 | n/a |
| 2026-09-02 | 42 | 0 | 0 | n/a |
| 2026-09-03 | 42 | 1 | 1 | $-0.04 |
| 2026-09-04 | 42 | 0 | 0 | n/a |
| 2026-09-05 | 42 | 1 | 0 | n/a |
| 2026-09-06 | 42 | 2 | 1 | $0.03 |
| 2026-09-07 | 42 | 0 | 0 | n/a |
| 2026-09-08 | 42 | 0 | 0 | n/a |
| 2026-09-09 | 42 | 0 | 0 | n/a |
| 2026-09-10 | 42 | 1 | 0 | n/a |

## Limitations

Previously researched dates, correlated city weather, short sample and source publication/polling delays limit inference.
NWS daily-high changes are not necessarily independent news. Remaining information can reach markets through other sources.
The within-day sign randomization is descriptive; exchangeability is an assumption, not a causal guarantee.
Strictly positive timing-mechanism gates are required before further model development, not direct deployment.
Sources: https://www.weather.gov/documentation/services-web-api and https://kalshi.com/docs/kalshi-fee-schedule.pdf
