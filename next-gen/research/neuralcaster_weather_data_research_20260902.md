# Neuralcaster Weather Data Research - 2026-09-02

## Executive Read

The late-day Neuralcaster issue is real, but the failure is not only the GRU. The current weather-only feature feed does not contain the same near-final settlement signal that Kalshi and the market are using.

At `t_plus_23h` in the local `current_20260701_20260831_lightweight` export:

- Weather-only Neuralcaster top-one bracket accuracy: 68.4%.
- Market-implied top bracket accuracy: 100.0%.
- Existing market-aware Neuralcaster top-one accuracy: 100.0% from `t_plus_18h` through `t_plus_23h`.
- Directly choosing the bracket from `observed_high_so_far_f`, HRRR, or NBM at `t_plus_23h`: about 69.5-70.1%.
- If the official winner bracket contains `observed_high_so_far_f`, Neuralcaster is 97.5% accurate.
- If `observed_high_so_far_f` is outside the winner bracket, Neuralcaster accuracy falls to 43.5%.
- Every `t_plus_23h` Neuralcaster top-one miss is adjacent to the winner bracket; top-or-adjacent accuracy is 100%.

Interpretation: Neuralcaster mostly understands the late-day weather state it is given. The problem is that the weather-only inputs are often one settlement bracket away from the official value, while the market already has the settlement-equivalent value.

## What Settlement Is Based On Now

Kalshi's current help page says each weather market names its own official source, but daily high/low temperature markets settle from the final NWS Daily Climate Report, usually the next morning. Hourly temperature markets settle on The Weather Company readings. Kalshi also notes that daily temperature markets use local standard time; during daylight saving time the daily high window is 1:00 AM through 12:59 AM local daylight time, not civil midnight to midnight.

Sources:

- Kalshi Weather Markets help: https://help.kalshi.com/en/articles/13823837-weather-markets
- Kalshi Market Outcomes help: https://help.kalshi.com/en/articles/13823826-market-outcomes
- Kalshi Rules Summary help: https://help.kalshi.com/en/articles/13823823-rules-summary

Kalshi announced a Weather Company partnership on 2026-08-27, saying The Weather Company will verify outcomes on Kalshi weather markets and provide authoritative observation data with documented methodology. Weather.com now has a public Kalshi page for official climate reports.

Sources:

- Kalshi announcement: https://news.kalshi.com/p/kalshi-weather-company-partnership
- Weather.com Kalshi data page: https://weather.com/kalshi

Practical reading for our daily high markets: do not assume generic app highs, NWS forecast highs, or raw hourly observations settle the market. We need to model the named station's official daily climate high as published for the market's climate window, and we need to track whether the official source path changed for a specific market.

## Publicly Accessible Data

### Kalshi Market Data

Kalshi exposes public market/event endpoints. The event response includes settlement sources and market rules fields, plus settlement metadata after resolution.

Source:

- Kalshi Get Event API docs: https://docs.kalshi.com/api-reference/events/get-event

Useful fields for this project:

- `event.settlement_sources`
- `markets[].rules_primary`
- `markets[].rules_secondary`
- `markets[].expiration_value`
- `markets[].settlement_ts`
- `markets[].result`
- bracket metadata from market titles/strikes/product metadata

We should persist those fields per event, not just prices. The current local export has many `market_settlement_source: "unknown"` rows, which is not acceptable for a model that needs source-specific finality.

### NWS API

The NWS API is public/open data and free to use with reasonable rate limits. It provides forecast, hourly forecast, grid data, alerts, stations, and station observations. It requires a User-Agent header.

Source:

- NWS API documentation: https://www.weather.gov/documentation/services-web-api

Relevant endpoints:

- `/points/{lat},{lon}` for grid/station discovery.
- `/gridpoints/{office}/{x},{y}/forecast`
- `/gridpoints/{office}/{x},{y}/forecast/hourly`
- `/stations/{stationId}/observations`
- `/stations/{stationId}/observations/latest`

Important caveat from NWS: station observations are upstreamed through MADIS and may be delayed up to about 20 minutes due to QC processing; station observation endpoints also have known upstream issues around 24h max/min outside the central time zone. That means NWS API latest observations are useful, but not sufficient as the only settlement-finality source.

### IEM ASOS/METAR Data

Iowa Environmental Mesonet provides public ASOS/AWOS/METAR downloads. Its routine archive is synced from realtime ingest every 10 minutes and sources include Unidata IDD, NCEI ISD, and MADIS One Minute ASOS.

Source:

- IEM ASOS/METAR download: https://www.mesonet.agron.iastate.edu/request/download.phtml
- IEM API index: https://mesonet3.agron.iastate.edu/api/

This should be our primary public cross-check for station observations because it can include routine and special reports and is easy to script. Use both routine and special reports, not routine-only.

### IEM / NCEI One-Minute ASOS

IEM also exposes processed one-minute ASOS data from NCEI/MADIS. It is useful for settlement reconciliation and training labels, but not for live trading near market close because availability is delayed, often until the next day or later.

Source:

- IEM ASOS one-minute page: https://www.mesonet.agron.iastate.edu/request/asos/1min.phtml

### NWS CLI / CF6 Climate Reports

NWS climate pages expose Daily Climate Report (CLI) and Preliminary Monthly Climate Data (CF6) products. These are the official daily settlement-style products for daily temperature markets.

Source:

- NWS climate page example: https://www.weather.gov/wrh/climate
- IEM CF6 map: https://mesonet1.agron.iastate.edu/nws/cf6map.php

For historical labels and post-close reconciliation, we should fetch CLI first and CF6 as backup. For live finality before settlement, scrape/parse preliminary CLI text only when it is explicitly the relevant product/date/station and distinguish preliminary versus final.

## What This Means For Our Current Data

The current collector builds:

- `observed_high_so_far_f` from observation rows.
- `hrrr_projected_high_f` and `nbm_projected_high_f` as `max(observed_high_so_far_f, remaining forecast high)`.
- `nws_anchor_high_f` as `max(daily forecast high, hourly forecast-window max)`.

Code references:

- `production/deployable/collector/normalizers.py`
- `maxtemp-engine/raycaster/v1/features.py`
- `maxtemp-engine/neuralcaster/v2/cli.py`

This creates a dangerous late-day pattern: if `observed_high_so_far_f` is stale, station-misaligned, or computed from the wrong observation cadence, HRRR and NBM become confirming-but-wrong because they are clamped to that same observed value. Neuralcaster then sees three agreeing sources and sensibly predicts the adjacent wrong bracket.

Concrete local example:

- Event: `KXHIGHDEN-26JUL16`
- At `2026-07-17T06:00:00Z`, settlement and market winner: 93-94F.
- Stored weather fields: `observed_high_so_far_f = 91.94`, `hrrr_projected_high_f = 91.94`, `nbm_projected_high_f = 91.94`, `nws_anchor_high_f = 79`.
- Market-implied probability on the winner at that timestamp: 97.5%.

That is not a modeling nuance; it is a feature/source mismatch.

## Why Neuralcaster v2 Fails Late Day

1. The model target is continuous final high, but trades settle discrete 2F brackets.

   A 0.6F MAE at `t_plus_23h` sounds strong, but exact bracket accuracy is fragile when the actual value is near a bracket boundary.

2. The probability layer is too soft for finality.

   Neuralcaster v2 predicts a Gaussian residual and clamps sigma to at least 1F when making quantiles. The distribution also uses an observed floor of `observed_high_so_far_f - 0.75F`, not a hard impossibility boundary. That spreads probability across adjacent brackets even late in the day.

3. The observed feature does not reliably match settlement.

   Late-day `observed_high_so_far_f` is present, but it is not settlement-equivalent. It often lands outside the eventual official winner bracket.

4. NWS daily forecast parsing can be wrong for the market climate day.

   Some late-day `nws_anchor_high_f` values are wildly wrong versus settlement, which suggests the forecast period/date logic is not always aligned to the Kalshi climate window.

5. Weather-only model lacks settlement-source finality.

   The market and market-aware Neuralcaster know the near-final result by `t_plus_18h`; weather-only does not. We need a public source path that captures that same information without using market prices as a crutch.

## Recovery Plan

### Phase 1: Fix Labels And Source Metadata

- Store event-level `settlement_sources`, `rules_primary`, and `rules_secondary` from Kalshi API.
- Normalize daily-high source type per event: NWS CLI, Weather Company daily climate, hourly Weather Company, backup CF6, unknown.
- Reject or quarantine training rows where the final label disagrees with the winner bracket.
- Keep both Kalshi settlement value and independent official CLI/TWC value as separate labels.

Success criterion: for every settled daily high event, we know the station, source family, climate window, final label source, and whether the label maps to the winner bracket.

### Phase 2: Replace `observed_high_so_far_f`

Build a station-source reconciler that computes observed high so far from multiple public feeds:

- NWS API station observations.
- IEM ASOS/METAR routine + specials.
- IEM one-minute ASOS for post-hoc correction/backfill.
- Weather.com/Kalshi official climate page where accessible.
- NWS CLI/CF6 once published.

Persist separate fields instead of one blended observed value:

- `observed_high_nws_api_f`
- `observed_high_iem_metar_f`
- `observed_high_iem_1min_f`
- `official_cli_high_f`
- `weather_company_kalshi_high_f`
- `observed_high_consensus_f`
- `observed_high_source_count`
- `observed_high_disagreement_f`
- `latest_observation_age_seconds_by_source`
- `settlement_source_available`

Success criterion: by `t_plus_18h`, the public observed consensus should match the winner bracket at least 97-99% on historical settled days, comparable to market-implied finality.

### Phase 3: Fix Climate Window And Station Mapping

- Treat daily temperature markets as local-standard-time climate days.
- During daylight saving time, daily high window is 1:00 AM through 12:59 AM local daylight time, per Kalshi help.
- Do not infer station from city name alone; persist the station named in rules or source URL.
- Add station metadata for each series/city and validate against market rules.

Success criterion: no more `market_settlement_source: "unknown"` for active daily markets; station/time window is explicit in every weather row.

### Phase 4: Make Neuralcaster Bracket-Native

Replace or augment the Gaussian final-high head with a bracket-native model:

- Direct ordinal/bracket classification head over the exact active market brackets.
- Monotonic cumulative probabilities for thresholds.
- Hard constraints from observed high:
  - Any YES bracket whose upper bound is below the confirmed observed high gets probability 0.
  - Any NO side for an observed-confirmed bracket is blocked.
  - Tail handling respects rounded official settlement rules.
- Dynamic uncertainty:
  - large early-day sigma;
  - near-zero late-day sigma when official/consensus observed source is fresh and source disagreement is small.

Success criterion: weather-only Neuralcaster should approach market-like top-one accuracy after `t_plus_18h` without using market prices.

### Phase 5: Trading Signal Recovery

After weather-only late-day finality is repaired:

- Do not trade when market is already 95-99% correct unless the price is stale.
- Trade only disagreement between repaired weather consensus and market, not raw forecast.
- Separate strategies:
  - early-day forecast revision edge;
  - late-day settlement-finality/stale-order edge.
- Add features for market lag:
  - newest official observed high timestamp;
  - market last update timestamp;
  - orderbook still offering stale YES/NO;
  - source disagreement less than threshold.

Success criterion: positive CLV before settlement, not just positive final PnL on a small sample.

## Immediate Implementation Recommendation

The next code change should not be another strategy threshold. It should be a new `settlement_source_reconciler` module that builds settlement-aligned observed highs per event/snapshot and writes diagnostics:

- observed source availability by city/checkpoint;
- observed consensus versus Kalshi settlement bracket;
- stale observation age by source;
- source disagreement by checkpoint;
- NWS CLI/TWC availability after close;
- market-implied top accuracy as a benchmark, not as a training feature.

Once that shows `t_plus_18h` through `t_plus_23h` observed consensus accuracy near 98-100%, rerun Neuralcaster weather-only with bracket-native constraints.
