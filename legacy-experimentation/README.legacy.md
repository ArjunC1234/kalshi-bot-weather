# Kalshi Weather Probabilities

This project calculates probability distributions for the nearest open daily-high
temperature event in six Kalshi series: New York City, Miami, Los Angeles,
Denver, Austin, and Oklahoma City. It does not inspect prices or trade.

The model combines public Kalshi bracket definitions, NWS forecasts and
settlement-station observations, and four Open-Meteo ensemble families: GEFS,
ECMWF IFS, ICON EPS, and GEM. Each model family receives equal total weight even
though their member counts differ.

Kalshi close times and settlement rules define and validate each 24-hour climate
day. The model anchors the model-balanced ensemble median to the larger of the
NWS daytime high and the NWS hourly maximum over that exact window. During an
active climate day, completed hours come from station observations and ensemble
members model only the remaining hours. The resulting Gaussian mixture cannot
assign probability below the high temperature already observed.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:NWS_USER_AGENT="kalshi-weather-probabilities/0.1 your-email@example.com"
```

The NWS requests a descriptive user agent containing contact information.

## Run

```powershell
python weather_probabilities.py
```

The command prints all bracket probabilities, saves a timestamped PNG under
`output/`, and opens a 2x3 Matplotlib dashboard. It also prints model member
counts, the raw ensemble consensus, the NWS center correction, the observation
cutoff, and any data-quality warnings.

```powershell
python weather_probabilities.py --date 2026-06-21
python weather_probabilities.py --output output/custom.png
python weather_probabilities.py --no-show
```

The requested date must currently be open on Kalshi. `--no-show` is useful on
headless systems and still saves the PNG.

## Test

```powershell
python -m unittest -v
```

This remains an uncalibrated forecast model. The equal model weights, 1°F minimum
kernel bandwidth, NWS centering, and Gaussian tail shape are explicit provisional
assumptions. Historical evaluation is required before treating the probabilities
as accurate trading estimates.

## Point-in-time backtesting

The backtester collects forecasts prospectively because the current NWS forecast
and individual Open-Meteo ensemble inputs cannot be reconstructed accurately for
older timestamps. Every snapshot contains the raw API responses, retrieval time,
model source hash, full probability distribution, and a forecast-only ablation.
Artifacts are compressed, immutable JSON files under `backtest_data/`.

The five checkpoints use each city's fixed-standard-time climate day:

- Previous evening at T-6 hours.
- Market day at T+6, T+10, T+14, and T+18 hours.

Set a descriptive NWS user agent before collecting:

```powershell
$env:NWS_USER_AGENT="kalshi-weather-backtest/0.1 your-email@example.com"
```

Run the collector manually once. This creates and freezes the cohort manifest:

```powershell
python weather_backtest.py --cohort pilot-v1 collect-due
python weather_backtest.py --cohort pilot-v1 status
```

Install the one-minute Windows scheduled task after the model version is final:

```powershell
.\install_collector_task.ps1 `
  -Cohort pilot-v1 `
  -NwsUserAgent "kalshi-weather-backtest/0.1 your-email@example.com"
```

The task records the first complete collection within five minutes of each
checkpoint. A checkpoint that misses that window is permanently marked missing;
later data is never substituted. If the model source changes, collection stops
with an error and a new cohort name is required. It runs through `pythonw.exe`
without opening a console and appends operational output to
`backtest_data/collector.log`.

Fetch settlements periodically, then evaluate entirely from stored data:

```powershell
python weather_backtest.py --cohort pilot-v1 settle
python weather_backtest.py --cohort pilot-v1 evaluate
```

Evaluation produces per-forecast and aggregate CSV files, JSON results, and a
Matplotlib dashboard. It reports log loss, multiclass Brier score, normalized
Ranked Probability Score, event-level top-one accuracy and coverage, paired
checkpoint changes, calibration bins, source ablation, collection latency, and
missing-data rates. It never treats the five forecasts for one city-day as five
independent settlements.

New schema-v2 snapshots also normalize NWS `updateTime`, `generatedAt`, request
and response times, latest observation age, and provider retrieval timestamps.
Schema-v1 snapshots remain fully supported because this metadata is recovered
from their immutable raw HTTP traces.

The evaluator computes several experimental distributions offline from those
same traces: a pooled family-centered challenger and independently centered
GEFS, ECMWF IFS, ICON EPS, and GEM variants. These preserve each family's member
spread while aligning its median to the NWS anchor. Reports include paired
baseline-versus-challenger scores, family diagnostics, and source freshness.
Challengers remain exploratory and are never promoted automatically; at least
60 distinct evaluation dates are required before manual promotion review.

New captures also try to archive Open-Meteo's NOAA `gfs_hrrr` short-range model
from the `/v1/gfs` endpoint as an auxiliary source. HRRR is used only as an
offline challenger named `hrrr_top3_rerank`: it keeps the baseline weather
model's top three brackets, then shifts probability inside that top-three set
toward the bracket containing HRRR's projected high. It does not create a trade
outside the original top three, and older snapshots without archived HRRR data
continue to evaluate normally.

This is the current source priority for top-three bracket selection:

| Source | Use | Reason |
|---|---|---|
| Open-Meteo `gfs_hrrr` | Implemented | No-key, short-range, hourly temperatures near each station; directly useful for choosing between neighboring brackets. |
| Aviation Weather Center METAR | Deferred | Useful as an independent observation freshness check, but not a forward high-temperature forecast. |
| Commercial consumer forecasts | Deferred | Potentially useful, but usually need API keys, terms review, or paid access before reliable archival. |

## Offline training

`train_offline_model.py` trains a calibration layer from archived snapshots and
settlements only. It does not call live APIs, overwrite snapshots, or change the
frozen baseline model. The default version learns a probability blend across the
baseline weather model, family-centered challenger, archived Kalshi midpoint, and
uniform reference:

```powershell
python train_offline_model.py --cohort pilot-v1
```

Reports are written under `output/offline_trained_model/`, including
`trained_model.json`, `summary.json`, `expanding_scores.csv`,
`weights_by_date.csv`, `checkpoint_models.json`, `checkpoint_model_scores.csv`,
and `comparison.png`. The expanding-window score trains
only on prior target dates, then scores the next date, so it is the leakage-safe
number to watch as more settlements arrive. The trained-on-all model is useful
for producing the current global and time-of-day weights, but it is in-sample and
should not be treated as proof of edge.

To train a weather-only calibration layer that excludes Kalshi prices:

```powershell
python train_offline_model.py --cohort pilot-v1 --candidates full,family_centered,uniform --output-dir output/offline_trained_weather_only
```

To compare offline model-shape improvements and archived ask-price edge signals:

```powershell
python model_improvement_report.py --cohort pilot-v1
```

This writes `output/model_improvement_report/` with soft-observation-floor
challengers, checkpoint-specific trained blends, disagreement diagnostics, and a
gross before-fee ask-price simulation. These reports are offline only and do not
change the active collector or frozen baseline model.

To simulate actual offline PnL with archived YES ask prices and Kalshi-style fees:

```powershell
python strategy_simulator.py --cohort pilot-v1
```

This writes `output/strategy_simulation/` with per-trade PnL, summaries by
model/checkpoint/city, negative-trade reports, cumulative PnL, and a strategy
dashboard. The simulator buys at archived YES ask prices, uses one contract per
trade by default, includes taker fees by default, and never places live orders.

To test capped fractional Kelly sizing offline:

```powershell
python strategy_simulator.py --cohort pilot-v1 --sizing kelly --output-dir output/strategy_simulation_kelly
```

Kelly sizing uses the model probability, archived YES ask, estimated fee, a
default 25% Kelly multiplier, a 5% bankroll cap per trade, and a 10-contract cap.
Archived ask size is used as an additional cap when available. Fixed one-contract
sizing remains the default because it is the cleanest apples-to-apples model
comparison.

To reduce exposure to very cheap longshot contracts while keeping the same
trade-selection logic:

```powershell
python strategy_simulator.py --cohort pilot-v1 --sizing kelly --longshot-max-contracts 1 --output-dir output/strategy_simulation_kelly_longshot_cap1
```

Additional optional controls are available for sensitivity tests:

```powershell
python strategy_simulator.py --cohort pilot-v1 --sizing kelly --min-ask 0.05 --output-dir output/strategy_simulation_kelly_minask05
python strategy_simulator.py --cohort pilot-v1 --sizing kelly --longshot-min-ev 0.15 --output-dir output/strategy_simulation_kelly_longshot_ev15
```

These controls are intentionally explicit flags rather than default behavior, so
baseline, Kelly, and risk-controlled Kelly results can be compared side by side.

The first 15 days are a pipeline pilot, not conclusive accuracy evidence. Sixty
days supports an initial pooled comparison but remains season-specific; prices,
fills, PnL, and trading profitability are intentionally excluded.
