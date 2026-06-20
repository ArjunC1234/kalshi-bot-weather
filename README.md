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

The first 15 days are a pipeline pilot, not conclusive accuracy evidence. Sixty
days supports an initial pooled comparison but remains season-specific; prices,
fills, PnL, and trading profitability are intentionally excluded.
