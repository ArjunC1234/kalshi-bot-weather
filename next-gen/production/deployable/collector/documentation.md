# Collector v3

Collector v3 is the standalone server collector. It records immutable weather-market facts for later local backtesting and model development.

## Purpose

Collector v3 records facts only:

- Public Kalshi daily-high events and bracket quote snapshots.
- NWS points metadata.
- NWS daily forecast periods.
- NWS hourly forecast periods.
- NWS station observations.
- Open-Meteo ensemble forecast summaries and raw payloads.
- Open-Meteo HRRR and NBM forecast summaries and raw payloads.
- Compact derived weather and market facts.
- Provider failures.
- Final Kalshi settlement facts after markets resolve.
- Final NWS high labels after NWS climate products become available.

It never stores model runs, model outputs, Trends reports, strategy simulations, or PnL.

## Commands

Run from `/opt/kalshi-weather-next-gen`:

```bash
python collector/collector.py init-db --print-sql
python collector/collector.py collect-once --dry-run
python collector/collector.py collect-once
python collector/collector.py sync-spool
python collector/collector.py settle-pending
python collector/collector.py ingest-final-highs
python collector/collector.py status
python collector/collector.py export --start 2026-07-01 --end 2026-09-30 --output-dir exports/2026q3
```

## Normal Hourly Behavior

`collect-once` should:

1. Determine each city's local target date and fixed-standard climate window.
2. Fetch Kalshi, NWS, observations, ensemble, HRRR, and NBM data.
3. Write a local gzip spool file before remote sync.
4. Upload raw payloads to Supabase Storage.
5. Insert compact immutable rows into Postgres.
6. Attempt pending settlement ingestion.
7. Attempt final NWS high ingestion.
8. Leave missing labels pending rather than backfilling with guessed values.

## Design Rules

- Every collection run writes a local spool before remote sync.
- Raw HTTP responses go to Supabase Storage as `.json.gz`.
- Compact facts go to Postgres with deterministic primary keys.
- Inserts use conflict-safe immutable behavior.
- Normal operation does not mutate old snapshot facts.
- Settlements and final highs are separate label facts joined through views.
- Backtests export/query this database, then run models locally.

## Required Environment

- `NWS_USER_AGENT`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET`
- `DATABASE_URL`
- `COLLECTOR_DATA_DIR`

## Files

- `collector.py`: CLI and orchestration.
- `schema.py`: immutable fact tables and views.
- `clients.py`: HTTP recorder, Supabase Storage client, Postgres client.
- `normalizers.py`: provider payload parsing and normalized row building.
- `time_utils.py`: city-local and fixed-standard climate-day windows.
- `spool.py`: local retryable spool files.
- `config.py`: city definitions and environment loading.
- `ids.py`: deterministic row and payload IDs.

## Health Checks

On the server:

```bash
python collector/collector.py status
```

Locally, after exporting:

```powershell
python -m backtest.cli daily-health --data data/export_YYYY --date 2026-07-04
```
