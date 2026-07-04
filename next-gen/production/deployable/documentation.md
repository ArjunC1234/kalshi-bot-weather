# Deployable Server Folder

This folder mirrors the server-side directory deployed to `/opt/kalshi-weather-next-gen`. The production deploy scripts upload this folder's contents, not the whole repository.

## What Belongs Here

- Collector v3 under `collector/`.
- Runtime `requirements.txt`.
- `.env.example` templates with placeholder values only.
- `systemd/` service and timer files.
- Minimal production entrypoints such as `bot.py` placeholders.

## What Does Not Belong Here

- Backtest reports.
- Model artifacts.
- Trends UI reports.
- Strategy simulations.
- Local frozen exports.
- Legacy experimentation scripts.
- Real credentials in tracked files.

## Collector v3 Commands

Run from `/opt/kalshi-weather-next-gen` on the droplet:

```bash
source .venv/bin/activate
python collector/collector.py init-db --print-sql
python collector/collector.py collect-once --dry-run
python collector/collector.py collect-once
python collector/collector.py sync-spool
python collector/collector.py settle-pending
python collector/collector.py ingest-final-highs
python collector/collector.py status
python collector/collector.py export --start 2026-07-01 --end 2026-09-30 --output-dir exports/2026q3
```

`collect-once` writes a local spool first, then syncs facts. It should also run post-event settlement/final-high ingestion without blocking normal snapshot collection if labels are not ready yet.

## Required Environment

- `NWS_USER_AGENT`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET`
- `DATABASE_URL`
- `COLLECTOR_DATA_DIR`

## First-Time Server Setup

```bash
cd /opt/kalshi-weather-next-gen
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python collector/collector.py collect-once --dry-run
```

Apply the SQL printed by `init-db --print-sql` in the Supabase SQL editor, then enable `systemd/kalshi-weather-collector-v3.timer`.

## Ongoing Checks

```bash
python collector/collector.py status
systemctl status kalshi-weather-collector-v3.timer --no-pager
journalctl -u kalshi-weather-collector-v3.service -n 100 --no-pager
```
