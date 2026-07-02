# Collector v3

Standalone immutable weather-market collector for the deployed server.

## Purpose

Collector v3 records facts only:

- Public Kalshi daily-high event and bracket quote snapshots.
- NWS points, daily forecast, hourly forecast, and station observations.
- Open-Meteo ensemble, HRRR, and NBM forecast payloads.
- Compact derived weather and market features.
- Provider failures.
- Final Kalshi settlement facts after markets resolve.

It never stores model runs, model outputs, reports, strategy simulations, or PnL.

## Commands

Run from `/opt/kalshi-weather-next-gen`:

```bash
python collector/collector.py init-db --print-sql
python collector/collector.py collect-once --dry-run
python collector/collector.py collect-once
python collector/collector.py sync-spool
python collector/collector.py settle-pending
python collector/collector.py status
python collector/collector.py export --start 2026-07-01 --end 2026-09-30 --output-dir exports/2026q3
```

## Design Rules

- Every collection run writes a local gzip spool before remote sync.
- Raw HTTP responses go to Supabase Storage as `.json.gz`.
- Compact facts go to Postgres with deterministic primary keys.
- Inserts use `on conflict do nothing`; normal operation does not mutate facts.
- Settlements are inserted as separate facts and joined by SQL views.
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
- `normalizers.py`: provider payload parsing and feature rows.
- `time_utils.py`: city-local and fixed-standard climate-day windows.
- `spool.py`: local retryable spool files.
- `config.py`: city definitions and env loading.
