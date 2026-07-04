# Systemd Files

Systemd service/timer files for server-side next-gen production processes belong here.

## Files

- `kalshi-weather-next-gen.service`: long-running bot placeholder.
- `kalshi-weather-collector-v3.service`: one-shot immutable fact collection run.
- `kalshi-weather-collector-v3.timer`: hourly schedule for collector v3.

## Collector v3 Install

Copy the service and timer to `/etc/systemd/system/`, then run:

```bash
systemctl daemon-reload
systemctl enable --now kalshi-weather-collector-v3.timer
systemctl status kalshi-weather-collector-v3.timer --no-pager
journalctl -u kalshi-weather-collector-v3.service -n 100 --no-pager
```

The collector service runs `collector/collector.py collect-once`. It writes a local spool first, syncs immutable fact rows to Postgres, uploads raw payloads to Supabase Storage, checks pending Kalshi settlements, and attempts final NWS high ingestion.

## Useful Operations

Disable the timer:

```bash
systemctl disable --now kalshi-weather-collector-v3.timer
```

Run one collection immediately:

```bash
systemctl start kalshi-weather-collector-v3.service
```

Inspect logs:

```bash
journalctl -u kalshi-weather-collector-v3.service -n 200 --no-pager
```

Keep these files aligned with the layout of `next-gen/production/deployable/`.
