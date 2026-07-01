# Hourly Supabase Collector

This collector is independent from the existing demo trading bot. It captures hourly Kalshi/weather research data, writes an immutable local spool first, then syncs to Supabase.

## Environment

Add these values to `/opt/kalshi-weather-demo-bot/.env` on the droplet:

```bash
NWS_USER_AGENT="kalshi-weather-collector/0.1 your-email@example.com"
SUPABASE_URL="https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY="YOUR_SERVICE_ROLE_KEY"
SUPABASE_STORAGE_BUCKET="weather-research-raw"
COLLECTOR_DATA_DIR="/opt/kalshi-weather-demo-bot/collector_spool"
```

Use the Supabase service-role key only on the server. Do not put it in client-side apps.

## Supabase Setup

Create a private Storage bucket named `weather-research-raw`.

Print the database SQL:

```bash
cd /opt/kalshi-weather-demo-bot
source .venv/bin/activate
python supabase_hourly_collector.py init-db --print-sql
```

Paste the SQL into the Supabase SQL editor and run it.

## First Run

Dry run creates a local spool file only:

```bash
cd /opt/kalshi-weather-demo-bot
source .venv/bin/activate
python supabase_hourly_collector.py collect-once --dry-run
python supabase_hourly_collector.py status
```

Real run creates a spool file and syncs it:

```bash
python supabase_hourly_collector.py collect-once
python supabase_hourly_collector.py status
```

If Supabase fails, the spool stays in `collector_spool/pending/`. Retry with:

```bash
python supabase_hourly_collector.py sync-spool
```

## Systemd Deployment

Copy service files and enable the hourly timer:

```bash
cd /opt/kalshi-weather-demo-bot
cp systemd/kalshi-weather-hourly-collector.service /etc/systemd/system/
cp systemd/kalshi-weather-hourly-collector.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kalshi-weather-hourly-collector.timer
systemctl list-timers kalshi-weather-hourly-collector.timer --no-pager
```

Run once immediately:

```bash
systemctl start kalshi-weather-hourly-collector.service
journalctl -u kalshi-weather-hourly-collector.service -n 100 --no-pager
```

Follow logs:

```bash
journalctl -u kalshi-weather-hourly-collector.service -f
```

## Export

Export compact rows from Supabase for offline research:

```bash
python supabase_hourly_collector.py export --start 2026-07-01 --end 2026-07-07 --output-dir output/supabase_export
```

## Storage Model

- Droplet SSD: local durable spool and outage buffer.
- Supabase Storage: compressed raw API payloads.
- Supabase Postgres: compact searchable features and raw payload pointers.

The collector does not place trades.
