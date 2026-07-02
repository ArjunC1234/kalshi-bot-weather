# Deployable Server Folder

This folder mirrors the server-side directory deployed to `/opt/kalshi-weather-next-gen`.

Only files in this folder are uploaded by the production deploy scripts.

## What Belongs Here

- Production bot entrypoints.
- Collector v3 code under `collector/`.
- Runtime `requirements.txt`.
- `.env.example` templates with placeholder values only.
- `systemd/` service and timer files.

## What Does Not Belong Here

- Backtest reports, model artifacts, plots, or strategy simulations.
- Real `.env` files or private keys.
- Legacy experimentation scripts unless intentionally adapted to next-gen production.

## Collector v3

Collector v3 treats Supabase/Postgres as an immutable fact database. It stores raw API payloads, compact market/weather facts, provider errors, and settlement facts. It does not store model outputs, backtest results, strategy results, or reports.

Run from the deployed folder:

```bash
source .venv/bin/activate
python collector/collector.py init-db --print-sql
python collector/collector.py collect-once --dry-run
python collector/collector.py collect-once
python collector/collector.py settle-pending
python collector/collector.py sync-spool
python collector/collector.py status
python collector/collector.py export --start 2026-07-01 --end 2026-09-30 --output-dir exports/2026q3
```

Required environment variables:

- `NWS_USER_AGENT`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET`
- `DATABASE_URL`
- `COLLECTOR_DATA_DIR`

## Deployment Check

After copying this folder to the droplet:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python collector/collector.py collect-once --dry-run
```

Apply the SQL printed by `init-db --print-sql` in Supabase SQL editor, then enable `systemd/kalshi-weather-collector-v3.timer`.
