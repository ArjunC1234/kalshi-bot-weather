# Kalshi Weather Probabilities

This project calculates probability distributions for the nearest open daily-high
temperature event in six Kalshi series: New York City, Miami, Los Angeles,
Denver, Austin, and Oklahoma City. It does not inspect prices or trade.

The model combines public Kalshi bracket definitions, the NWS forecast and
settlement-station observations, and the Open-Meteo GFS ensemble. Ensemble
members are centered on the NWS forecast and converted into bracket probabilities
with a smoothed kernel distribution.

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
`output/`, and opens a 2x3 Matplotlib dashboard.

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

This is an uncalibrated forecast model. Historical evaluation is required before
treating its probabilities as accurate trading estimates.
