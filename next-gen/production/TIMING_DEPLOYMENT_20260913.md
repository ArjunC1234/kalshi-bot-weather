# Timing Collector Deployment Verification

Deployed over SSH to `root@134.122.9.101`, under
`/opt/kalshi-weather-next-gen`. Verification completed September 13, 2026 EDT
(September 14, 2026 UTC). Release: `releases/timing-20260913-v1`.

## Scope

Added `collector/timing_collector.py`, its documentation, and
`kalshi-weather-timing-v1.service` / `.timer`. The additional raw capture runs at
minutes 00, 05, 10, ..., second 30 UTC. Full model/weather collection and label
maintenance remain hourly. No trading changes, schema migration, credential
changes, historical rewrites, or data deletion.

The existing Python collector files, `live_strategy.py`, and both hourly systemd
units were hashed before installation and verified unchanged afterward. The
baseline manifest is retained on the droplet at
`releases/timing-20260913-v1/existing-files.sha256`. Staged file hashes matched the
tested local files before installation:

| File | SHA-256 |
| --- | --- |
| timing_collector.py | 427b33087d62aed99dec8aa350a5b7de55d38822fedb02ebf5db968cc09cae42 |
| timing service | d59d73b26c43165baadcccdc9a478ad5461f9e3a4608d51ea51d057eeb56350d |
| timing timer | ffd4ac60bee27bfd75ef40cb8d2cfa4770b0bde61166ee029f7ba879bb928010 |

## Checks

- 12 timing tests and 19 existing collector tests passed.
- 16 backtest regression tests passed. Initial invocation from the repository root
  failed on import paths; rerunning from `next-gen` resolved that invocation error.
- Ruff passed for new Python code and verification script.
- Native `systemd-analyze verify` passed for both new units; calendar parsed as
  the intended five-minute UTC schedule.
- Real API dry run: six cities, 24 receipts, zero errors, no database writes.
- Two production captures were verified against committed Postgres receipt IDs
  and exact request/receipt timestamps. IDs did not collide within the hour.
- Every city retained six market brackets and a quote request after completion
  of its forecast requests. Cache response headers remained keyed to receipt IDs.
- Systemd journal confirmed the second run was triggered at 02:20:30 UTC and
  completed successfully at 02:20:37 UTC.

| Capture | UTC cycle start | Cities | Raw receipts / DB receipts | Errors | Max forecast-ready to quote-request gap |
| --- | --- | --- | --- | --- | --- |
| Initial service run | 2026-09-14 02:18:21.657827 | 6/6 | 24/24 | 0 | 0.004196 seconds |
| Timer-triggered run | 2026-09-14 02:20:31.541678 | 6/6 | 24/24 | 0 | 0.004383 seconds |

Production run IDs:
- `1a8a64c0bb0126fc45c3fa2c4647443a`
- `67ad715a4da6e4d616bf47af2d5d8a8b`

No pending timing spools remained after verification. Both timers were active;
the next timing trigger was 02:25:30 UTC and the next hourly trigger 03:00:39 UTC.
The production captures were about 62.5 KB each. If that size holds, the two local
spool copies imply roughly 34 MiB/day of disk growth; this is an estimate, not a
storage quota or cost guarantee. Available droplet space before deployment was
17 GB. Raw storage and database growth also require monitoring.

## Boundaries

These checks establish initial operation, not long-term reliability or a trading
edge. NWS response versions can still be old; request order is not equivalent to
forecast freshness. Market quotes are not guaranteed fills. Timing raw records do
not automatically become rows in existing normalized training exports.

See `deployable/collector/TIMING_CAPTURE.md` for the data contract, failure handling,
request budget, and operational details. Rollback requires only:

```sh
systemctl disable --now kalshi-weather-timing-v1.timer
systemctl stop kalshi-weather-timing-v1.service
```

This leaves the original hourly collector running and preserves all captured data.
