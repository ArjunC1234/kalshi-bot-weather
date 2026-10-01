# Five-Minute Timing Capture

The existing `kalshi-weather-collector-v3.timer` remains hourly. The additional
`kalshi-weather-timing-v1.timer` runs every five minutes at second 30 UTC, all day
for all six cities. This is data capture only: no orders, model decisions, or
changes to trading configuration.

Each city cycle requests NWS points, daily forecast, hourly forecast, then Kalshi
open markets. The market request begins only after both forecast requests finish.
Unchanged forecasts are retained, so later research can include no-change controls.
This is five-minute sampling, not tick data or guaranteed executable prices.

## Data Contract

- Exact run, request and receipt timestamps, without hourly truncation.
- Separate immutable receipt IDs even when identical bodies share a storage blob.
- Endpoint names start with `timing_`; `collector_version` and metadata job are
  `timing-v1`. Existing hourly endpoint names and normalized rows are unchanged.
- Supabase/Postgres `collector_runs`, `raw_payloads`, and `provider_errors` contain
  the additional stream. Full provider JSON is in the existing storage bucket.
- No partial `weather_snapshots`, `market_snapshots`, or `events` are inserted.
  Existing normalized training exports therefore do NOT automatically include
  the higher-frequency stream. Timing analysis must read the raw timing endpoints
  and join by run/city and request/receipt times, not rounded checkpoint labels.
- Run metadata includes per-city source IDs, selected event/date, six-bracket
  validation, forecast-ready and quote timestamps, and response cache headers
  keyed by receipt ID. Failed/incomplete cities remain explicit; a partial run
  exits nonzero after attempting to sync its diagnostic records.
- The nominal snapshot is a cycle-start timestamp, NOT a claim that the later
  responses were available then. A successful capture does not establish signal
  freshness, forecast accuracy, fillability, or profitability.

## Load And Failure Handling

Nominal additional load: 18 NWS and 6 Kalshi GETs per five-minute cycle (5,184 NWS
and 1,728 Kalshi requests/day). No additional Open-Meteo or label backfill requests.
Requests are sequential, timeout after 8 seconds of inactivity, and are not retried
inside a cycle. A 429 persists a provider-wide Retry-After cooldown (minimum five
minutes), including across subsequent processes. NWS limits are unpublished;
watch errors instead of assuming this request rate guarantees acceptance.

The oneshot service does not overlap itself; `flock` also protects invocations
using the same service command. Direct Python invocations bypass that lock and
should use a separate dry-run directory. The hourly and timing services may run
concurrently, with separate pending queues. A 240-second systemd timeout bounds
stuck runs, and no missed-cycle replay storm is scheduled after downtime.

The timing spool lives under `collector_spool_v3/timing`, protected by the existing
deployment exclusions. Capture is durably spooled before sync. Up to three pending
files are synced per cycle; interrupted/failed sync leaves files retryable and DB
inserts remain idempotent. A backlog needs operator attention. No data is deleted
automatically. Capture stops below 512 MiB free disk; monitor archive growth and
remote storage usage. If a process is killed before spooling, that cycle can be
missing; the next scheduled cycle proceeds rather than fabricating recovery data.

## Operations

Install only `collector/timing_collector.py` and the two timing systemd units.
No database migration or existing collector replacement is required. Preserve the
hourly collector, `.env`, virtualenv, credentials, spools, and live strategy.

```sh
systemd-analyze verify /etc/systemd/system/kalshi-weather-timing-v1.service /etc/systemd/system/kalshi-weather-timing-v1.timer
systemctl daemon-reload
systemctl enable --now kalshi-weather-timing-v1.timer
systemctl start kalshi-weather-timing-v1.service
journalctl -u kalshi-weather-timing-v1.service -n 30 --no-pager
systemctl list-timers kalshi-weather-timing-v1.timer
```

Run `production/verify_timing.py` on the droplet to check the latest two synced
captures against committed database receipt IDs and timestamps. It fails if the
captures are incomplete, quotes precede forecasts, or receipt IDs collide.

Rollback stops only the added timer/service; the hourly collection continues:

```sh
systemctl disable --now kalshi-weather-timing-v1.timer
systemctl stop kalshi-weather-timing-v1.service
```

Provider documentation consulted:
- https://www.weather.gov/documentation/services-web-api
- https://open-meteo.com/en/pricing
