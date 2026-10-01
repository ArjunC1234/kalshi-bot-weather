# Observation-Only Timing Capture Requirements

Status: design only. Not deployed, scheduled or running. No order submission.
Purpose: obtain evidence that hourly history cannot supply, not assume a latency edge.

## Records

- Persist source identifier, target date, station/grid, version, updateTime,
  generatedAt, content hash, HTTP cache metadata and monotonic/wall-clock request
  and response timestamps. Never equate an internal update timestamp with proven
  first public availability.
- When a new forecast version is observed, request a fresh relevant market
  orderbook only after the forecast response completes. Keep the preceding quote
  as a reference, but do not assume it remains executable.
- Record quote request and receipt times, bid/ask prices, exact quantities,
  market status, applicable fee multiplier and orderbook sequencing where available.
- Sample subsequent prices at predeclared 1-, 5-, 15- and 60-minute horizons;
  respect provider rate limits and cache controls. Missing or throttled observations
  remain missing, not forward-filled executable quotes.
- Retain no-revision control cycles and all six cities. Do not sample only updates
  that later moved prices favorably.

## Evaluation

Freeze the signal definition, horizons, fee/execution assumptions and exclusions
before capture. Separate the decision log from outcome attachment and preserve
both timestamps. At least 20 independent revision events are needed for an initial
mechanism check; a 14-day period with fewer is inconclusive, not a failed guarantee.

First ask whether the revision improves price prediction beyond information
already in the market. Then assess executable prices after costs, uncertainty and
event concentration. An 80%-hit settlement strategy requires separate probability
calibration and evaluation; a positive short-horizon quote movement is not that.

Do not assume maker fills, queue priority, unlimited depth, zero latency or a
standing quote surviving a news release. No historical replay can substitute for
the missing high-frequency observation log. Activating capture is a separate step
from promoting or trading any strategy.
