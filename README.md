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
