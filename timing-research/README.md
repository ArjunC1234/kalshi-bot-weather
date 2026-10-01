# Timing Research

An isolated sibling of `next-gen`, within the existing repository. Python 3.12,
standard library only. No runtime imports from `next-gen`, external dependencies,
credentials, order APIs, production deployment code, or unattended trading.
The deployed five-minute collector remains unchanged.

## Layout

- `timing_research/contract.py`: explicit collector-v3 raw JSON boundary, city
  climate offsets, bracket rules, and price/quantity units.
- `timing_research/reader.py`: immutable spool reader and quality audit.
- `timing_research/experiment.py`: availability-safe intentions, control matching,
  separate outcome attachment, conservative cost proxies, uncertainty and gates.
- `experiments/timing_forward_v1/`: prospective protocol and byte-level code lock.
- `data/`: ignored immutable source archives, copied from the collector; no secrets.
- `reports/`: separate audit, decision and outcome artifacts; outputs are never
  overwritten by the CLI.
- `tests/`: synthetic contracts, failure paths, leakage invariance and CLI tests.

The unfinished reader/protocol and previously downloaded timing archive were moved
here from `next-gen`; earlier research and production files were not moved.

## Commands

Run from this directory. Nothing needs installing into the production environment.

```powershell
python -m unittest discover -s tests -v
python -m timing_research --help
```

Data input is the collector's immutable `*.json.gz` spool format, recursively read
from a specified directory. Copy only the timing archive into a NEW snapshot
directory. Do not copy `.env`, credentials, live strategy, or the entire droplet.
Example for an operator-authorized refresh:

```powershell
New-Item -ItemType Directory data/capture_refresh
scp -r -o BatchMode=yes root@134.122.9.101:/opt/kalshi-weather-next-gen/collector_spool_v3/timing/archive data/capture_refresh/
```

Audit explicit bounds, including missing slots at both edges. `--as-of` means the
latest allowed capture completion time, not a substitute for downloading data.
Choose the actual fully elapsed interval; do not count a still-running final slot.

```powershell
python -m timing_research audit --data data/timing_capture_20260914 --start 2026-09-14T02:20:30Z --end 2026-09-14T03:00:00Z --as-of 2026-09-14T03:00:00Z --output reports/initial_audit
```

`AUDIT.json` has cadence, completeness, timestamps, source age, missing sizes,
provider failures, duplicates, and integrity issues. `INPUTS.json` hashes all input
files and `OBSERVATIONS.json` preserves the normalized research observations.
An archive does not prove Supabase sync; the collector's separate deployment
verifier checks that. A first-24-hour audit must wait until that interval elapses.

The v1 freeze is performed once, before September 15 00:00 UTC:

```powershell
python -m timing_research freeze --experiment experiments/timing_forward_v1
```

The saved artifact is a local tamper-evident lock, not an external registration
or security boundary. Rerunning freeze refuses to overwrite it. Changing source,
configuration, protocol notes, or Python major/minor invalidates the lock. Do not
format or edit frozen source; fixes require a new future experiment and documented
deviation, not a rewritten past protocol.

Create a separate sealed decision artifact with each research replay. This command
does not attach future trade outcomes and accepts no settlement labels. It is not
a live-intention daemon. Prefix decisions are tested for invariance under future
quotes, and scoring independently reproduces the sealed as-of replay.

```powershell
python -m timing_research decide --experiment experiments/timing_forward_v1 --data data/capture_refresh --as-of 2026-09-29T01:10:00Z --output reports/decisions_complete
```

After October 2 00:00 UTC, score the full window. No early performance-scoring
override is provided. Download captures through the fixed scoring deadline first.

```powershell
python -m timing_research score --experiment experiments/timing_forward_v1 --data data/capture_refresh --decisions reports/decisions_complete --settlements data/settlements.json.gz --output reports/evaluation
```

Settlement input is an optional JSON list (plain or gzip) of collector settlement
rows: `event_ticker`, `winner_ticker`, `settled_at_utc`, `validation_status: valid`,
`source_provider: kalshi`, and `raw_payload_id`. Obtain it via an authorized export
of verified Kalshi settlements, not a weather-observation proxy. Omit the option to
leave settlement outcomes unresolved. Capture files used by the sealed decisions
must remain available unchanged when scoring.

Outputs: `OUTCOMES.json`, `SUMMARY.json`, `QUALITY.json`, `PROVENANCE.json`, and a
14-day `REPORT.md`. Primary statistical, depth, coverage and concentration checks
must pass together before even suggesting additional paper research.

## Current Limits

The initial sample has no displayed order sizes. Missing size is never invented:
price movement can be studied, but economic scoring is blocked for those quotes.
The first sample is evening data and has no remaining current-day daytime NWS
forecast; that is different from a failed collection. Five-minute sampling cannot
establish instantaneous execution, a trading edge, or an 80% minimum hit rate.

This package does not schedule future audits or auto-fetch data. The production
collector continues gathering; research refresh/audit/decision/score commands are
explicit operator actions. See the frozen protocol for all thresholds and limits.

## September 18 Model Build

Two isolated settlement models are under `reliable_model_v1/` and
`predictive_model_v2/`. Their requirements were written before their respective
evaluations, and both preserve failed results rather than relaxing gates afterward.

Run the current causal predictive replay against the immutable hourly export:

```powershell
python -m predictive_model_v2 --data-dir data/hourly_snapshot_20260918/model_20260918 --output-dir predictive_model_v2/results
```

The output includes the complete selection grid, selected serialized model, trades,
input hashes, software versions, frozen checks, and report. As of September 18, v1
has no executable qualifying barrier trades and v2 fails its 14-day economic and
reliability requirements. Neither result authorizes paper or live deployment.
