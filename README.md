# Kalshi Weather Forecasting Research

An end-to-end research system for collecting point-in-time weather and prediction-market data, modeling daily high-temperature outcomes, evaluating Kalshi bracket probabilities, and testing paper-trading strategies under reproducible offline conditions.

This project started as a tightly coupled prototype and evolved into a modular research platform with immutable data collection, frozen local exports, weather-only model families, backtesting utilities, a local analysis workbench, and production deployment scaffolding.

## What Employers Should Notice

- Built a full data pipeline around time-sensitive prediction-market research: hourly collectors, immutable Supabase/Postgres facts, raw payload storage, frozen local exports, validation, model evaluation, and reporting.
- Designed clear boundaries between weather forecasting, market-aware modeling, strategy simulation, and production runtime code.
- Implemented multiple model families for maximum-temperature forecasting, including baseline blends, Raycaster, Cloudcaster, Neuralcaster, and manual residual experiments.
- Created offline backtesting and quality tooling so model and strategy results can be audited against fixed datasets instead of changing live sources.
- Built a React/Vite workbench and Python control API for browsing exports, artifacts, reports, registered models, and visualization-ready data.
- Treated trading claims skeptically: positive paper results are kept separate from weather-model accuracy and documented as hypothesis-generating, not deployment proof.

## Project Scope

Kalshi weather markets resolve against daily weather outcomes such as a city's official high temperature. The project asks two separate questions:

1. Can point-in-time weather data be converted into calibrated probabilities over Kalshi temperature brackets?
2. If those probabilities differ from market prices, do any paper-trading policies survive realistic validation?

The distinction matters. A model can predict temperature well and still make poor trades if its bracket probabilities are miscalibrated, if the market already prices in the signal, or if execution rules select bad entries.

## Current Architecture

```text
collector v3 on server
  -> immutable Supabase/Postgres facts and raw payload references
  -> frozen local exports under next-gen/data/
  -> validation, quality checks, and model reports
  -> weather-only max-temperature models
  -> strategy and EV backtests
  -> Trends / Workbench UI for inspection
```

Core design choices:

- **Immutable facts first:** snapshots preserve what was known at collection time.
- **Frozen exports:** local datasets make evaluations repeatable.
- **Model boundaries:** weather-only models do not use Kalshi prices, PnL, or trade labels as predictive features.
- **Local reports:** model outputs, strategy simulations, and PnL artifacts stay local instead of being written back to Supabase.
- **Versioned experiments:** new model assumptions live in new folders rather than overwriting prior results.

## Repository Map

| Path | Purpose |
| --- | --- |
| `next-gen/` | Active modular codebase for collection, exports, models, backtests, control APIs, and production deployment. |
| `next-gen/libs/` | Shared clients, schemas, metrics, probabilities, time helpers, constants, and validation utilities. |
| `next-gen/backtest/` | Supabase export, local replay, validation, quality reports, settlement loading, and model report writing. |
| `next-gen/maxtemp-engine/` | Weather-only models that forecast final NWS high temperature and convert distributions into Kalshi bracket probabilities. |
| `next-gen/strategy/` and `next-gen/strategy-engine/` | Strategy, EV, fills, sizing, replay, metrics, and trade/no-trade research. |
| `next-gen/control/` | Registry-driven backend for exports, model runs, artifacts, jobs, dashboards, and visualization queries. |
| `next-gen/trends/` | Local analysis workbench and API for inspecting datasets, reports, and model behavior. |
| `next-gen/trends/ui/` | React 19 + Vite + ECharts frontend for the workbench experience. |
| `next-gen/production/deployable/` | Server-ready collector/runtime tree and systemd units. |
| `manual-model/` | Small, readable hand-written model workspace for validating ideas before integrating them. |
| `legacy-experimentation/` | Archived collectors, backtests, reports, and early prototypes kept for historical context. |
| `research-paper/` | Synthesized research writeup, indexes, and charts summarizing findings and limitations. |

## Evidence And Results

The repository includes generated research summaries and chart assets in `research-paper/`. The strongest large-sample bracket-probability results documented there came from Neuralcaster v2 market-blended models, with a reported log loss of `0.588`, temperature MAE of `0.865 F`, and top-one bracket accuracy of `75.1%` across `2,012` bracket predictions.

That result is useful evidence, but it is not a pure weather-only claim because the model blends in market information. Weather-only and residual models are evaluated separately, and trading backtests are treated as less settled because apparent profitability changes under validation windows, controls, spreads, and execution filters.

For the full writeup, see [`research-paper/kalshi_weather_research_paper.md`](research-paper/kalshi_weather_research_paper.md).

## Main Workflows

Run these from the repository root unless noted otherwise.

```powershell
python -m compileall next-gen
python -m unittest discover -s next-gen
python -m ruff check next-gen
```

Create and validate a local export:

```powershell
cd next-gen
python -m backtest.cli pipeline --start 2026-07-01 --end 2026-07-31
```

Evaluate a max-temperature model:

```powershell
cd next-gen
python maxtemp-engine/raycaster/v1/cli.py evaluate --data data/export_YYYY --output reports/model/raycaster_v1_eval
```

Start the local analysis UI:

```powershell
cd next-gen
python -m trends.cli serve
```

Start the newer workbench backend and frontend:

```powershell
cd next-gen/trends/ui
npm install
npm run dev:workbench
```

## Setup

Python dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r next-gen/requirements.txt
```

Environment variables:

```powershell
copy .env.example .env
```

Fill `.env` with local credentials only. Real `.env` files, PEM private keys, logs, virtual environments, node modules, and generated UI builds are intentionally ignored.

## Security Note

This is a research repository, not a trading recommendation or deployment guarantee. Live credentials should never be committed. If a real key or service credential has ever been committed to git history, rotate it before making the repository public.

## Documentation

- [`documentation.md`](documentation.md): repository workflow and folder rules.
- [`next-gen/documentation.md`](next-gen/documentation.md): active architecture and subsystem responsibilities.
- [`next-gen/maxtemp-engine/documentation.md`](next-gen/maxtemp-engine/documentation.md): weather-model boundaries.
- [`next-gen/backtest/documentation.md`](next-gen/backtest/documentation.md): export, validation, quality, and report commands.
- [`next-gen/control/README.md`](next-gen/control/README.md): control API and registry contracts.
- [`next-gen/production/deployable/documentation.md`](next-gen/production/deployable/documentation.md): server deployment folder expectations.
- [`legacy-experimentation/documentation.md`](legacy-experimentation/documentation.md): archived prototype context.
