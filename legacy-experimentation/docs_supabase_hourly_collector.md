# Supabase Hourly Collector

This collector is separate from the `pilot-v1` file archive and the demo
trading bot. It collects hourly Kalshi/weather research data, writes an
immutable local copy to the droplet SSD, and then syncs to Supabase.

## Data Layout

Local droplet data defaults to `collector_spool/`:

- `pending/`: queue files waiting for Supabase sync.
- `synced/`: queue files that were uploaded successfully.
- `archive/YYYYMMDD/`: permanent local compressed copies kept on the droplet.
- `failed/`: reserved for failed queue handling.

Supabase stores compact rows in Postgres and raw compressed JSON payloads in
Storage.

## Environment

Create `/opt/kalshi-bot-weather/.env`:

```bash
SUPABASE_URL="https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY="YOUR_SERVICE_ROLE_KEY"
SUPABASE_STORAGE_BUCKET="weather-research-raw"
NWS_USER_AGENT="kalshi-weather-research/0.1 your-email@example.com"
COLLECTOR_DATA_DIR="/opt/kalshi-bot-weather/collector_spool"
```

Use the Supabase service-role key only on the droplet. Do not expose it in a
browser or client app.

## Supabase Setup

Print the SQL schema:

```bash
cd /opt/kalshi-bot-weather
source .venv/bin/activate
python supabase_hourly_collector.py init-db --print-sql
```

Apply the SQL in the Supabase SQL editor. Then create a private Storage bucket
named `weather-research-raw`.

## Manual Test

Dry run without Supabase upload:

```bash
python supabase_hourly_collector.py collect-once --dry-run
python supabase_hourly_collector.py status
```

Real upload:

```bash
python supabase_hourly_collector.py collect-once
python supabase_hourly_collector.py status
```

If Supabase upload fails, the run remains in `collector_spool/pending/` and can
be retried:

```bash
python supabase_hourly_collector.py sync-spool
```

## Systemd

Copy the unit files:

```bash
sudo cp systemd/kalshi-weather-hourly-collector.service /etc/systemd/system/
sudo cp systemd/kalshi-weather-hourly-collector.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-weather-hourly-collector.timer
```

Check status and logs:

```bash
systemctl status kalshi-weather-hourly-collector.timer --no-pager
journalctl -u kalshi-weather-hourly-collector.service -f
```

## Export

Export compact rows for offline backtesting:

```bash
python supabase_hourly_collector.py export \
  --start 2026-07-01 \
  --end 2026-07-31 \
  --output-dir output/supabase_export_july
```

## Storage Expectations

The collector stores local compressed queue/archive files. A normal hourly run
should be small enough for a 10 GB droplet SSD for months of data, assuming logs
are managed. Supabase Free remains the tighter remote limit, so keep the raw
payloads compressed and use the `status` command plus the Supabase dashboard.
